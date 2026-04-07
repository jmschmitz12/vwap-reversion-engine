"""
Premarket scanner — identifies high-probability mean-reversion candidates.

Runs each morning before the opening bell.  For every symbol in the
scan universe it evaluates:

    1. Overnight gap (previous close → latest premarket price)
    2. Premarket volume relative to the stock's typical daily volume
    3. Absolute price level (avoids micro-caps where spreads eat profits)

Candidates are ranked by gap magnitude and the top N are returned as
today's dynamic watchlist.

The module also provides a historical variant that replays the scanner
logic across past trading days for backtesting.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta

import pandas as pd

from config.settings import API_KEY, SECRET_KEY, TIMEFRAME
from scanner.universe import SCAN_UNIVERSE
from utils.logger import logger


# ── Configuration ────────────────────────────────────────────────────────────

MIN_GAP_DOWN_PCT: float = -0.03       # Minimum overnight gap (-3%, was -2%)
MAX_GAP_DOWN_PCT: float = -0.08       # Ignore catastrophic gaps (< -8%, was -10%)
MIN_PRICE: float = 50.0               # Floor price — mega-cap only (was $15)
MIN_RELATIVE_VOLUME: float = 0.5      # Premarket vol ≥ 50% of avg daily (was 30%)
MAX_CANDIDATES: int = 5               # Fewer, higher-conviction picks (was 8)


@dataclass
class ScanCandidate:
    """A single scanner result with its scoring criteria."""

    symbol: str
    prev_close: float
    current_price: float
    gap_pct: float
    premarket_volume: int = 0
    avg_daily_volume: int = 0
    relative_volume: float = 0.0

    def __repr__(self) -> str:
        return (
            f"{self.symbol:<6} | Gap: {self.gap_pct:+.2%} | "
            f"Price: ${self.current_price:.2f} | "
            f"RelVol: {self.relative_volume:.1f}x"
        )


# ── Live Scanner ─────────────────────────────────────────────────────────────


def scan_premarket(
    symbols: list[str] | None = None,
    max_candidates: int = MAX_CANDIDATES,
) -> list[ScanCandidate]:
    """Scan for gap-down mean-reversion candidates using live Alpaca data.

    Intended to run once at ~9:15 AM before the opening bell.

    Args:
        symbols:        Universe to scan (defaults to SCAN_UNIVERSE).
        max_candidates: Maximum number of candidates to return.

    Returns:
        A list of :class:`ScanCandidate` objects ranked by gap magnitude,
        best (most negative) first.
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockSnapshotRequest

    symbols = symbols or SCAN_UNIVERSE
    client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

    logger.info("Scanning %d symbols for premarket gap-downs ...", len(symbols))

    try:
        request = StockSnapshotRequest(symbol_or_symbols=symbols)
        snapshots = client.get_stock_snapshot(request)
    except Exception as exc:
        logger.error("Snapshot fetch failed: %s", exc)
        return []

    candidates: list[ScanCandidate] = []

    for symbol, snap in snapshots.items():
        try:
            if snap.previous_daily_bar is None or snap.daily_bar is None:
                continue

            prev_close = snap.previous_daily_bar.close
            current_price = snap.latest_trade.price if snap.latest_trade else snap.daily_bar.close

            if current_price < MIN_PRICE or prev_close <= 0:
                continue

            gap_pct = (current_price - prev_close) / prev_close

            # Only interested in gap-downs within our range
            if gap_pct > MIN_GAP_DOWN_PCT or gap_pct < MAX_GAP_DOWN_PCT:
                continue

            # Volume analysis
            premarket_vol = snap.daily_bar.volume if snap.daily_bar else 0
            avg_daily_vol = snap.previous_daily_bar.volume if snap.previous_daily_bar else 1
            rel_vol = premarket_vol / avg_daily_vol if avg_daily_vol > 0 else 0

            if rel_vol < MIN_RELATIVE_VOLUME:
                continue

            candidates.append(
                ScanCandidate(
                    symbol=symbol,
                    prev_close=prev_close,
                    current_price=current_price,
                    gap_pct=gap_pct,
                    premarket_volume=premarket_vol,
                    avg_daily_volume=avg_daily_vol,
                    relative_volume=rel_vol,
                )
            )
        except Exception as exc:
            logger.debug("Skipping %s: %s", symbol, exc)
            continue

    # Rank by gap magnitude (most negative first)
    candidates.sort(key=lambda c: c.gap_pct)
    selected = candidates[:max_candidates]

    if selected:
        logger.info("Scanner found %d candidates:", len(selected))
        for c in selected:
            logger.info("  %s", c)
    else:
        logger.info("No candidates met scanner criteria today.")

    return selected


# ── Historical Scanner (for backtesting) ─────────────────────────────────────


def scan_historical(
    lookback_days: int = 180,
    symbols: list[str] | None = None,
    max_candidates: int = MAX_CANDIDATES,
) -> dict[str, list[str]]:
    """Replay the scanner logic across historical trading days.

    For each trading day in the lookback window, this function
    identifies which symbols gapped down from the prior day's close
    to the current day's open, applies the same filters as the live
    scanner, and returns a mapping of date → selected symbols.

    This is the backbone of scanner-aware backtesting: instead of
    testing a fixed list, the backtester uses the symbols this
    function says would have been selected on each specific day.

    Args:
        lookback_days:  Calendar days of history to analyze.
        symbols:        Universe to scan (defaults to SCAN_UNIVERSE).
        max_candidates: Max symbols per day.

    Returns:
        A dict mapping date strings (YYYY-MM-DD) to lists of symbols.
    """
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit

    symbols = symbols or SCAN_UNIVERSE
    client = StockHistoricalDataClient(API_KEY, SECRET_KEY)

    logger.info(
        "Building historical scanner picks for %d days across %d symbols ...",
        lookback_days,
        len(symbols),
    )

    end = datetime.now()
    start = end - timedelta(days=lookback_days)

    # Fetch daily bars for the full universe
    request = StockBarsRequest(
        symbol_or_symbols=symbols,
        timeframe=TimeFrame(1, TimeFrameUnit.Day),
        start=start,
        end=end,
    )

    try:
        bars = client.get_stock_bars(request)
        daily_df = bars.df
    except Exception as exc:
        logger.error("Historical daily data fetch failed: %s", exc)
        return {}

    logger.info("Daily data shape: %s", daily_df.shape)

    daily_picks: dict[str, list[str]] = {}

    # Get all unique symbols that actually returned data
    available_symbols = daily_df.index.get_level_values("symbol").unique().tolist()

    for symbol in available_symbols:
        try:
            sdf = daily_df.loc[symbol].copy()
            sdf = sdf.sort_index()

            # Calculate overnight gap: today's open vs yesterday's close
            sdf["prev_close"] = sdf["close"].shift(1)
            sdf["gap_pct"] = (sdf["open"] - sdf["prev_close"]) / sdf["prev_close"]

            # Calculate average volume (trailing 20-day)
            sdf["avg_vol_20"] = sdf["volume"].rolling(window=20, min_periods=5).mean()
            sdf["rel_vol"] = sdf["volume"] / sdf["avg_vol_20"]

            for ts, row in sdf.iterrows():
                if pd.isna(row["gap_pct"]) or pd.isna(row["prev_close"]):
                    continue

                # Apply the same filters as the live scanner
                if row["open"] < MIN_PRICE:
                    continue
                if row["gap_pct"] > MIN_GAP_DOWN_PCT:
                    continue
                if row["gap_pct"] < MAX_GAP_DOWN_PCT:
                    continue
                if not pd.isna(row["rel_vol"]) and row["rel_vol"] < MIN_RELATIVE_VOLUME:
                    continue

                date_key = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]

                if date_key not in daily_picks:
                    daily_picks[date_key] = []

                daily_picks[date_key].append(
                    (symbol, row["gap_pct"])
                )

        except Exception as exc:
            logger.debug("Historical scan skipping %s: %s", symbol, exc)
            continue

    # For each day, sort by gap magnitude and take top N
    result: dict[str, list[str]] = {}
    for date_key, picks in daily_picks.items():
        picks.sort(key=lambda x: x[1])  # Most negative gap first
        result[date_key] = [sym for sym, _ in picks[:max_candidates]]

    total_days_with_picks = len(result)
    avg_picks = (
        sum(len(v) for v in result.values()) / total_days_with_picks
        if total_days_with_picks > 0
        else 0
    )
    logger.info(
        "Historical scanner: %d days with picks, %.1f avg candidates/day",
        total_days_with_picks,
        avg_picks,
    )

    return result
