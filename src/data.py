"""
Market data retrieval via the Alpaca Historical Data API.

Fetches multi-symbol intraday bars and returns a MultiIndex DataFrame
indexed by (symbol, timestamp) — the standard Alpaca SDK format that
the rest of the pipeline expects.
"""

from datetime import datetime, timedelta

import pandas as pd
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest

from config.settings import API_KEY, LOOKBACK_DAYS, SECRET_KEY, TARGET_SYMBOLS, TIMEFRAME
from utils.logger import logger

# Initialized once at module load; reused across every cycle.
_data_client = StockHistoricalDataClient(API_KEY, SECRET_KEY)


def fetch_intraday_data(
    lookback_days: int = LOOKBACK_DAYS,
    symbols: list[str] | None = None,
) -> pd.DataFrame | None:
    """Fetch recent 5-minute bars for the given symbols.

    Args:
        lookback_days: Number of calendar days of history to request.
            Defaults to 2 so the first trading day always has enough
            prior candles for a 14-period RSI at market open.
        symbols: Tickers to fetch.  Falls back to ``TARGET_SYMBOLS``
            if not provided.

    Returns:
        A MultiIndex DataFrame (symbol × timestamp) on success, or
        ``None`` if the request fails.
    """
    active_symbols = symbols or TARGET_SYMBOLS

    logger.info(
        "Fetching %d-%s bars for %s ...",
        TIMEFRAME.amount,
        TIMEFRAME.unit.value,
        active_symbols,
    )

    end_time = datetime.now()
    start_time = end_time - timedelta(days=lookback_days)

    request_params = StockBarsRequest(
        symbol_or_symbols=active_symbols,
        timeframe=TIMEFRAME,
        start=start_time,
        end=end_time,
    )

    try:
        bars = _data_client.get_stock_bars(request_params)
        df: pd.DataFrame = bars.df
        logger.info("Market data retrieved — shape: %s", df.shape)
        return df
    except Exception as exc:
        logger.error("Failed to fetch market data: %s", exc)
        return None
