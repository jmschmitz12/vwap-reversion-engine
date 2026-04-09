"""
Technical indicator calculations.

Takes the raw Alpaca MultiIndex DataFrame and appends per-symbol
columns for every indicator the strategy consumes.  New indicators
should be added here — never inline in the signal logic.

Current indicators:
    - RSI(14)    — momentum oscillator used for oversold detection
    - VWAP       — daily volume-weighted average price for dip confirmation
    - EMA(200)   — long-term trend filter (disabled but calculated)
    - ATR(14)    — volatility measure for adaptive exits
    - vol_avg_20 — 20-bar rolling average volume
"""

import pandas as pd
import pandas_ta as ta  # noqa: F401 — used via DataFrame.ta accessor

from utils.logger import logger

# Column names emitted by pandas_ta for each indicator.
_RSI_COL = "RSI_14"
_VWAP_COL = "VWAP_D"
_EMA_COL = "EMA_200"
_ATR_COL = "ATRr_14"


def apply_indicators(df: pd.DataFrame) -> pd.DataFrame | None:
    """Calculate all technical indicators for every symbol in *df*.

    The function operates per-symbol to prevent look-ahead bias across
    tickers.  Results are written back into the master DataFrame under
    lowercase column names.

    Defensive checks ensure that missing indicator columns (e.g. EMA_200
    when there aren't enough bars) don't crash the entire cycle — the
    column is left as NaN and the bot continues without it.

    Args:
        df: MultiIndex DataFrame (symbol × timestamp) returned by
            :func:`src.data.fetch_intraday_data`.

    Returns:
        The enriched DataFrame, or ``None`` if the input is empty or
        calculation fails catastrophically.
    """
    if df is None or df.empty:
        logger.warning("Empty DataFrame received — skipping indicator calculation.")
        return None

    try:
        df = df.copy()
        df[["rsi", "vwap", "ema_200", "atr", "vol_avg_20"]] = float("nan")

        symbols: list[str] = df.index.get_level_values("symbol").unique().tolist()

        for symbol in symbols:
            sdf = df.loc[symbol].copy()

            # Calculate all indicators — each in its own block so one
            # failure doesn't prevent the others from computing.
            try:
                sdf.ta.rsi(length=14, append=True)
            except Exception as exc:
                logger.warning("[%s] RSI calculation failed: %s", symbol, exc)

            try:
                sdf.ta.vwap(append=True)
            except Exception as exc:
                logger.warning("[%s] VWAP calculation failed: %s", symbol, exc)

            try:
                sdf.ta.ema(length=200, append=True)
            except Exception as exc:
                logger.warning("[%s] EMA-200 calculation failed: %s", symbol, exc)

            try:
                sdf.ta.atr(length=14, append=True)
            except Exception as exc:
                logger.warning("[%s] ATR calculation failed: %s", symbol, exc)

            # Rolling 20-bar volume average
            try:
                sdf["vol_avg_20"] = sdf["volume"].rolling(window=20, min_periods=5).mean()
            except Exception as exc:
                logger.warning("[%s] Volume avg calculation failed: %s", symbol, exc)

            # Map results back — only if the column exists in sdf
            if _RSI_COL in sdf.columns:
                df.loc[(symbol, slice(None)), "rsi"] = sdf[_RSI_COL].values
            if _VWAP_COL in sdf.columns:
                df.loc[(symbol, slice(None)), "vwap"] = sdf[_VWAP_COL].values
            if _EMA_COL in sdf.columns:
                df.loc[(symbol, slice(None)), "ema_200"] = sdf[_EMA_COL].values
            if _ATR_COL in sdf.columns:
                df.loc[(symbol, slice(None)), "atr"] = sdf[_ATR_COL].values
            if "vol_avg_20" in sdf.columns:
                df.loc[(symbol, slice(None)), "vol_avg_20"] = sdf["vol_avg_20"].values

        logger.info("Indicators calculated: RSI, VWAP, EMA-200, ATR, VolAvg.")
        return df

    except Exception as exc:
        logger.error("Indicator calculation failed: %s", exc)
        return None
