# VWAP Reversion Engine

An automated intraday mean-reversion trading system built on the Alpaca
brokerage API.  The engine scans a curated set of 3 mega-cap stocks on
5-minute candles during a data-optimized afternoon window, identifies
oversold entries using RSI and VWAP, and executes trades with
limit-bracket orders that combine entry and ATR-adaptive exits in a
single atomic submission.

Deployed on a Raspberry Pi via systemd for fully autonomous operation.

## Strategy Overview

The engine targets quick intraday bounces (avg hold: ~30 minutes) by
looking for two simultaneous conditions during the afternoon session:

1. **RSI(14) < 28** — the ticker is oversold on the 5-minute timeframe.
2. **Price < VWAP** — price is trading below its volume-weighted average,
   confirming the dip has substance.

When both conditions fire, the engine submits a **limit bracket order**:

1. **Limit buy** at `signal_price + $0.05` — only fills if the price is
   still in the oversold zone.  If the bounce has already started, the
   order sits unfilled and is cancelled at the start of the next cycle.
2. **Take-profit** (child order) at `limit + 1.5 × ATR`.
3. **Stop-loss** (child order) at `limit - 1.0 × ATR`.

Alpaca executes the bracket atomically, so TP and SL cannot desynchronize.
Using a limit (rather than market) entry prevents the common failure mode
where a market order chases a reversal and Alpaca rejects the bracket
because the new market price has moved past the TP or SL.

## Key Parameters

| Parameter | Value | Rationale |
|---|---|---|
| Universe | GOOGL, META, AAPL | Only symbols with survivable edge across all friction profiles |
| Timeframe | 5-minute candles | Balance between signal quality and noise |
| RSI threshold | < 28 | Consistent oversold across mega-caps |
| Allocation | 50% of buying power | Per-trade sizing, self-limiting on successive entries |
| Max positions | 5 | Room to hold all 3 symbols + reserve capacity |
| TP / SL | 1.5× / 1.0× ATR | Adaptive exits; 1.5:1 reward-to-risk ratio |
| Limit buffer | $0.05 | Handle tick noise without chasing bounces |
| Trading window | 1:00–3:30 PM ET | Afternoon-only outperformed all-day in backtesting |
| SL cooldown | 30 minutes | Prevents cascading losses on the same ticker |
| Daily loss limit | 3% | Circuit breaker shuts down entries for the day |

## Realistic Backtest Results

The original backtest used idealized fills (100% execution at signal
price, zero slippage, zero spread) on a symbol universe that was
different from what the bot actually traded in production.  After a
rigorous re-backtest that models realistic frictions, the honest
picture is:

```
CORE_3 universe (GOOGL, META, AAPL), 180-day backtest
$25,000 starting capital

Profile         Trades   Fill%   Return     PF    Sharpe  MaxDD
─────────────────────────────────────────────────────────────────
IDEALIZED         60     45.1%   +1.71%   1.56    3.14   -0.51%
MODERATE          59     44.4%   +1.73%   1.56    2.77   -0.52%
CONSERVATIVE      59     44.0%   +1.12%   1.35    1.77   -0.55%
```

The **MODERATE** profile matches observed live behavior: $0.05 limit
buffer, $0.02 stop-loss slippage, price improvement on fills.  The
**CONSERVATIVE** profile stacks pessimistic assumptions (strict dip
requirement, $0.05 SL slippage, 10% TP partial-fill haircut) and the
strategy still shows positive expectancy.

This is not a "get rich" strategy.  It is an incremental edge that
needs to be run mechanically and continuously to matter.

## What Was Tested and Rejected

| Feature | Result | Why rejected |
|---|---|---|
| 12 additional symbols (CRM, JPM, UNH, MSFT, etc.) | Net losers under friction | Less mean-reversion; spreads eat edge |
| Market entry orders | 60% bracket rejection rate | Bounce starts before order reaches exchange |
| Dynamic pre-market scanner | -13.3% return | Gap-down stocks don't mean-revert reliably |
| 1-minute candles | Noise dominated signal | 4x trades, materially worse quality |
| Trailing stops | Avg win cut nearly in half | Bounces are short snaps, not trends |
| Entry filters (VWAP distance, volume, EMA-200) | Lowered PF | Removed more winners than losers |
| Conviction sizing | Amplified falling-knife losses | Deepest RSI ≠ best entries |

## Project Structure

```
vwap_reversion_engine/
├── backtest/
│   └── realistic_engine.py        # Friction-aware backtesting engine
├── config/
│   └── settings.py                # All tunable parameters
├── src/
│   ├── bot.py                     # Core loop: signal detection + orchestration
│   ├── data.py                    # Market data fetching
│   ├── execution.py               # Limit-bracket order submission
│   └── indicators.py              # RSI, VWAP, EMA-200, ATR, volume avg
├── utils/
│   ├── exceptions.py              # Custom exception hierarchy
│   ├── journal.py                 # Trade journal (CSV audit log)
│   ├── logger.py                  # Centralized logging
│   └── validation.py              # Startup pre-flight checks
├── tests/
│   └── test_core.py               # Unit tests (offline, no API needed)
├── generate_daily_report.py       # End-of-day performance report
├── run_realistic_backtest.py      # Friction comparison across 3 profiles
├── run_winners_backtest.py        # Multi-universe subset testing
├── main.py                        # Entry point for the live engine
├── requirements.txt
└── pyproject.toml
```

## Prerequisites

- Python 3.10+
- An [Alpaca](https://alpaca.markets/) brokerage account (paper or live)
- Raspberry Pi (optional, for autonomous deployment)

## Setup

```bash
git clone git@github.com:jmschmitz12/vwap-reversion-engine.git
cd vwap-reversion-engine

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt

cp .env.example .env
# Edit .env with your Alpaca API key and secret

python main.py
```

## Raspberry Pi Deployment

```bash
# On the Pi
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git
mkdir -p ~/trading && cd ~/trading
git clone git@github.com:jmschmitz12/vwap-reversion-engine.git
cd vwap-reversion-engine
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
cp .env.example .env && nano .env  # Add your API keys

# Create systemd service
sudo tee /etc/systemd/system/vwap-engine.service << 'EOF'
[Unit]
Description=VWAP Reversion Engine
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=YOUR_USERNAME
WorkingDirectory=/home/YOUR_USERNAME/trading/vwap-reversion-engine
ExecStart=/home/YOUR_USERNAME/trading/vwap-reversion-engine/venv/bin/python main.py
Restart=on-failure
RestartSec=60
StandardOutput=append:/home/YOUR_USERNAME/trading/vwap-reversion-engine/logs/service.log
StandardError=append:/home/YOUR_USERNAME/trading/vwap-reversion-engine/logs/service.log

[Install]
WantedBy=multi-user.target
EOF

mkdir -p logs
sudo systemctl daemon-reload
sudo systemctl enable vwap-engine.service
sudo systemctl start vwap-engine.service
```

## Daily Operations

```bash
# Check service status
sudo systemctl status vwap-engine.service

# Watch live logs
tail -f ~/trading/vwap-reversion-engine/logs/engine.log

# Generate end-of-day report (for a specific date)
python generate_daily_report.py                # Today
python generate_daily_report.py 2026-04-20     # Specific date

# Deploy code updates (laptop → GitHub → Pi)
# On laptop:
git commit -am "..." && git push
# On Pi:
cd ~/trading/vwap-reversion-engine && git pull && sudo systemctl restart vwap-engine.service
```

## Backtesting

```bash
# Compare idealized vs moderate vs conservative assumptions
python run_realistic_backtest.py

# Test multiple curated symbol subsets against all three profiles
python run_winners_backtest.py
```

## Testing

```bash
pytest
```

Tests cover position sizing math, startup validation, and trade journal
writes.  They run entirely offline — no Alpaca credentials required.

## Development History

The strategy went through six major iterations, with each decision
driven by backtest data rather than intuition:

1. **Signal discovery** — RSI < 28 + price < VWAP on 5-min candles as
   the core mean-reversion trigger.
2. **Time-window filter** — Afternoon-only (1:00-3:30 PM ET) after
   hour-by-hour analysis showed morning entries consistently lost money.
3. **ATR-adaptive exits** — Replaced fixed-percentage TP/SL with ATR
   multiples to adapt to each stock's current volatility.
4. **Execution hardening** — Multiple iterations to find a reliable
   Alpaca order flow:  market+OCO (SDK errors) → market+two-sell (share-
   lock bug) → market+bracket (60% rejection rate) → limit+bracket (✓).
5. **Realistic backtesting** — Discovered the original PF 1.24 result
   was on a different symbol universe than production and ignored fill
   rate, slippage, and spread.  Rebuilt the backtest to model these.
6. **Symbol curation** — Per-symbol friction analysis identified that
   only GOOGL, META, and AAPL retained edge under conservative
   assumptions.  Reduced from 15 symbols to the CORE_3 set.

## Disclaimer

This software is for **educational and research purposes only**.
Automated trading carries substantial risk of financial loss.  Past
performance of any strategy does not guarantee future results.  Always
test thoroughly with paper trading before risking real capital.
