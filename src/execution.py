"""
Order execution and account management via the Alpaca Trading API.

Uses a two-stage flow:

  1. Submit a limit BUY order at signal + LIMIT_BUFFER_DOLLARS.
  2. Poll for fill (up to ~15 seconds).
  3. Once filled, submit an OCO (one-cancels-other) sell that combines
     the take-profit and stop-loss, calculated from the *actual* fill
     price rather than the limit price.

Why split entry from exits?

  An earlier version used Alpaca's BRACKET order class, which submits
  the limit buy and its child TP/SL in one atomic request.  The TP/SL
  prices had to be computed in advance — from the limit price — before
  the fill was known.  When a fill came in below the limit (which can
  happen on paper trading, gap fills, or thin-liquidity conditions),
  the stop-loss was already above the fill price and triggered
  immediately, churning through positions for tiny losses.

  By waiting for the fill first and then calculating exits from the
  actual fill price, we guarantee the stop is always a full SL_ATR
  distance below the entry — regardless of fill mechanics.

Sanity check on fills

  On liquid mega-caps, a limit buy should fill at or just below the
  limit price.  If the fill is dramatically below (more than 2 × ATR),
  something is wrong — paper-trading simulation artifact, stale quote,
  halted security, etc.  In that case we immediately close the
  position at market rather than submit exits at nonsensical levels.

Unfilled limit orders are cancelled at the start of each new cycle
(handled in bot.py) so stale signals don't stack up.

History of exit approaches:
  - Market + OCO:             Failed — SDK validation errors
  - Market + 2 separate sells: Failed — Alpaca locks shares on first sell
  - Market + BRACKET:         Worked but rejected ~60% of entries due to drift
  - Limit + BRACKET:          Fills below limit fired SL instantly on paper
  - Limit + poll + OCO:       Current — exits calculated from actual fill
"""

import time

from alpaca.trading.client import TradingClient
from alpaca.trading.enums import OrderClass, OrderSide, OrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetOrdersRequest,
    LimitOrderRequest,
    MarketOrderRequest,
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
    TP_ATR_MULTIPLIER,
)
from utils.journal import record_trade
from utils.logger import logger

# Initialized once at module load; reused across every cycle.
trading_client = TradingClient(API_KEY, SECRET_KEY, paper=PAPER_TRADING)

# ── Fill Polling Configuration ───────────────────────────────────────────────

_FILL_POLL_MAX_SECONDS = 15
_FILL_POLL_INTERVAL_SECONDS = 1.0

# If the actual fill price is more than this many ATRs below the limit,
# something is wrong — abort with a flatten rather than submit exits.
_FILL_SANITY_ATR_THRESHOLD = 2.0


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
    are sell orders and so excluded by the buy-only filter.

    Returns:
        Number of orders cancelled.
    """
    cancelled = 0
    try:
        open_orders = trading_client.get_orders(filter=GetOrdersRequest(status="open"))

        for order in open_orders:
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


# ── Internal Helpers ─────────────────────────────────────────────────────────


def _poll_for_fill(order_id: str, symbol: str) -> float | None:
    """Poll an order's status until it fills or the timeout expires.

    Returns:
        The filled average price if the order filled within the window,
        or ``None`` if it's still pending, cancelled, or failed.
    """
    deadline = time.time() + _FILL_POLL_MAX_SECONDS
    last_status = None

    while time.time() < deadline:
        try:
            check = trading_client.get_order_by_id(order_id)
        except Exception as exc:
            logger.warning("[%s] Could not poll order status: %s", symbol, exc)
            time.sleep(_FILL_POLL_INTERVAL_SECONDS)
            continue

        last_status = check.status

        if check.status == OrderStatus.FILLED:
            return float(check.filled_avg_price)

        # Terminal non-fill states — give up early
        if check.status in (
            OrderStatus.CANCELED,
            OrderStatus.EXPIRED,
            OrderStatus.REJECTED,
        ):
            logger.info(
                "[%s] Limit buy reached terminal state: %s",
                symbol, check.status,
            )
            return None

        time.sleep(_FILL_POLL_INTERVAL_SECONDS)

    logger.info(
        "[%s] Limit buy still pending after %ds (status: %s) — will cancel next cycle",
        symbol, _FILL_POLL_MAX_SECONDS, last_status,
    )
    return None


def _flatten_at_market(symbol: str, qty: int, reason: str) -> None:
    """Immediately close a position via market sell.

    Used when a fill comes in at a price so anomalous that we cannot
    safely set stops around it.  Logs loudly so the operator can
    investigate.
    """
    logger.error(
        "[%s] FLATTENING %d shares at market. Reason: %s",
        symbol, qty, reason,
    )
    try:
        trading_client.submit_order(order_data=MarketOrderRequest(
            symbol=symbol,
            qty=qty,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.DAY,
        ))
    except Exception as exc:
        logger.error(
            "[%s] Market flatten FAILED after anomalous fill. "
            "POSITION IS NAKED — MANUAL INTERVENTION REQUIRED: %s",
            symbol, exc,
        )


def _submit_oco_exits(
    symbol: str,
    qty: int,
    fill_price: float,
    atr: float,
) -> bool:
    """Submit a one-cancels-other (OCO) sell combining TP and SL.

    Calculated from the actual fill price, so the stop is always a
    full SL_ATR × ATR distance below the real entry.

    Returns:
        True on successful submission, False otherwise.
    """
    tp_price = round(fill_price + (atr * TP_ATR_MULTIPLIER), 2)
    sl_price = round(fill_price - (atr * SL_ATR_MULTIPLIER), 2)

    if tp_price <= fill_price + 0.01:
        logger.error(
            "[%s] TP $%.2f not meaningfully above fill $%.2f (ATR too small). "
            "FLATTENING.",
            symbol, tp_price, fill_price,
        )
        _flatten_at_market(symbol, qty, "TP calculation invalid")
        return False

    if sl_price >= fill_price - 0.01:
        logger.error(
            "[%s] SL $%.2f not meaningfully below fill $%.2f. FLATTENING.",
            symbol, sl_price, fill_price,
        )
        _flatten_at_market(symbol, qty, "SL calculation invalid")
        return False

    try:
        trading_client.submit_order(order_data=LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            limit_price=tp_price,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            order_class=OrderClass.OCO,
            take_profit=TakeProfitRequest(limit_price=tp_price),
            stop_loss=StopLossRequest(stop_price=sl_price),
        ))
        logger.info(
            "OCO EXITS submitted for %s: qty %d | TP: $%.2f | SL: $%.2f (from fill $%.2f)",
            symbol, qty, tp_price, sl_price, fill_price,
        )
        return True
    except Exception as exc:
        logger.error(
            "[%s] OCO exit submission FAILED: %s. FLATTENING to avoid naked position.",
            symbol, exc,
        )
        _flatten_at_market(symbol, qty, f"OCO submission failed: {exc}")
        return False


# ── Order Submission ─────────────────────────────────────────────────────────


def submit_entry_with_exits(
    symbol: str,
    qty: int,
    signal_price: float,
    atr: float = 0.0,
) -> object | None:
    """Submit a limit BUY, wait for fill, then submit OCO exits.

    Flow:
      1. Submit limit buy at signal + LIMIT_BUFFER_DOLLARS.
      2. Poll for fill up to _FILL_POLL_MAX_SECONDS.
      3. If filled:
         a. Sanity-check fill price against signal (guard against
            bad quotes / simulation artifacts).
         b. If reasonable: submit OCO (TP + SL) based on actual fill.
         c. If not: flatten at market immediately.
      4. If not filled in time: return None.  The stale limit will
         be cancelled at the start of the next cycle.

    Args:
        symbol:       Ticker to trade.
        qty:          Number of whole shares.
        signal_price: Price at signal time.
        atr:          Current ATR value for exit distances.

    Returns:
        The entry order object on success (filled AND exits submitted),
        or ``None`` on failure or pending status.
    """
    if atr <= 0:
        logger.warning("[%s] ATR unavailable -- skipping entry.", symbol)
        return None

    limit_price = round(signal_price + LIMIT_BUFFER_DOLLARS, 2)

    # ── Step 1: Submit limit buy (no bracket, no children) ──────────
    try:
        order = trading_client.submit_order(order_data=LimitOrderRequest(
            symbol=symbol,
            qty=qty,
            limit_price=limit_price,
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
        ))
        logger.info(
            "LIMIT BUY submitted: %d x %s @ limit $%.2f (signal: $%.2f, ATR: $%.2f)",
            qty, symbol, limit_price, signal_price, atr,
        )
    except Exception as exc:
        logger.error("[%s] Limit buy submission FAILED: %s", symbol, exc)
        return None

    # ── Step 2: Poll for fill ───────────────────────────────────────
    fill_price = _poll_for_fill(str(order.id), symbol)
    if fill_price is None:
        return None

    logger.info(
        "LIMIT FILLED %s @ $%.2f (limit was $%.2f, signal was $%.2f, delta from signal: $%+.2f)",
        symbol, fill_price, limit_price, signal_price, fill_price - signal_price,
    )

    # ── Step 3a: Sanity check the fill ──────────────────────────────
    # A real limit buy fills at or very close to the limit price on
    # liquid mega-caps.  Anything more than _FILL_SANITY_ATR_THRESHOLD
    # below the limit is a red flag.
    max_reasonable_discount = atr * _FILL_SANITY_ATR_THRESHOLD
    fill_discount = limit_price - fill_price
    if fill_discount > max_reasonable_discount:
        logger.error(
            "[%s] Anomalous fill: got $%.2f on limit $%.2f "
            "(discount $%.2f > %.1f × ATR = $%.2f). Flattening immediately.",
            symbol, fill_price, limit_price, fill_discount,
            _FILL_SANITY_ATR_THRESHOLD, max_reasonable_discount,
        )
        _flatten_at_market(symbol, qty, f"anomalous fill discount ${fill_discount:.2f}")
        return None

    # ── Step 3b: Submit OCO exits from actual fill price ────────────
    if not _submit_oco_exits(symbol, qty, fill_price, atr):
        return None

    # ── Step 4: Journal the completed trade ─────────────────────────
    tp_price = round(fill_price + (atr * TP_ATR_MULTIPLIER), 2)
    sl_price = round(fill_price - (atr * SL_ATR_MULTIPLIER), 2)
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
