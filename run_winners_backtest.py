"""
Run the realistic backtest on a configurable subset of symbols.

Tests multiple curated symbol lists against the three friction profiles
to find out whether a trimmed universe actually has survivable edge.

Usage:
    python run_winners_backtest.py
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


# ── Candidate Universes ──────────────────────────────────────────────────────
# Derived from the per-symbol breakdown of the full-universe backtest.
# Each list is a progressively stricter filter for "symbols that work."

UNIVERSES: dict[str, list[str]] = {
    # Tier 1: The only names that were net positive across ALL three profiles
    "CORE_3": ["GOOGL", "META", "AAPL"],

    # Tier 2: Add AMZN (positive all three but high variance)
    "CORE_4": ["GOOGL", "META", "AAPL", "AMZN"],

    # Tier 3: Add the best "sensitive to friction" names
    "EXPANDED_6": ["GOOGL", "META", "AAPL", "AMZN", "AVGO", "LLY"],

    # Tier 4: Everything that wasn't a consistent loser
    "NO_LOSERS_9": [
        "GOOGL", "META", "AAPL", "AMZN",
        "AVGO", "LLY", "NFLX", "QCOM", "AMD",
    ],
}


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
    max_drawdown: float
    sharpe: float
    symbol_pnl: dict[str, float]
    symbol_trades: dict[str, int]


def _compute_stats(results: BacktestResults) -> ProfileStats:
    """Extract key performance metrics from a backtest run."""
    trades = results.trades
    signals = results.signal_stats.total_signals
    fill_rate = results.signal_stats.filled / signals if signals > 0 else 0.0

    if not trades:
        return ProfileStats(
            profile=results.profile,
            signals=signals, fill_rate=fill_rate,
            trades=0, win_rate=0.0, total_pnl=0.0, total_return=0.0,
            profit_factor=0.0, max_drawdown=0.0, sharpe=0.0,
            symbol_pnl={}, symbol_trades={},
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
        max_drawdown=max_dd,
        sharpe=sharpe,
        symbol_pnl=results.symbol_pnl,
        symbol_trades=results.symbol_trades,
    )


def _print_universe_result(name: str, symbols: list[str], stats_list: list[ProfileStats]) -> None:
    """Print the three-profile table for one universe."""
    divider = "═" * 95
    row_div = "─" * 95

    print(f"\n{divider}")
    print(f"  UNIVERSE: {name}  ({len(symbols)} symbols)")
    print(f"  {', '.join(symbols)}")
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
    row("Max Drawdown", [f"{s.max_drawdown:.2%}" for s in stats_list])
    row("Sharpe Ratio", [f"{s.sharpe:.2f}" for s in stats_list])


def _print_final_summary(results_by_universe: dict[str, list[ProfileStats]]) -> None:
    """Side-by-side summary across all universes (moderate profile only)."""
    divider = "═" * 95
    row_div = "─" * 95

    print(f"\n{divider}")
    print("  SIDE-BY-SIDE SUMMARY (moderate profile)")
    print(divider)

    print(f"\n  {'Universe':<15} {'Trades':>8} {'Fill%':>8} {'Return':>10} {'PF':>8} {'Sharpe':>8} {'MaxDD':>8}")
    print(f"  {row_div}")

    for name, stats_list in results_by_universe.items():
        mod = next((s for s in stats_list if s.profile == RealismProfile.MODERATE), None)
        if mod is None:
            continue
        print(
            f"  {name:<15} {mod.trades:>8} {mod.fill_rate:>7.1%} "
            f"{mod.total_return:>+9.2%} {mod.profit_factor:>8.2f} "
            f"{mod.sharpe:>8.2f} {mod.max_drawdown:>7.2%}"
        )

    print(f"\n{divider}")
    print("  CONSERVATIVE PROFILE — THE REAL TEST")
    print(divider)
    print(f"\n  {'Universe':<15} {'Trades':>8} {'Fill%':>8} {'Return':>10} {'PF':>8} {'Sharpe':>8} {'MaxDD':>8}")
    print(f"  {row_div}")

    for name, stats_list in results_by_universe.items():
        cons = next((s for s in stats_list if s.profile == RealismProfile.CONSERVATIVE), None)
        if cons is None:
            continue
        print(
            f"  {name:<15} {cons.trades:>8} {cons.fill_rate:>7.1%} "
            f"{cons.total_return:>+9.2%} {cons.profit_factor:>8.2f} "
            f"{cons.sharpe:>8.2f} {cons.max_drawdown:>7.2%}"
        )

    print(f"\n{divider}")
    print("  VERDICT")
    print(divider)
    print()

    # Find the best universe under conservative assumptions
    best_name = None
    best_pf = 0.0
    for name, stats_list in results_by_universe.items():
        cons = next((s for s in stats_list if s.profile == RealismProfile.CONSERVATIVE), None)
        if cons and cons.profit_factor > best_pf:
            best_pf = cons.profit_factor
            best_name = name

    if best_name is None or best_pf < 1.0:
        print("  No universe produces PF > 1.0 under conservative assumptions.")
        print("  The strategy does not have a survivable edge on any subset tested.")
        print("  Recommendation: do not trade this strategy live.")
    elif best_pf < 1.3:
        print(f"  Best universe: {best_name} (conservative PF: {best_pf:.2f})")
        print("  This is a marginal edge — trading it live would require discipline")
        print("  to stop if real-world results deviate from backtest.")
    else:
        print(f"  Best universe: {best_name} (conservative PF: {best_pf:.2f})")
        print("  This universe shows a credible edge even under pessimistic")
        print("  assumptions. Worth considering for live deployment after")
        print("  additional validation on out-of-sample data.")

    print(f"\n{divider}\n")


def main() -> None:
    logger.info("═══ VWAP Reversion Engine — Winners-Only Backtest ═══")

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

    results_by_universe: dict[str, list[ProfileStats]] = {}

    for uni_name, symbols in UNIVERSES.items():
        print(f"\n{'='*95}")
        print(f"  TESTING UNIVERSE: {uni_name}")
        print(f"{'='*95}")

        stats_list: list[ProfileStats] = []
        for profile in profiles:
            print(f"  Running {profile.value.upper()} profile on {uni_name} ...")
            engine = RealisticBacktestEngine(
                profile=profile,
                starting_capital=starting_capital,
                lookback_days=lookback_days,
                symbols=symbols,
            )
            results = engine.run()
            if results is None:
                logger.error("[%s / %s] Failed.", uni_name, profile.value)
                continue
            stats_list.append(_compute_stats(results))

        if len(stats_list) == 3:
            _print_universe_result(uni_name, symbols, stats_list)
            results_by_universe[uni_name] = stats_list

    if results_by_universe:
        _print_final_summary(results_by_universe)


if __name__ == "__main__":
    main()
