# VWAP Reversion Engine

An automated intraday mean-reversion trading system built on the Alpaca
brokerage API. The engine scans 15 mega-cap stocks on 5-minute candles
during a data-optimized afternoon trading window, identifies oversold
entries using RSI and VWAP, and executes trades with ATR-adaptive exits
calculated from actual fill prices.

Deployed on a Raspberry Pi via systemd for fully autonomous operation.

## Strategy Overview

The engine targets quick intraday bounces (avg hold: ~30 minutes) by
looking for two simultaneous conditions during the afternoon session:

1. **RSI(14) < 28** — the ticker is oversold on the 5-minute timeframe.
2. **Price < VWAP** — price is trading below its volume-weighted average,
   confirming the dip has substance.

When both conditions fire, the engine:

1. Submits a **market buy** order.
2. Polls for the actual **fill price** (not the signal price).
3. Calculates **ATR-based exits** from the fill: TP at 1.5× ATR above,
   SL at 1.0× ATR below — adapting to current volatility.
4. Submits an **OCO exit order** (limit sell at TP + stop sell at SL).

This two-step flow eliminates fill-price drift where exits would be
misaligned because the market moved between signal and execution.

## Key Parameters

| Parameter | Value | Rationale |
|---|---|---|
| Universe | 15 mega-cap stocks | Institutional dip-buying provides reliable mean reversion |
| Timeframe | 5-minute candles | 1-min too noisy (PF 1.15), 5-min optimal (PF 1.24) |
| RSI threshold | < 28 | Optimized from sweep testing (was 30) |
| Allocation | 50% of buying power | Per-trade sizing, self-limiting on successive entries |
| Max positions | 5 | Allows broad participation across the universe |
| TP / SL | 1.5× / 1.0× ATR | Adaptive exits; 1.5:1 reward-to-risk ratio |
| Trading window | 1:00–3:30 PM ET | Time analysis showed morning entries lose money |
| SL cooldown | 30 minutes | Prevents cascading losses on the same ticker |
| Daily loss limit | 3% | Circuit breaker shuts down entries for the day |

## Backtest Results (Best Proven Config)

```
Return:          +4.93%  (6 months, ~10% annualized)
Profit Factor:   1.24
Sharpe Ratio:    1.54
Win Rate:        45.4%
Max Drawdown:    -4.63%
Avg Win:         $84.28  (+0.39%)
Avg Loss:        -$56.40 (-0.26%)
Avg Hold:        30 min
Trades:          328
```

## What Was Tested and Rejected

| Feature | Result | Why rejected |
|---|---|---|
| Scanner (dynamic daily picks) | -13.3% return | Gap-down stocks don't mean-revert reliably |
| 1-minute candles | PF 1.15, Sharpe 0.91 | Too noisy, 4x more trades with worse quality |
| Trailing stop | Avg win dropped $84→$53 | Mean reversion = short snap, not a trend to ride |
| Entry filters (VWAP dist, volume, EMA-200) | PF 1.05 | Removed more winners than losers |
| Conviction sizing | PF 1.05 | Amplified falling-knife losses |

## Project Structure

```
vwap_reversion_engine/
├── backtest/
│   ├── engine.py                # Event-driven backtesting engine
│   ├── report.py                # Stats, equity curve, per-symbol breakdown
│   └── scanner_engine.py        # Scanner-aware backtester (disabled)
├── config/
│   └── settings.py              # All tunable parameters in one place
├── scanner/
│   ├── premarket.py             # Gap-down scanner (disabled)
│   └── universe.py              # ~100 high-cap symbols for scanner
├── src/
│   ├── bot.py                   # Core loop: signal detection + trade orchestration
│   ├── data.py                  # Market data fetching via Alpaca
│   ├── execution.py             # Two-step fill-price-aware order flow
│   └── indicators.py            # RSI, VWAP, EMA-200, ATR, volume avg
├── utils/
│   ├── exceptions.py            # Custom exception hierarchy
│   ├── journal.py               # Trade journal (CSV audit log)
│   ├── logger.py                # Centralized logging
│   └── validation.py            # Startup pre-flight checks
├── tests/
│   └── test_core.py             # Unit tests (offline, no API needed)
├── logs/
│   ├── engine.log               # Main engine log
│   ├── service.log              # systemd service output
│   ├── trade_journal.csv        # All executed trades
│   └── daily_reports/           # End-of-day analysis reports
├── generate_daily_report.py     # Daily summary generator
├── run_backtest.py              # Fixed-symbol backtest
├── run_time_analysis.py         # Hour-by-hour P&L breakdown
├── run_sweep.py                 # Parameter optimization sweep
├── run_scanner_backtest.py      # Scanner backtest (disabled)
├── main.py                      # Entry point with scanner toggle
├── requirements.txt
├── pyproject.toml
└── .env.example
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
# Check status
sudo systemctl status vwap-engine.service

# Watch live logs
tail -f ~/trading/vwap-reversion-engine/logs/engine.log

# Generate end-of-day report
python generate_daily_report.py

# Deploy code updates
cd ~/trading/vwap-reversion-engine && git pull && sudo systemctl restart vwap-engine.service
```

## Backtesting

```bash
# Run backtest with current settings
python run_backtest.py

# Hour-by-hour P&L analysis (identifies best trading windows)
python run_time_analysis.py

# Parameter optimization sweep
python run_sweep.py
```

## Testing

```bash
pytest
```

Tests cover position sizing math, startup validation, and trade journal
writes. They run entirely offline — no Alpaca credentials required.

## Optimization History

The system evolved through data-driven iteration:

1. **Original** — 7 symbols, RSI<30, fixed 0.5%/0.5% exits → PF 1.08, Sharpe 0.59
2. **Symbol + RSI optimization** — 3 symbols, RSI<28, 0.7%/0.5% → PF 1.23, Sharpe 1.68
3. **Time filter** — Afternoon only (1–3:30 PM ET) → PF 1.56, Sharpe 3.44
4. **Expanded universe** — 15 mega-caps at 25% allocation → PF 1.15, Sharpe 1.26
5. **ATR exits + cooldown + breaker** — Adaptive exits, risk guards → PF 1.24, Sharpe 1.54
6. **Fill-price fix** — Two-step order flow, exits from actual fill → In production

## Disclaimer

This software is for **educational and research purposes only**. Automated
trading carries substantial risk of financial loss. Past performance of any
strategy does not guarantee future results. Always test thoroughly with paper
trading before risking real capital.
