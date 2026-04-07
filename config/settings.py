"""
Centralized configuration for the VWAP Reversion Engine.

All tunable parameters are defined here so the rest of the codebase
stays free of magic numbers. API credentials are loaded from a .env
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
# Mega-cap names with high institutional ownership where afternoon
# dips get bought by fund rebalancing.  Curated from backtest and
# scanner analysis — speculative/mid-cap names excluded.

TARGET_SYMBOLS: list[str] = [
    "AAPL",   # Apple
    "MSFT",   # Microsoft
    "GOOGL",  # Alphabet
    "META",   # Meta Platforms
    "AMZN",   # Amazon
    "AMD",    # AMD
    "AVGO",   # Broadcom
    "CRM",    # Salesforce
    "NFLX",   # Netflix
    "QCOM",   # Qualcomm
    "LLY",    # Eli Lilly
    "JPM",    # JPMorgan Chase
    "GS",     # Goldman Sachs
    "UNH",    # UnitedHealth
    "ADBE",   # Adobe
]

# ── Data Parameters ──────────────────────────────────────────────────────────

TIMEFRAME: TimeFrame = TimeFrame(5, TimeFrameUnit.Minute)
LOOKBACK_DAYS: int = 5              # Enough bars for EMA-200 warm-up

# ── Signal Thresholds ────────────────────────────────────────────────────────

RSI_OVERSOLD: int = 28

# ── Entry Quality Filters ────────────────────────────────────────────────────
# Tested and disabled — degraded performance vs base config.

VWAP_DISTANCE_PCT: float = 0.0
VOLUME_MULTIPLIER: float = 0.0
USE_EMA_TREND_FILTER: bool = False

# ── Position Sizing & Risk ───────────────────────────────────────────────────

ALLOCATION_PERCENT: float = 0.50       # 50% of available buying power per trade
MAX_OPEN_POSITIONS: int = 5            # Up to 5 simultaneous positions

# ── Conviction Sizing ────────────────────────────────────────────────────────

USE_CONVICTION_SIZING: bool = False
CONVICTION_MAX_MULTIPLIER: float = 1.5

# ── Exit Strategy ────────────────────────────────────────────────────────────
# ATR-based exits calculated from ACTUAL FILL PRICE (not signal price).
# The live engine submits a market order first, gets the fill, then
# submits exit orders based on the real entry.

USE_ATR_EXITS: bool = True
TP_ATR_MULTIPLIER: float = 1.5        # Take-profit = fill + 1.5 x ATR
SL_ATR_MULTIPLIER: float = 1.0        # Stop-loss   = fill - 1.0 x ATR

# Trailing stop — tested and disabled (mean reversion bounces are
# short snaps, not trends to ride; reduced avg win from $84 to $53).
USE_TRAILING_STOP: bool = False
TRAIL_ACTIVATION_ATR: float = 1.0
TRAIL_DISTANCE_ATR: float = 0.5

# Fallback fixed exits (used when ATR unavailable)
TP_PERCENT: float = 0.007
SL_PERCENT: float = 0.005

# ── Risk Guards ──────────────────────────────────────────────────────────────

COOLDOWN_MINUTES: int = 30
DAILY_LOSS_LIMIT_PCT: float = 0.03     # 3% daily loss limit

# ── Execution Mode ───────────────────────────────────────────────────────────

PAPER_TRADING: bool = True

# ── Trading Window (Eastern Time) ────────────────────────────────────────────

TRADING_START_HOUR_UTC: int = 17       # 1:00 PM ET = 17:00 UTC
TRADING_END_HOUR_UTC: int = 19         # 3:00 PM ET = 19:00 UTC
TRADING_END_MINUTE_UTC: int = 30       # 3:30 PM ET = 19:30 UTC

# ── Scanner Mode ─────────────────────────────────────────────────────────────

USE_SCANNER: bool = False
SCANNER_MAX_CANDIDATES: int = 5

# ── Scheduling ───────────────────────────────────────────────────────────────

CYCLE_INTERVAL_SEC: int = 300
