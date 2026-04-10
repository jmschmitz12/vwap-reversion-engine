"""
Order execution and account management via the Alpaca Trading API.

Uses a BRACKET order that atomically creates:
  - Market buy (entry)
  - Limit sell (take-profit) as child order
  - Stop sell (stop-loss) as child order

Alpaca manages the OCO relationship between TP and SL automatically.
Exits are calculated from the signal price. Fill-price drift is
logged for monitoring but does not affect exit placement.
"""

import time

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, OrderStatus, TimeInForce
from alpaca.trading.requests import (
    MarketOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

from config.settings import (
    API_KEY,
    MAX_OPEN_POSITIONS,
    PAPER_TRADING,
    SECRET_KEY,
    SL_ATR_MULTIPLIER,
    SL_PERCENT,
    TP_ATR_MULTIPLIER,
    TP_PERCENT,
    USE_ATR_EXITS,
)
from utils.journal import record_trade
from utils.logger import logger

trading_client = TradingClient(API_KEY, SECRET_KEY, paper=PAPER_TRADING)


def get_buying_power(minimum_required: float = 500.0) -> float | None:
    try:
        account = trading_client.get_account()
        buying_power = float(account.buying_power)
        if buying_power < minimum_required:
            logger.warning("Insufficient buying power: $%.2f (min: $%.2f)", buying_power, minimum_required)
            return None
        return buying_power
    except Exception as exc:
        logger.error("Failed to fetch account details: %s", exc)
        return None


def get_open_position_symbols() -> set[str]:
    try:
        positions = trading_client.get_all_positions()
        return {p.symbol for p in positions}
    except Exception as exc:
        logger.error("Failed to fetch open positions: %s", exc)
        return set()


def has_capacity_for_new_position() -> bool:
    open_count = len(get_open_position_symbols())
    if open_count >= MAX_OPEN_POSITIONS:
        logger.info("At position cap (%d/%d) -- no new entries allowed.", open_count, MAX_OPEN_POSITIONS)
        return False
    return True


def submit_entry_with_exits(
    symbol: str,
    qty: int,
    signal_price: float,
    atr: float = 0.0,
) -> object | None:
    """Submit a bracket order: market buy + TP + SL in one atomic request.

    Exits are calculated from signal_price. After fill, the actual
    fill price and drift are logged for monitoring.
    """
    # ── Calculate exits from signal price ────────────────────────────
    if USE_ATR_EXITS and atr > 0:
        tp_price = round(signal_price + (atr * TP_ATR_MULTIPLIER), 2)
        sl_price = round(signal_price - (atr * SL_ATR_MULTIPLIER), 2)
        exit_mode = "ATR"
    else:
        tp_price = round(signal_price * (1 + TP_PERCENT), 2)
        sl_price = round(signal_price * (1 - SL_PERCENT), 2)
        exit_mode = "FIXED"

    # ── Submit bracket order ─────────────────────────────────────────
    try:
        order = trading_client.submit_order(order_data=MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=tp_price),
            stop_loss=StopLossRequest(stop_price=sl_price),
        ))
        logger.info(
            "BRACKET ORDER submitted for %d x %s @ ~$%.2f | TP: $%.2f | SL: $%.2f | %s",
            qty, symbol, signal_price, tp_price, sl_price, exit_mode,
        )
    except Exception as exc:
        logger.error("Bracket order FAILED for %s: %s", symbol, exc)
        return None

    # ── Journal immediately ──────────────────────────────────────────
    record_trade(
        symbol=symbol,
        side="BUY",
        qty=qty,
        entry_price=signal_price,
        take_profit=tp_price,
        stop_loss=sl_price,
        order_id=str(order.id),
    )

    # ── Log fill price and drift (non-blocking) ──────────────────────
    try:
        for _ in range(20):
            check = trading_client.get_order_by_id(str(order.id))
            if check.status == OrderStatus.FILLED:
                fill_price = float(check.filled_avg_price)
                drift = fill_price - signal_price
                logger.info(
                    "FILL CONFIRMED %s @ $%.2f (signal: $%.2f, drift: $%+.2f)",
                    symbol, fill_price, signal_price, drift,
                )
                break
            if check.status in (OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.REJECTED):
                logger.warning("Order %s ended with status: %s", order.id, check.status)
                break
            time.sleep(0.5)
    except Exception as exc:
        logger.warning("Could not confirm fill for %s: %s", symbol, exc)

    return order
