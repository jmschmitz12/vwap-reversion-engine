"""
Run all three realism profiles side-by-side and compare results.

This tells you exactly how much of your "edge" survives when
realistic frictions are modeled.

Usage:
    python run_realistic_backtest.py
"""

from dataclasses import dataclass

import numpy as np
import pandas as pd

from backtest.realistic_engine import (
    BacktestResults,
    RealismProfile,
    RealisticBacktestEngine,
)
from utils.logger import logger
from utils.validation import validate_environment


@dataclass
class ProfileStats:
    profile: RealismProfile
    signals: int
    fill_rate: float
    trades: int
    win_rate: float
    total_pnl: float
    total_return: float
    profit_factor: float
    avg_win: float
    avg_loss: float
    max_drawdown: float
    sharpe: float
    symbol_pnl: dict[str, float]
    symbol_trades: dict[str, int]


def _compute_stats(results: BacktestResults) -> ProfileStats:
    """Extract key performance metrics from a backtest run."""
    trades = results.trades
    signals = results.signal_stats.total_signals
    fill_rate = (
        results.signal_stats.filled / signals if signals > 0 else 0.0
    )

    if not trades:
        return ProfileStats(
            profile=results.profile,
            signals=signals,
            fill_rate=fill_rate,
            trades=0,
            win_rate=0.0,
            total_pnl=0.0,
            total_return=0.0,
            profit_factor=0.0,
            avg_win=0.0,
            avg_loss=0.0,
            max_drawdown=0.0,
            sharpe=0.0,
            symbol_pnl={},
            symbol_trades={},
        )

    winners = [t for t in trades if t.pnl > 0]
    losers = [t for t in trades if t.pnl <= 0]
    gross_profit = sum(t.pnl for t in winners)
    gross_loss = abs(sum(t.pnl for t in losers))
    pf = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    pnl_pcts = [t.pnl_percent for t in trades]
    sharpe = 0.0
    if len(pnl_pcts) > 1:
        s = pd.Series(pnl_pcts)
        if s.std() > 0:
            sharpe = (s.mean() / s.std()) * np.sqrt(252)

    max_dd = 0.0
    if not results.equity_curve.empty:
        eq = results.equity_curve["equity"]
        dd = (eq - eq.cummax()) / eq.cummax()
        max_dd = dd.min()

    return ProfileStats(
        profile=results.profile,
        signals=signals,
        fill_rate=fill_rate,
        trades=len(trades),
        win_rate=len(winners) / len(trades),
        total_pnl=sum(t.pnl for t in trades),
        total_return=(results.ending_capital - results.starting_capital) / results.starting_capital,
        profit_factor=pf,
        avg_win=np.mean([t.pnl for t in winners]) if winners else 0.0,
        avg_loss=np.mean([t.pnl for t in losers]) if losers else 0.0,
        max_drawdown=max_dd,
        sharpe=sharpe,
        symbol_pnl=results.symbol_pnl,
        symbol_trades=results.symbol_trades,
    )


def _print_comparison_table(stats_list: list[ProfileStats]) -> None:
    """Print the three profiles side by side."""
    divider = "═" * 95
    row_div = "─" * 95

    print(f"\n{divider}")
    print("  REALISTIC BACKTEST COMPARISON — all 15 production symbols, 180 days")
    print(divider)

    labels = [s.profile.value.upper() for s in stats_list]
    print(f"\n  {'Metric':<28} {labels[0]:>20} {labels[1]:>20} {labels[2]:>20}")
    print(f"  {row_div}")

    def row(label: str, values: list[str]) -> None:
        print(f"  {label:<28} {values[0]:>20} {values[1]:>20} {values[2]:>20}")

    row("Signals Fired", [f"{s.signals}" for s in stats_list])
    row("Fill Rate", [f"{s.fill_rate:.1%}" for s in stats_list])
    row("Trades Executed", [f"{s.trades}" for s in stats_list])
    print(f"  {row_div}")

    row("Total P&L", [f"${s.total_pnl:+,.2f}" for s in stats_list])
    row("Total Return", [f"{s.total_return:+.2%}" for s in stats_list])
    row("Profit Factor", [f"{s.profit_factor:.2f}" for s in stats_list])
    print(f"  {row_div}")

    row("Win Rate", [f"{s.win_rate:.1%}" for s in stats_list])
    row("Avg Win", [f"${s.avg_win:+,.2f}" for s in stats_list])
    row("Avg Loss", [f"${s.avg_loss:+,.2f}" for s in stats_list])
    print(f"  {row_div}")

    row("Max Drawdown", [f"{s.max_drawdown:.2%}" for s in stats_list])
    row("Sharpe Ratio", [f"{s.sharpe:.2f}" for s in stats_list])

    print(f"\n{divider}")


def _print_per_symbol_breakdown(stats_list: list[ProfileStats]) -> None:
    """Per-symbol P&L for each profile."""
    all_symbols: set[str] = set()
    for s in stats_list:
        all_symbols.update(s.symbol_pnl.keys())

    if not all_symbols:
        return

    divider = "═" * 95
    row_div = "─" * 95
    print(f"\n{divider}")
    print("  PER-SYMBOL BREAKDOWN")
    print(divider)

    labels = [s.profile.value.upper() for s in stats_list]
    print(f"\n  {'Symbol':<10} {labels[0]:>25} {labels[1]:>25} {labels[2]:>25}")
    print(f"  {row_div}")

    # Sort by moderate-profile P&L descending
    moderate = next((s for s in stats_list if s.profile == RealismProfile.MODERATE), stats_list[0])
    symbol_order = sorted(
        all_symbols,
        key=lambda s: moderate.symbol_pnl.get(s, 0.0),
        reverse=True,
    )

    for symbol in symbol_order:
        cells = []
        for s in stats_list:
            pnl = s.symbol_pnl.get(symbol, 0.0)
            trades = s.symbol_trades.get(symbol, 0)
            if trades > 0:
                cells.append(f"${pnl:+,.0f} ({trades} tr)")
            else:
                cells.append("—")
        print(f"  {symbol:<10} {cells[0]:>25} {cells[1]:>25} {cells[2]:>25}")

    print(f"\n{divider}")


def _print_verdict(stats_list: list[ProfileStats]) -> None:
    """Interpret the numbers in plain English."""
    idealized = next(s for s in stats_list if s.profile == RealismProfile.IDEALIZED)
    moderate = next(s for s in stats_list if s.profile == RealismProfile.MODERATE)
    conservative = next(s for s in stats_list if s.profile == RealismProfile.CONSERVATIVE)

    divider = "═" * 95
    print(f"\n{divider}")
    print("  VERDICT")
    print(divider)
    print()

    print(f"  The idealized backtest (no friction) reports {idealized.total_return:+.2%} return,")
    print(f"  {idealized.trades} trades, and PF {idealized.profit_factor:.2f}.")
    print()

    # How much is lost to friction?
    edge_moderate = moderate.total_return - idealized.total_return
    edge_conservative = conservative.total_return - idealized.total_return

    print(f"  With MODERATE frictions (matches observed live behavior):")
    print(f"    - Fill rate drops to {moderate.fill_rate:.1%}")
    print(f"    - Return becomes {moderate.total_return:+.2%} ({edge_moderate:+.2%} vs idealized)")
    print(f"    - Profit factor: {moderate.profit_factor:.2f}")
    print()

    print(f"  With CONSERVATIVE frictions (worst reasonable case):")
    print(f"    - Fill rate drops to {conservative.fill_rate:.1%}")
    print(f"    - Return becomes {conservative.total_return:+.2%} ({edge_conservative:+.2%} vs idealized)")
    print(f"    - Profit factor: {conservative.profit_factor:.2f}")
    print()

    # Final assessment
    print(f"  BOTTOM LINE:")
    if moderate.profit_factor > 1.3 and conservative.profit_factor > 1.1:
        print(f"    Strategy has REAL EDGE that survives friction.")
        print(f"    Moderate PF: {moderate.profit_factor:.2f} | Conservative PF: {conservative.profit_factor:.2f}")
    elif moderate.profit_factor > 1.1 and conservative.profit_factor > 0.95:
        print(f"    Strategy has MARGINAL EDGE — works in moderate case,")
        print(f"    barely breaks even under conservative assumptions.")
        print(f"    Moderate PF: {moderate.profit_factor:.2f} | Conservative PF: {conservative.profit_factor:.2f}")
    else:
        print(f"    Strategy does NOT have survivable edge.")
        print(f"    Moderate PF: {moderate.profit_factor:.2f} | Conservative PF: {conservative.profit_factor:.2f}")
        print(f"    Recommendation: do not trade live with this configuration.")

    print(f"\n{divider}\n")


def main() -> None:
    logger.info("═══ VWAP Reversion Engine — Realistic Backtest Comparison ═══")

    try:
        validate_environment()
    except Exception:
        return

    starting_capital = 25_000.0
    lookback_days = 180

    profiles = [
        RealismProfile.IDEALIZED,
        RealismProfile.MODERATE,
        RealismProfile.CONSERVATIVE,
    ]

    all_stats: list[ProfileStats] = []

    for profile in profiles:
        print(f"\n  Running {profile.value.upper()} profile ...")
        engine = RealisticBacktestEngine(
            profile=profile,
            starting_capital=starting_capital,
            lookback_days=lookback_days,
        )
        results = engine.run()
        if results is None:
            logger.error("[%s] Failed to produce results.", profile.value)
            continue
        stats = _compute_stats(results)
        all_stats.append(stats)

    if len(all_stats) != 3:
        logger.error("Not all profiles completed successfully. Aborting comparison.")
        return

    _print_comparison_table(all_stats)
    _print_per_symbol_breakdown(all_stats)
    _print_verdict(all_stats)


if __name__ == "__main__":
    main()
