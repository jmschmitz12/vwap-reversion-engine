"""
Order execution and account management via the Alpaca Trading API.

Two-step order flow to ensure exits are calculated from the ACTUAL
fill price rather than the stale candle close:

    1. Submit a market buy order.
    2. Poll for fill confirmation and read the fill price.
    3. Submit an OTO (One-Triggers-Other) exit order — a limit sell
       at take-profit AND a stop sell at stop-loss — calculated from
       the real fill price.

This eliminates the fill-price drift bug where bracket exits were
misaligned because the market moved between signal and fill.
"""

import time

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, OrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
    StopLossRequest,
    StopOrderRequest,
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

# Initialized once at module load; reused across every cycle.
trading_client = TradingClient(API_KEY, SECRET_KEY, paper=PAPER_TRADING)


# ── Account Helpers ──────────────────────────────────────────────────────────


def get_buying_power(minimum_required: float = 500.0) -> float | None:
    """Return available buying power if it exceeds *minimum_required*."""
    try:
        account = trading_client.get_account()
        buying_power = float(account.buying_power)

        if buying_power < minimum_required:
            logger.warning(
                "Insufficient buying power: $%.2f (minimum: $%.2f)",
                buying_power,
                minimum_required,
            )
            return None

        return buying_power

    except Exception as exc:
        logger.error("Failed to fetch account details: %s", exc)
        return None


def get_open_position_symbols() -> set[str]:
    """Return the set of ticker symbols with currently open positions."""
    try:
        positions = trading_client.get_all_positions()
        return {p.symbol for p in positions}
    except Exception as exc:
        logger.error("Failed to fetch open positions: %s", exc)
        return set()


def has_capacity_for_new_position() -> bool:
    """Check whether the bot is below its maximum position count."""
    open_count = len(get_open_position_symbols())
    if open_count >= MAX_OPEN_POSITIONS:
        logger.info(
            "At position cap (%d/%d) -- no new entries allowed.",
            open_count,
            MAX_OPEN_POSITIONS,
        )
        return False
    return True


# ── Fill Price Retrieval ─────────────────────────────────────────────────────


def _wait_for_fill(order_id: str, max_wait: int = 10) -> float | None:
    """Poll Alpaca until the order fills or timeout.

    Args:
        order_id: The Alpaca order ID to check.
        max_wait: Maximum seconds to wait for fill.

    Returns:
        The average fill price, or ``None`` if the order didn't fill.
    """
    for _ in range(max_wait * 2):  # Check every 0.5s
        try:
            order = trading_client.get_order_by_id(order_id)
            if order.status == OrderStatus.FILLED:
                return float(order.filled_avg_price)
            if order.status in (OrderStatus.CANCELED, OrderStatus.EXPIRED, OrderStatus.REJECTED):
                logger.warning("Order %s ended with status: %s", order_id, order.status)
                return None
        except Exception as exc:
            logger.warning("Error polling order %s: %s", order_id, exc)
        time.sleep(0.5)

    logger.warning("Order %s did not fill within %ds", order_id, max_wait)
    return None


# ── Order Submission ─────────────────────────────────────────────────────────


def submit_entry_with_exits(
    symbol: str,
    qty: int,
    signal_price: float,
    atr: float = 0.0,
) -> object | None:
    """Submit a market buy, then set exits based on the actual fill price.

    This two-step flow ensures TP and SL are correctly distanced from
    where you actually entered, not where the signal fired.

    Args:
        symbol:       Ticker to trade.
        qty:          Number of whole shares.
        signal_price: Price at signal time (logged for reference only).
        atr:          Current ATR value for calculating exit distances.

    Returns:
        The entry order object on success, or ``None`` on failure.
    """
    try:
        # ── Step 1: Market buy ───────────────────────────────────────
        entry_order = MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        )

        order = trading_client.submit_order(order_data=entry_order)
        logger.info("Market buy submitted for %d x %s (order: %s)", qty, symbol, order.id)

        # ── Step 2: Wait for fill ────────────────────────────────────
        fill_price = _wait_for_fill(str(order.id))

        if fill_price is None:
            logger.error("Entry order for %s did not fill. Attempting cancel.", symbol)
            try:
                trading_client.cancel_order_by_id(str(order.id))
            except Exception:
                pass
            return None

        # ── Step 3: Calculate exits from FILL price ──────────────────
        if USE_ATR_EXITS and atr > 0:
            tp_price = round(fill_price + (atr * TP_ATR_MULTIPLIER), 2)
            sl_price = round(fill_price - (atr * SL_ATR_MULTIPLIER), 2)
            exit_mode = "ATR"
        else:
            tp_price = round(fill_price * (1 + TP_PERCENT), 2)
            sl_price = round(fill_price * (1 - SL_PERCENT), 2)
            exit_mode = "FIXED"

        logger.info(
            "FILLED %d x %s @ $%.2f (signal: $%.2f, drift: $%.2f) | TP: $%.2f | SL: $%.2f | %s",
            qty,
            symbol,
            fill_price,
            signal_price,
            fill_price - signal_price,
            tp_price,
            sl_price,
            exit_mode,
        )

        # ── Step 4: Submit bracket exit (OCO: TP + SL) ───────────────
        exit_order = LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.OCO,
            take_profit=TakeProfitRequest(limit_price=tp_price),
            stop_loss=StopLossRequest(stop_price=sl_price),
        )

        exit_result = trading_client.submit_order(order_data=exit_order)
        logger.info("Exit orders placed for %s (OCO: %s)", symbol, exit_result.id)

        record_trade(
            symbol=symbol,
            side="BUY",
            qty=qty,
            entry_price=fill_price,
            take_profit=tp_price,
            stop_loss=sl_price,
            order_id=str(order.id),
        )

        return order

    except Exception as exc:
        logger.error("Entry failed for %s: %s", symbol, exc)
        return None
