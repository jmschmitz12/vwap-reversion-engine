"""
Order execution and account management via the Alpaca Trading API.

Uses a LIMIT BRACKET order that atomically creates:
  - Limit buy at (signal_price + $0.05) as entry
  - Limit sell (take-profit) as child order
  - Stop sell (stop-loss) as child order

This solves the core problem with market brackets: when a mean-reversion
signal fires, the bounce often starts within 1-2 seconds.  A market
order chases the bounce and Alpaca rejects the bracket because the
new market price is above the TP or below the SL.  A limit order says
"only fill if price is still in the oversold zone" — preserving the
strategy's edge and eliminating bracket rejections.

Unfilled limit orders are cancelled at the start of each new cycle
(handled in bot.py) so stale signals don't stack up.

History of exit approaches:
  - Market + OCO:       Failed — SDK validation errors
  - Market + 2 sells:   Failed — Alpaca locks shares on first sell
  - Market + BRACKET:   Worked but rejected ~60% of entries due to drift
  - Limit + BRACKET:    Current — limit price gates entry at oversold zone
"""

import time

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, OrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    StopLossRequest,
    TakeProfitRequest,
)

from config.settings import (
    API_KEY,
    LIMIT_BUFFER_DOLLARS,
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
                buying_power, minimum_required,
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
            open_count, MAX_OPEN_POSITIONS,
        )
        return False
    return True


# ── Stale Order Cleanup ──────────────────────────────────────────────────────


def cancel_stale_limit_orders() -> int:
    """Cancel all open (unfilled) buy limit orders from prior cycles.

    Called at the start of each cycle to ensure no stale signals linger.
    Does NOT cancel child exit orders (TP/SL) of filled positions — those
    are identified as having already had a fill.

    Returns:
        Number of orders cancelled.
    """
    cancelled = 0
    try:
        # Get all open orders
        open_orders = trading_client.get_orders(filter=GetOrdersRequest(status="open"))

        for order in open_orders:
            # Only cancel unfilled buy limits (entry orders that never triggered)
            # Child orders of filled brackets are sell orders, so they're safe
            if order.side == OrderSide.BUY and str(order.order_type).endswith("limit"):
                if float(order.filled_qty or 0) == 0:
                    try:
                        trading_client.cancel_order_by_id(str(order.id))
                        logger.info(
                            "Cancelled stale limit buy: %s (qty %s @ $%s, age: %s)",
                            order.symbol, order.qty, order.limit_price,
                            order.submitted_at,
                        )
                        cancelled += 1
                    except Exception as exc:
                        logger.warning("Could not cancel order %s: %s", order.id, exc)
    except Exception as exc:
        logger.warning("Could not fetch open orders for cleanup: %s", exc)

    return cancelled


# ── Order Submission ─────────────────────────────────────────────────────────


def submit_entry_with_exits(
    symbol: str,
    qty: int,
    signal_price: float,
    atr: float = 0.0,
) -> object | None:
    """Submit a LIMIT BRACKET: limit buy + TP + SL in one atomic request.

    The limit buy is set at signal_price + LIMIT_BUFFER_DOLLARS, so we
    only fill if the price is still near the oversold signal zone.  If
    the price has already bounced, the order sits unfilled until cancelled
    at the start of the next cycle.

    Args:
        symbol:       Ticker to trade.
        qty:          Number of whole shares.
        signal_price: Price at signal time.
        atr:          Current ATR value for calculating exit distances.

    Returns:
        The entry order object on success, or ``None`` on failure.
    """
    # ── Calculate entry limit and exits ──────────────────────────────
    limit_price = round(signal_price + LIMIT_BUFFER_DOLLARS, 2)

    if USE_ATR_EXITS and atr > 0:
        tp_price = round(limit_price + (atr * TP_ATR_MULTIPLIER), 2)
        sl_price = round(limit_price - (atr * SL_ATR_MULTIPLIER), 2)
        exit_mode = "ATR"
    else:
        tp_price = round(limit_price * (1 + TP_PERCENT), 2)
        sl_price = round(limit_price * (1 - SL_PERCENT), 2)
        exit_mode = "FIXED"

    # ── Sanity check: TP must be above limit by at least $0.01 ───────
    if tp_price <= limit_price:
        logger.warning(
            "[%s] TP $%.2f not above limit $%.2f (ATR too small). Skipping.",
            symbol, tp_price, limit_price,
        )
        return None

    # ── Sanity check: SL must be below limit by at least $0.01 ───────
    if sl_price >= limit_price:
        logger.warning(
            "[%s] SL $%.2f not below limit $%.2f. Skipping.",
            symbol, sl_price, limit_price,
        )
        return None

    # ── Submit limit bracket order ───────────────────────────────────
    try:
        order = trading_client.submit_order(order_data=LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            limit_price=limit_price,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=tp_price),
            stop_loss=StopLossRequest(stop_price=sl_price),
        ))
        logger.info(
            "LIMIT BRACKET submitted: %d x %s @ limit $%.2f (signal: $%.2f) | TP: $%.2f | SL: $%.2f | %s",
            qty, symbol, limit_price, signal_price, tp_price, sl_price, exit_mode,
        )
    except Exception as exc:
        logger.error("Limit bracket FAILED for %s: %s", symbol, exc)
        return None

    # ── Journal immediately ──────────────────────────────────────────
    record_trade(
        symbol=symbol,
        side="BUY",
        qty=qty,
        entry_price=limit_price,
        take_profit=tp_price,
        stop_loss=sl_price,
        order_id=str(order.id),
    )

    # ── Quick fill check (non-blocking) ──────────────────────────────
    # Many limit orders fill within 1-2 seconds when signal is accurate.
    # Longer waits are pointless here — the order stays live for 5 min
    # (until next cycle cleanup), so we just note the immediate status.
    try:
        time.sleep(2)
        check = trading_client.get_order_by_id(str(order.id))
        if check.status == OrderStatus.FILLED:
            fill_price = float(check.filled_avg_price)
            logger.info(
                "LIMIT FILLED %s @ $%.2f (limit was $%.2f, signal was $%.2f)",
                symbol, fill_price, limit_price, signal_price,
            )
        else:
            logger.info(
                "LIMIT PENDING for %s @ $%.2f (status: %s) — will cancel next cycle if not filled",
                symbol, limit_price, check.status,
            )
    except Exception as exc:
        logger.warning("Could not check fill status for %s: %s", symbol, exc)

    return order
