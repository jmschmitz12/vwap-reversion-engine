#!/usr/bin/env python3
"""
Generate a daily report for the VWAP Reversion Engine.

Run at end of day:  python generate_daily_report.py
Optional date arg:  python generate_daily_report.py 2026-04-07

Outputs a .txt file to logs/daily_reports/ that captures everything
needed to evaluate the day's trading and inform adjustments.
"""

import csv
import os
import re
import sys
from datetime import datetime, timedelta
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Config ────────────────────────────────────────────────────────────────────

PROJECT_DIR = Path(__file__).parent
LOG_FILE = PROJECT_DIR / "logs" / "engine.log"
SERVICE_LOG = PROJECT_DIR / "logs" / "service.log"
JOURNAL_FILE = PROJECT_DIR / "logs" / "trade_journal.csv"
REPORT_DIR = PROJECT_DIR / "logs" / "daily_reports"
SETTINGS_FILE = PROJECT_DIR / "config" / "settings.py"


def get_report_date() -> str:
    """Return target date as YYYY-MM-DD string."""
    if len(sys.argv) > 1:
        return sys.argv[1]
    return datetime.now().strftime("%Y-%m-%d")


def read_log_lines(date_str: str) -> list[str]:
    """Extract all log lines for the given date from both log files."""
    lines = []
    for path in [LOG_FILE, SERVICE_LOG]:
        if not path.exists():
            continue
        with open(path) as f:
            for line in f:
                if date_str in line:
                    lines.append(line.rstrip())
    return lines


def parse_entries(lines: list[str]) -> list[dict]:
    """Extract entry details from log lines."""
    entries = []
    for line in lines:
        if "FILLED" in line and "drift" in line:
            m = re.search(
                r"FILLED (\d+) x (\w+) @ \$([0-9.]+) \(signal: \$([0-9.]+), drift: \$([0-9.-]+)\) \| TP: \$([0-9.]+) \| SL: \$([0-9.]+) \| (\w+)",
                line,
            )
            if m:
                entries.append({
                    "time": line[:19],
                    "symbol": m.group(2),
                    "qty": int(m.group(1)),
                    "fill_price": float(m.group(3)),
                    "signal_price": float(m.group(4)),
                    "drift": float(m.group(5)),
                    "tp": float(m.group(6)),
                    "sl": float(m.group(7)),
                    "exit_mode": m.group(8),
                })
    return entries


def parse_errors(lines: list[str]) -> list[str]:
    """Extract ERROR lines."""
    return [l for l in lines if "| ERROR" in l]


def parse_signals(lines: list[str]) -> list[str]:
    """Extract BUY SIGNAL lines."""
    return [l for l in lines if "BUY SIGNAL" in l]


def parse_rsi_snapshots(lines: list[str]) -> list[dict]:
    """Extract the last RSI snapshot for each symbol from cycle logs."""
    snapshots = {}
    for line in lines:
        m = re.search(
            r"\[(\w+)\] Price: \$([0-9.]+) \| RSI: ([0-9.]+) \| VWAP: \$([0-9.]+) \| ATR: \$([0-9.]+)",
            line,
        )
        if m:
            snapshots[m.group(1)] = {
                "symbol": m.group(1),
                "price": float(m.group(2)),
                "rsi": float(m.group(3)),
                "vwap": float(m.group(4)),
                "atr": float(m.group(5)),
            }
    return sorted(snapshots.values(), key=lambda x: x["rsi"])


def read_journal_entries(date_str: str) -> list[dict]:
    """Read trade journal entries for the given date."""
    entries = []
    if not JOURNAL_FILE.exists():
        return entries
    with open(JOURNAL_FILE) as f:
        reader = csv.DictReader(f)
        for row in reader:
            if date_str in row.get("timestamp", ""):
                entries.append(row)
    return entries


def get_alpaca_positions() -> list[dict]:
    """Fetch current positions from Alpaca."""
    try:
        from alpaca.trading.client import TradingClient

        api_key = os.getenv("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        if not api_key:
            return []

        client = TradingClient(api_key, secret_key, paper=True)
        positions = client.get_all_positions()
        return [
            {
                "symbol": p.symbol,
                "qty": int(p.qty),
                "entry": float(p.avg_entry_price),
                "current": float(p.current_price),
                "market_value": float(p.market_value),
                "unrealized_pl": float(p.unrealized_pl),
                "unrealized_plpc": float(p.unrealized_plpc) * 100,
            }
            for p in positions
        ]
    except Exception as e:
        return [{"error": str(e)}]


def get_alpaca_account() -> dict:
    """Fetch account summary from Alpaca."""
    try:
        from alpaca.trading.client import TradingClient

        api_key = os.getenv("ALPACA_API_KEY", "")
        secret_key = os.getenv("ALPACA_SECRET_KEY", "")
        if not api_key:
            return {}

        client = TradingClient(api_key, secret_key, paper=True)
        acct = client.get_account()
        return {
            "equity": float(acct.equity),
            "cash": float(acct.cash),
            "buying_power": float(acct.buying_power),
            "portfolio_value": float(acct.portfolio_value),
            "day_trade_count": int(acct.daytrade_count),
        }
    except Exception as e:
        return {"error": str(e)}


def read_settings() -> dict:
    """Read key settings values."""
    settings = {}
    if not SETTINGS_FILE.exists():
        return settings
    with open(SETTINGS_FILE) as f:
        for line in f:
            for key in [
                "ALLOCATION_PERCENT",
                "MAX_OPEN_POSITIONS",
                "RSI_OVERSOLD",
                "TP_ATR_MULTIPLIER",
                "SL_ATR_MULTIPLIER",
                "COOLDOWN_MINUTES",
                "DAILY_LOSS_LIMIT_PCT",
                "LOOKBACK_DAYS",
                "USE_TRAILING_STOP",
            ]:
                if line.strip().startswith(f"{key}"):
                    m = re.search(r"=\s*(.+?)(?:\s*#|$)", line)
                    if m:
                        settings[key] = m.group(1).strip()
    return settings


def count_cycles(lines: list[str]) -> dict:
    """Count cycle types for the day."""
    total = sum(1 for l in lines if "Starting analysis cycle" in l)
    market_closed = sum(1 for l in lines if "Market is closed" in l)
    outside_window = sum(1 for l in lines if "Outside trading window" in l)
    at_cap = sum(1 for l in lines if "position cap" in l or "At position cap" in l)
    scanning = total - market_closed - outside_window - at_cap
    return {
        "total_cycles": total,
        "market_closed": market_closed,
        "outside_window": outside_window,
        "at_position_cap": at_cap,
        "active_scanning": scanning,
    }


def generate_report(date_str: str) -> str:
    """Build the full daily report string."""
    lines = read_log_lines(date_str)
    entries = parse_entries(lines)
    errors = parse_errors(lines)
    signals = parse_signals(lines)
    snapshots = parse_rsi_snapshots(lines)
    journal = read_journal_entries(date_str)
    positions = get_alpaca_positions()
    account = get_alpaca_account()
    settings = read_settings()
    cycles = count_cycles(lines)

    sep = "=" * 70
    subsep = "-" * 70
    r = []

    r.append(sep)
    r.append(f"  VWAP REVERSION ENGINE — DAILY REPORT")
    r.append(f"  Date: {date_str}")
    r.append(f"  Generated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    r.append(sep)

    # ── Account Summary
    r.append("\n  ACCOUNT SUMMARY")
    r.append(subsep)
    if "error" not in account:
        r.append(f"  Portfolio Value:     ${account.get('portfolio_value', 0):>12,.2f}")
        r.append(f"  Cash:               ${account.get('cash', 0):>12,.2f}")
        r.append(f"  Buying Power:       ${account.get('buying_power', 0):>12,.2f}")
        r.append(f"  Day Trade Count:    {account.get('day_trade_count', 0):>12}")
    else:
        r.append(f"  Error fetching account: {account['error']}")

    # ── Open Positions
    r.append(f"\n  OPEN POSITIONS ({len(positions)})")
    r.append(subsep)
    if positions and "error" not in positions[0]:
        r.append(f"  {'Symbol':<8} {'Qty':>5} {'Entry':>10} {'Current':>10} {'P&L':>10} {'P&L%':>8}")
        r.append(f"  {'------':<8} {'---':>5} {'-----':>10} {'-------':>10} {'---':>10} {'----':>8}")
        total_unrealized = 0
        for p in positions:
            r.append(
                f"  {p['symbol']:<8} {p['qty']:>5} ${p['entry']:>9.2f} ${p['current']:>9.2f} "
                f"${p['unrealized_pl']:>9.2f} {p['unrealized_plpc']:>7.2f}%"
            )
            total_unrealized += p["unrealized_pl"]
        r.append(f"  {'':>35} Total: ${total_unrealized:>9.2f}")
    elif not positions:
        r.append("  No open positions.")
    else:
        r.append(f"  Error: {positions[0].get('error', 'unknown')}")

    # ── Today's Entries
    r.append(f"\n  TODAY'S ENTRIES ({len(entries)})")
    r.append(subsep)
    if entries:
        for e in entries:
            r.append(
                f"  {e['time']} | {e['symbol']:<6} | {e['qty']} × ${e['fill_price']:.2f} "
                f"(signal: ${e['signal_price']:.2f}, drift: ${e['drift']:+.2f}) "
                f"| TP: ${e['tp']:.2f} | SL: ${e['sl']:.2f} | {e['exit_mode']}"
            )
    else:
        r.append("  No entries today.")

    # ── Signals Fired
    r.append(f"\n  SIGNALS FIRED ({len(signals)})")
    r.append(subsep)
    for s in signals:
        # Extract just time and symbol
        m = re.search(r"(\d{2}:\d{2}:\d{2}).*BUY SIGNAL -- (\w+)", s)
        if m:
            r.append(f"  {m.group(1)} — {m.group(2)}")

    # ── Errors
    r.append(f"\n  ERRORS ({len(errors)})")
    r.append(subsep)
    if errors:
        # Deduplicate by error message
        seen = set()
        for e in errors:
            # Extract just the error portion
            m = re.search(r"\| ERROR\s+\| (.+)", e)
            msg = m.group(1) if m else e
            if msg not in seen:
                seen.add(msg)
                count = sum(1 for x in errors if msg in x)
                r.append(f"  [{count}x] {msg[:120]}")
    else:
        r.append("  No errors.")

    # ── Cycle Summary
    r.append(f"\n  CYCLE SUMMARY")
    r.append(subsep)
    r.append(f"  Total cycles:        {cycles['total_cycles']:>6}")
    r.append(f"  Active scanning:     {cycles['active_scanning']:>6}")
    r.append(f"  Market closed:       {cycles['market_closed']:>6}")
    r.append(f"  Outside window:      {cycles['outside_window']:>6}")
    r.append(f"  At position cap:     {cycles['at_position_cap']:>6}")

    # ── End-of-Day RSI Snapshot
    r.append(f"\n  END-OF-DAY INDICATOR SNAPSHOT")
    r.append(subsep)
    r.append(f"  {'Symbol':<8} {'Price':>10} {'RSI':>8} {'VWAP':>10} {'ATR':>8} {'vs VWAP':>8}")
    r.append(f"  {'------':<8} {'-----':>10} {'---':>8} {'----':>10} {'---':>8} {'-------':>8}")
    for s in snapshots:
        vwap_dist = ((s["price"] - s["vwap"]) / s["vwap"]) * 100
        flag = " ◄" if s["rsi"] < 30 else ""
        r.append(
            f"  {s['symbol']:<8} ${s['price']:>9.2f} {s['rsi']:>7.1f} "
            f"${s['vwap']:>9.2f} ${s['atr']:>7.2f} {vwap_dist:>+7.2f}%{flag}"
        )

    # ── Active Settings
    r.append(f"\n  ACTIVE SETTINGS")
    r.append(subsep)
    for k, v in settings.items():
        r.append(f"  {k + ':':<30} {v}")

    # ── Trade Journal (raw)
    r.append(f"\n  TRADE JOURNAL ENTRIES ({len(journal)})")
    r.append(subsep)
    if journal:
        for j in journal:
            r.append(
                f"  {j.get('timestamp', 'N/A')[:19]} | {j.get('symbol', '?'):<6} | "
                f"{j.get('side', '?')} {j.get('qty', '?')} × ${float(j.get('entry_price', 0)):.2f} | "
                f"TP: ${float(j.get('take_profit', 0)):.2f} | SL: ${float(j.get('stop_loss', 0)):.2f}"
            )
    else:
        r.append("  No journal entries for this date.")

    r.append(f"\n{sep}")
    r.append(f"  END OF REPORT — {date_str}")
    r.append(sep)

    return "\n".join(r)


def main():
    date_str = get_report_date()
    report = generate_report(date_str)

    # Print to terminal
    print(report)

    # Save to file
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    filename = REPORT_DIR / f"daily_report_{date_str}.txt"
    with open(filename, "w") as f:
        f.write(report)

    print(f"\nSaved to: {filename}")


if __name__ == "__main__":
    main()
