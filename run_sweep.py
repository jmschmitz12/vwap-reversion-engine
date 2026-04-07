"""
Parameter sweep for the VWAP Reversion Engine.

Tests multiple combinations of RSI threshold, take-profit/stop-loss
ratios, and symbol universes against the same 6-month dataset.
Outputs a ranked comparison table so you can see which configuration
produces the strongest edge before committing to live trading.

Usage:
    python run_sweep.py
"""

import itertools
from dataclasses import dataclass

from backtest.engine import BacktestEngine
from backtest.report import print_report, save_equity_chart
from utils.logger import logger
from utils.validation import validate_environment


# ── Sweep Configuration ──────────────────────────────────────────────────────

# Symbol universes to test
UNIVERSES: dict[str, list[str]] = {
    "ALL_7": ["NVDA", "META", "MSFT", "TSLA", "AMZN", "QQQ", "SPY"],
    "NO_ETFS": ["NVDA", "META", "MSFT", "TSLA", "AMZN"],
    "TOP_3": ["NVDA", "META", "AMZN"],
}

# RSI thresholds to test
RSI_LEVELS: list[int] = [25, 28, 30]

# (take_profit, stop_loss) pairs to test
TP_SL_PAIRS: list[tuple[float, float]] = [
    (0.005, 0.005),   # 1:1  (current)
    (0.007, 0.005),   # 1.4:1
    (0.010, 0.005),   # 2:1
    (0.010, 0.007),   # 1.4:1 (wider)
]


@dataclass
class SweepResult:
    """One row of the sweep comparison table."""

    label: str
    universe: str
    rsi: int
    tp: float
    sl: float
    trades: int
    win_rate: float
    total_pnl: float
    total_return: float
    profit_factor: float
    max_drawdown: float
    sharpe: float


def run_sweep() -> None:
    """Run all parameter combinations and print a ranked comparison."""
    logger.info("═══ VWAP Reversion Engine — Parameter Sweep ═══")

    try:
        validate_environment()
    except Exception:
        return

    combos = list(itertools.product(UNIVERSES.items(), RSI_LEVELS, TP_SL_PAIRS))
    total = len(combos)
    results: list[SweepResult] = []

    print(f"\nRunning {total} configurations...\n")

    for i, ((uni_name, symbols), rsi, (tp, sl)) in enumerate(combos, 1):
        label = f"{uni_name} | RSI<{rsi} | TP={tp:.1%}/SL={sl:.1%}"
        print(f"  [{i:>2}/{total}] {label}")

        engine = BacktestEngine(
            starting_capital=100_000,
            lookback_days=180,
            symbols=symbols,
            tp_pct=tp,
            sl_pct=sl,
            rsi_threshold=rsi,
        )

        bt = engine.run()
        if bt is None or not bt.trades:
            print(f"         → No trades. Skipping.")
            continue

        # Compute quick stats inline
        pnls = [t.pnl for t in bt.trades]
        winners = [t for t in bt.trades if t.pnl > 0]
        losers = [t for t in bt.trades if t.pnl <= 0]
        gross_profit = sum(t.pnl for t in winners)
        gross_loss = abs(sum(t.pnl for t in losers))
        pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")
        total_pnl = sum(pnls)
        total_ret = (bt.ending_capital - bt.starting_capital) / bt.starting_capital
        win_rate = len(winners) / len(bt.trades)

        # Max drawdown
        max_dd = 0.0
        if not bt.equity_curve.empty:
            eq = bt.equity_curve["equity"]
            dd = (eq - eq.cummax()) / eq.cummax()
            max_dd = dd.min()

        # Sharpe
        import numpy as np
        import pandas as pd

        pnl_pcts = [t.pnl_percent for t in bt.trades]
        if len(pnl_pcts) > 1:
            s = pd.Series(pnl_pcts)
            sharpe = (s.mean() / s.std()) * np.sqrt(252)
        else:
            sharpe = 0.0

        sr = SweepResult(
            label=label,
            universe=uni_name,
            rsi=rsi,
            tp=tp,
            sl=sl,
            trades=len(bt.trades),
            win_rate=win_rate,
            total_pnl=total_pnl,
            total_return=total_ret,
            profit_factor=pf,
            max_drawdown=max_dd,
            sharpe=sharpe,
        )
        results.append(sr)
        print(f"         → {len(bt.trades)} trades | {total_ret:+.2%} | PF={pf:.2f} | Sharpe={sharpe:.2f}")

    if not results:
        print("\nNo valid results produced.")
        return

    # ── Ranked Table ─────────────────────────────────────────────────
    # Sort by profit factor (primary), then Sharpe (secondary)
    results.sort(key=lambda r: (r.profit_factor, r.sharpe), reverse=True)

    divider = "═" * 115
    header_div = "─" * 115

    print(f"\n{divider}")
    print("  PARAMETER SWEEP RESULTS — Ranked by Profit Factor")
    print(divider)
    print(
        f"\n  {'#':>3}  {'Universe':<10} {'RSI':>4} {'TP/SL':>10} "
        f"{'Trades':>7} {'Win%':>7} {'Return':>9} {'P&L':>12} "
        f"{'PF':>7} {'MaxDD':>8} {'Sharpe':>8}"
    )
    print(f"  {header_div}")

    for rank, r in enumerate(results, 1):
        tp_sl_str = f"{r.tp:.1%}/{r.sl:.1%}"
        print(
            f"  {rank:>3}  {r.universe:<10} {r.rsi:>4} {tp_sl_str:>10} "
            f"{r.trades:>7} {r.win_rate:>6.1%} {r.total_return:>+8.2%} "
            f"${r.total_pnl:>11,.2f} {r.profit_factor:>7.2f} "
            f"{r.max_drawdown:>7.2%} {r.sharpe:>8.2f}"
        )

        if rank == 3:
            print(f"  {header_div}")

    print(f"\n{divider}")

    # ── Save the best config's equity chart ───────────────────────────
    best = results[0]
    print(f"\n  Best configuration: {best.label}")
    print(f"  Re-running for full report and equity chart...\n")

    engine = BacktestEngine(
        starting_capital=100_000,
        lookback_days=180,
        symbols=UNIVERSES[best.universe],
        tp_pct=best.tp,
        sl_pct=best.sl,
        rsi_threshold=best.rsi,
    )
    bt = engine.run()
    if bt:
        print_report(bt)
        save_equity_chart(bt, filename="equity_curve_best.png")


if __name__ == "__main__":
    run_sweep()
