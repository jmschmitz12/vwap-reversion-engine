"""
Technical indicator calculations.

Takes the raw Alpaca MultiIndex DataFrame and appends per-symbol
columns for every indicator the strategy consumes.  New indicators
should be added here — never inline in the signal logic.

Current indicators:
    - RSI(14)    — momentum oscillator used for oversold detection
    - VWAP       — daily volume-weighted average price for dip confirmation
    - EMA(200)   — long-term trend filter
    - ATR(14)    — volatility measure for adaptive exits
    - vol_avg_20 — 20-bar rolling average volume for entry confirmation
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

    Args:
        df: MultiIndex DataFrame (symbol × timestamp) returned by
            :func:`src.data.fetch_intraday_data`.

    Returns:
        The enriched DataFrame, or ``None`` if the input is empty or
        calculation fails.
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

            sdf.ta.rsi(length=14, append=True)
            sdf.ta.vwap(append=True)
            sdf.ta.ema(length=200, append=True)
            sdf.ta.atr(length=14, append=True)

            # Rolling 20-bar volume average for entry confirmation
            sdf["vol_avg_20"] = sdf["volume"].rolling(window=20, min_periods=5).mean()

            df.loc[(symbol, slice(None)), "rsi"] = sdf[_RSI_COL].values
            df.loc[(symbol, slice(None)), "vwap"] = sdf[_VWAP_COL].values
            df.loc[(symbol, slice(None)), "ema_200"] = sdf[_EMA_COL].values
            df.loc[(symbol, slice(None)), "atr"] = sdf[_ATR_COL].values
            df.loc[(symbol, slice(None)), "vol_avg_20"] = sdf["vol_avg_20"].values

        logger.info("Indicators calculated: RSI, VWAP, EMA-200, ATR, VolAvg.")
        return df

    except Exception as exc:
        logger.error("Indicator calculation failed: %s", exc)
        return None
