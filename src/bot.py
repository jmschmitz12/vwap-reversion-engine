"""
Core bot loop — signal detection and trade orchestration.

Each call to :func:`run_bot_iteration` represents one complete
analysis cycle: fetch data -> compute indicators -> scan for signals ->
execute qualifying trades.
"""

import pandas as pd

from config.settings import (
    ALLOCATION_PERCENT,
    MAX_OPEN_POSITIONS,
    RSI_OVERSOLD,
    TARGET_SYMBOLS,
    TRADING_END_HOUR_UTC,
    TRADING_END_MINUTE_UTC,
    TRADING_START_HOUR_UTC,
)
from src.data import fetch_intraday_data
from src.execution import (
    get_buying_power,
    get_open_position_symbols,
    submit_entry_with_exits,
    trading_client,
)
from src.indicators import apply_indicators
from utils.logger import logger


def _calculate_position_size(
    buying_power: float,
    current_price: float,
    allocation_percent: float = ALLOCATION_PERCENT,
) -> int:
    """Determine the number of whole shares to buy."""
    allocated_cash = buying_power * allocation_percent
    return int(allocated_cash // current_price)


def _is_in_trading_window() -> bool:
    """Return ``True`` if the current UTC time falls within the entry window.

    This is a FREE check (no API call) — always run this before
    _is_market_open() to avoid unnecessary Alpaca requests.
    """
    from datetime import datetime, timezone

    now = datetime.now(timezone.utc)
    start = TRADING_START_HOUR_UTC * 60
    end = TRADING_END_HOUR_UTC * 60 + TRADING_END_MINUTE_UTC
    current = now.hour * 60 + now.minute
    return start <= current <= end


def _is_market_open() -> bool:
    """Return ``True`` if the market is currently in a regular session.

    This makes an API call to Alpaca — only call after confirming
    we're inside the trading window.
    """
    try:
        clock = trading_client.get_clock()
        return clock.is_open
    except Exception as exc:
        logger.error("Failed to check market clock: %s", exc)
        return False


def run_bot_iteration(symbols: list[str] | None = None) -> None:
    """Execute a single analysis-and-trade cycle.

    Args:
        symbols: Tickers to scan this cycle. Falls back to
            ``TARGET_SYMBOLS`` if not provided.
    """
    active_symbols = symbols or TARGET_SYMBOLS
    logger.info("-- Starting analysis cycle (%d symbols) --", len(active_symbols))

    # -- Pre-flight checks (ordered cheapest-first) --

    # 1. Trading window (free local check — no API call)
    if not _is_in_trading_window():
        logger.info("Outside trading window (17:00-19:30 UTC) -- skipping entries.")
        return

    # 2. Market open (API call — only reached during the window)
    if not _is_market_open():
        logger.info("Market is closed -- skipping this cycle.")
        return

    # 3. Position capacity (single API call — get current positions)
    open_symbols: set[str] = get_open_position_symbols()
    current_position_count = len(open_symbols)

    if current_position_count >= MAX_OPEN_POSITIONS:
        logger.info(
            "At position cap (%d/%d) -- no new entries allowed.",
            current_position_count,
            MAX_OPEN_POSITIONS,
        )
        return

    # -- Data & Indicators --
    raw_df = fetch_intraday_data(symbols=active_symbols)
    if raw_df is None:
        return

    df = apply_indicators(raw_df)
    if df is None:
        return

    # -- Signal Scan --
    for symbol in active_symbols:
        # Check capacity using local counter (no API call)
        if current_position_count >= MAX_OPEN_POSITIONS:
            logger.info("Position cap reached during scan -- stopping.")
            break

        if symbol in open_symbols:
            logger.info("[%s] Position already open -- skipping.", symbol)
            continue

        try:
            symbol_data = df.xs(symbol, level="symbol")
            latest = symbol_data.iloc[-1]

            current_price: float = latest["close"]
            rsi: float = latest["rsi"]
            vwap: float = latest["vwap"]
            atr: float = latest.get("atr", 0.0)

            if pd.isna(rsi):
                continue

            # Handle NaN ATR
            if pd.isna(atr):
                atr = 0.0

            logger.info(
                "[%s] Price: $%.2f | RSI: %.2f | VWAP: $%.2f | ATR: $%.2f",
                symbol,
                current_price,
                rsi,
                vwap,
                atr,
            )

            # Mean-reversion entry: oversold + trading below VWAP
            if rsi < RSI_OVERSOLD and current_price < vwap:
                logger.info("*** BUY SIGNAL -- %s ***", symbol)

                bp = get_buying_power(minimum_required=current_price)
                if bp is None:
                    continue

                qty = _calculate_position_size(bp, current_price)
                if qty <= 0:
                    logger.warning(
                        "[%s] Allocated cash too low for a single share.", symbol
                    )
                    continue

                result = submit_entry_with_exits(
                    symbol=symbol,
                    qty=qty,
                    signal_price=current_price,
                    atr=atr,
                )

                # Track locally so we don't need another API call
                if result is not None:
                    open_symbols.add(symbol)
                    current_position_count += 1

        except KeyError:
            logger.warning("[%s] No data available -- skipping.", symbol)
        except Exception as exc:
            logger.error("[%s] Unexpected error: %s", symbol, exc)
