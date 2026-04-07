"""
Time-of-day analysis for the VWAP Reversion Engine.

Runs the standard backtest on the fixed symbol list and then breaks
down performance by hour of day.  This reveals which trading windows
produce the strongest edge and which are dead weight or actively
harmful.

Usage:
    python run_time_analysis.py
"""

from collections import defaultdict

from backtest.engine import BacktestEngine
from utils.logger import logger
from utils.validation import validate_environment


def main() -> None:
    logger.info("═══ VWAP Reversion Engine — Time-of-Day Analysis ═══")

    try:
        validate_environment()
    except Exception:
        return

    engine = BacktestEngine(
        starting_capital=100_000,
        lookback_days=180,
        use_time_filter=False,
    )

    results = engine.run()
    if results is None or not results.trades:
        logger.error("No trades to analyze.")
        return

    # ── Bucket trades by entry hour ──────────────────────────────────
    hourly: dict[int, dict] = defaultdict(lambda: {
        "trades": 0, "wins": 0, "losses": 0,
        "pnl": 0.0, "win_pnls": [], "loss_pnls": [],
    })

    for t in results.trades:
        hour = t.entry_time.hour
        bucket = hourly[hour]
        bucket["trades"] += 1
        bucket["pnl"] += t.pnl

        if t.pnl > 0:
            bucket["wins"] += 1
            bucket["win_pnls"].append(t.pnl)
        else:
            bucket["losses"] += 1
            bucket["loss_pnls"].append(t.pnl)

    # ── Print results ────────────────────────────────────────────────
    divider = "─" * 90

    print(f"\n{divider}")
    print("  PERFORMANCE BY HOUR OF DAY (Eastern)")
    print(f"{divider}\n")
    print(
        f"  {'Hour':<12} {'Trades':>7} {'Wins':>6} {'Losses':>7} "
        f"{'Win%':>7} {'P&L':>12} {'Avg Win':>10} {'Avg Loss':>10} {'PF':>7}"
    )
    print(f"  {'─' * 82}")

    sorted_hours = sorted(hourly.keys())

    total_positive_pnl = 0.0
    total_negative_pnl = 0.0

    for hour in sorted_hours:
        b = hourly[hour]
        win_rate = b["wins"] / b["trades"] if b["trades"] > 0 else 0
        avg_win = sum(b["win_pnls"]) / len(b["win_pnls"]) if b["win_pnls"] else 0
        avg_loss = sum(b["loss_pnls"]) / len(b["loss_pnls"]) if b["loss_pnls"] else 0
        gross_win = sum(b["win_pnls"])
        gross_loss = abs(sum(b["loss_pnls"]))
        pf = gross_win / gross_loss if gross_loss > 0 else float("inf")

        if b["pnl"] >= 0:
            total_positive_pnl += b["pnl"]
        else:
            total_negative_pnl += b["pnl"]

        # Flag profitable hours
        marker = "  ✓" if b["pnl"] > 0 and b["trades"] >= 10 else ""
        if b["pnl"] < 0 and b["trades"] >= 10:
            marker = "  ✗"

        time_label = f"{hour:02d}:00–{hour:02d}:59"

        print(
            f"  {time_label:<12} {b['trades']:>7} {b['wins']:>6} {b['losses']:>7} "
            f"{win_rate:>6.1%} ${b['pnl']:>11,.2f} "
            f"${avg_win:>9,.2f} ${avg_loss:>9,.2f} {pf:>7.2f}{marker}"
        )

    print(f"  {'─' * 82}")

    total_trades = sum(b["trades"] for b in hourly.values())
    total_wins = sum(b["wins"] for b in hourly.values())
    total_pnl = sum(b["pnl"] for b in hourly.values())
    overall_wr = total_wins / total_trades if total_trades > 0 else 0

    print(
        f"  {'TOTAL':<12} {total_trades:>7} {total_wins:>6} "
        f"{total_trades - total_wins:>7} {overall_wr:>6.1%} "
        f"${total_pnl:>11,.2f}"
    )

    # ── Summary ──────────────────────────────────────────────────────
    print(f"\n{divider}")
    print("  RECOMMENDATION")
    print(f"{divider}\n")

    profitable_hours = [
        h for h in sorted_hours
        if hourly[h]["pnl"] > 0 and hourly[h]["trades"] >= 10
    ]
    losing_hours = [
        h for h in sorted_hours
        if hourly[h]["pnl"] < 0 and hourly[h]["trades"] >= 10
    ]

    if profitable_hours:
        p_pnl = sum(hourly[h]["pnl"] for h in profitable_hours)
        p_trades = sum(hourly[h]["trades"] for h in profitable_hours)
        p_wins = sum(hourly[h]["wins"] for h in profitable_hours)
        p_wr = p_wins / p_trades if p_trades > 0 else 0
        hours_str = ", ".join(f"{h:02d}:00" for h in profitable_hours)
        print(f"  Profitable hours (✓):  {hours_str}")
        print(f"  Combined:  {p_trades} trades | {p_wr:.1%} win rate | ${p_pnl:,.2f} P&L")

    if losing_hours:
        l_pnl = sum(hourly[h]["pnl"] for h in losing_hours)
        l_trades = sum(hourly[h]["trades"] for h in losing_hours)
        hours_str = ", ".join(f"{h:02d}:00" for h in losing_hours)
        print(f"\n  Losing hours (✗):      {hours_str}")
        print(f"  Combined:  {l_trades} trades | ${l_pnl:,.2f} P&L")
        print(f"\n  Filtering these out would have saved ${abs(l_pnl):,.2f}")

    if profitable_hours:
        filtered_pnl = sum(hourly[h]["pnl"] for h in profitable_hours)
        filtered_trades = sum(hourly[h]["trades"] for h in profitable_hours)
        filtered_return = filtered_pnl / 100_000
        print(f"\n  Projected return (profitable hours only): {filtered_return:+.2%} on {filtered_trades} trades")

    print(f"\n{divider}\n")


if __name__ == "__main__":
    main()
