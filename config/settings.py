"""
Centralized configuration for the VWAP Reversion Engine.

All tunable parameters are defined here so the rest of the codebase
stays free of magic numbers.  API credentials are loaded from a .env
file and never committed to version control.
"""

import os

from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
from dotenv import load_dotenv

load_dotenv()

# ── API Credentials ──────────────────────────────────────────────────────────

API_KEY: str = os.getenv("ALPACA_API_KEY", "")
SECRET_KEY: str = os.getenv("ALPACA_SECRET_KEY", "")

# ── Ticker Universe ──────────────────────────────────────────────────────────
# CORE_3 — The only symbols that demonstrated survivable edge across
# all three friction profiles (idealized, moderate, conservative) in
# the realistic backtest.  12 other symbols were tested and dropped:
# they either had inconsistent results or were net losers once real
# frictions (slippage, fill rate, spread) were modeled.
#
# Conservative profile on CORE_3 (180-day backtest):
#   - PF 1.35, Sharpe 1.77, max drawdown 0.55%
#   - 59 trades, 50.8% win rate
#   - +1.12% return under pessimistic assumptions

TARGET_SYMBOLS: list[str] = [
    "GOOGL",  # Alphabet
    "META",   # Meta Platforms
    "AAPL",   # Apple
]

# ── Data Parameters ──────────────────────────────────────────────────────────

TIMEFRAME: TimeFrame = TimeFrame(5, TimeFrameUnit.Minute)
LOOKBACK_DAYS: int = 5              # Enough bars for EMA-200 warm-up

# ── Signal Thresholds ────────────────────────────────────────────────────────

RSI_OVERSOLD: int = 28

# ── Position Sizing & Risk ───────────────────────────────────────────────────

ALLOCATION_PERCENT: float = 0.50       # 50% of available buying power per trade
MAX_OPEN_POSITIONS: int = 5            # Up to 5 simultaneous positions

# ── Exit Strategy (ATR-based) ────────────────────────────────────────────────
# Exits are calculated from the LIMIT price (which equals the fill price
# since we use limit entries), ensuring TP/SL are always correctly
# distanced from the actual entry point.

TP_ATR_MULTIPLIER: float = 1.5        # Take-profit = limit + 1.5 × ATR
SL_ATR_MULTIPLIER: float = 1.0        # Stop-loss   = limit - 1.0 × ATR

# ── Limit Entry ──────────────────────────────────────────────────────────────
# Buffer above signal price for the limit buy.  Prevents chasing bounces
# (signals often fire just before the reversal begins) while still
# handling normal bid-ask tick noise.
#
# If the market bounces more than $0.05 between signal and order
# arrival, the order sits unfilled and gets cancelled at the start
# of the next cycle.

LIMIT_BUFFER_DOLLARS: float = 0.05

# ── Risk Guards ──────────────────────────────────────────────────────────────

COOLDOWN_MINUTES: int = 30             # No re-entry on a stopped-out symbol
DAILY_LOSS_LIMIT_PCT: float = 0.03     # 3% daily loss → circuit breaker

# ── Execution Mode ───────────────────────────────────────────────────────────

PAPER_TRADING: bool = True

# ── Trading Window (UTC) ─────────────────────────────────────────────────────
# 1:00-3:30 PM ET = 17:00-19:30 UTC (during DST).
# Time-of-day backtesting showed morning entries consistently lose money
# for this strategy; afternoon-only trading was a key edge discovery.

TRADING_START_HOUR_UTC: int = 17
TRADING_END_HOUR_UTC: int = 19
TRADING_END_MINUTE_UTC: int = 30

# ── Scheduling ───────────────────────────────────────────────────────────────

CYCLE_INTERVAL_SEC: int = 300          # Match the 5-minute candle timeframe
