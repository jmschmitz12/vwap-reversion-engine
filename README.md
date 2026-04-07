# VWAP Reversion Engine

An automated intraday mean-reversion trading system built on the Alpaca
brokerage API. The engine scans a configurable universe of tickers on
5-minute candles, identifies oversold entries using RSI and VWAP, and
executes bracket orders with predefined take-profit and stop-loss exits.

## Strategy Overview

The engine targets quick intraday bounces by looking for two simultaneous
conditions:

1. **RSI(14) < 30** — the ticker is oversold on the 5-minute timeframe.
2. **Price < VWAP** — price is trading below its volume-weighted average,
   confirming the dip has substance.

When both conditions are met, the engine sizes a position at a configurable
percentage of buying power and submits a bracket order targeting a 0.5% bounce
with a matching 0.5% stop-loss (1:1 risk/reward).

## Project Structure

```
vwap_reversion_engine/
├── backtest/
│   ├── __init__.py
│   ├── engine.py             # Event-driven backtesting engine
│   ├── report.py             # Stats computation and equity curve chart
│   └── scanner_engine.py     # Scanner-aware backtester
├── config/
│   ├── __init__.py
│   └── settings.py           # API keys, symbols, risk parameters
├── scanner/
│   ├── __init__.py
│   ├── premarket.py           # Live + historical gap-down scanner
│   └── universe.py            # Curated list of ~100 high-cap symbols
├── src/
│   ├── __init__.py
│   ├── bot.py                 # Core loop orchestration and signal logic
│   ├── data.py                # Market data fetching via Alpaca
│   ├── execution.py           # Order construction and submission
│   └── indicators.py          # Technical indicator calculations
├── utils/
│   ├── __init__.py
│   ├── exceptions.py          # Custom exception hierarchy
│   ├── journal.py             # Trade journal (CSV audit log)
│   ├── logger.py              # Centralized logging configuration
│   └── validation.py          # Startup pre-flight checks
├── tests/
│   ├── __init__.py
│   └── test_core.py           # Unit tests for sizing, validation, journal
├── .env.example
├── .gitignore
├── pyproject.toml
├── README.md
├── requirements.txt
├── run_backtest.py            # Fixed-symbol backtest
├── run_scanner_backtest.py    # Scanner-aware backtest
└── run_sweep.py               # Parameter optimization sweep
```

## Prerequisites

- Python 3.10+
- An [Alpaca](https://alpaca.markets/) brokerage account (paper or live)

## Setup

```bash
# Clone the repository
git clone <repo-url> && cd vwap_reversion_engine

# Create and activate a virtual environment
python -m venv venv
source venv/bin/activate        # Linux / macOS
venv\Scripts\activate           # Windows

# Install dependencies
pip install -r requirements.txt

# Configure credentials
cp .env.example .env
# Edit .env with your Alpaca API key and secret

# Run the engine
python main.py
```

## Configuration

All tunable parameters live in `config/settings.py`:

| Parameter            | Default                                       | Description                                |
|----------------------|-----------------------------------------------|--------------------------------------------|
| `TARGET_SYMBOLS`     | NVDA, META, AMZN                              | Fallback tickers when scanner is off       |
| `TIMEFRAME`          | 5 minutes                                     | Candlestick interval                       |
| `PAPER_TRADING`      | `True`                                        | Toggle paper vs. live execution            |
| `ALLOCATION_PERCENT` | 0.10                                          | Fraction of buying power per trade         |
| `TP_PERCENT`         | 0.007                                         | Take-profit distance from entry (0.7%)     |
| `SL_PERCENT`         | 0.005                                         | Stop-loss distance from entry (0.5%)       |
| `RSI_OVERSOLD`       | 28                                            | RSI threshold for buy signals              |
| `MAX_OPEN_POSITIONS` | 3                                             | Maximum concurrent positions allowed       |
| `USE_SCANNER`        | `True`                                        | Enable dynamic premarket scanner           |
| `SCANNER_MAX_CANDIDATES` | 8                                          | Max symbols the scanner selects per day    |
| `CYCLE_INTERVAL_SEC` | 300                                           | Seconds between analysis cycles            |

## Premarket Scanner

When `USE_SCANNER = True`, the engine runs a premarket scan each morning
before the opening bell.  It evaluates ~100 high-cap, high-liquidity stocks
for overnight gap-downs between -2% and -10%, filters by premarket volume
and price, and selects the top 8 candidates for that day's trading session.

If no candidates meet the criteria on a given day, the engine falls back to
the static `TARGET_SYMBOLS` list.

## Backtesting

Run a fixed-symbol backtest against the optimized parameters:

```bash
python run_backtest.py
```

Run a scanner-aware backtest that replays what the scanner would have picked
each day over the past 6 months:

```bash
python run_scanner_backtest.py
```

Run a parameter sweep across multiple configurations:

```bash
python run_sweep.py
```

All backtests output a terminal performance summary and save an equity curve
chart to the `reports/` directory.

## Testing

```bash
pytest
```

Tests cover position sizing math, startup validation, and trade journal
writes.  They run entirely offline — no Alpaca credentials required.

## Disclaimer

This software is for **educational and research purposes only**. Automated
trading carries substantial risk of financial loss. Past performance of any
strategy does not guarantee future results. Always test thoroughly with paper
trading before risking real capital.
