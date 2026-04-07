"""
Backtest reporting — terminal statistics and equity curve chart.

Consumes a :class:`BacktestResults` object from the engine and
produces a formatted performance summary plus a saved PNG of the
equity curve.
"""

from pathlib import Path

import numpy as np
import pandas as pd

from backtest.engine import BacktestResults, Trade
from utils.logger import logger

REPORTS_DIR = Path("reports")


# ── Statistics ───────────────────────────────────────────────────────────────


def _compute_stats(results: BacktestResults) -> dict:
    """Derive performance metrics from the completed backtest."""
    trades = results.trades
    if not trades:
        return {"error": "No trades executed."}

    pnls = [t.pnl for t in trades]
    pnl_pcts = [t.pnl_percent for t in trades]
    winners = [t for t in trades if t.pnl > 0]
    losers = [t for t in trades if t.pnl <= 0]

    total_pnl = sum(pnls)
    total_return_pct = (results.ending_capital - results.starting_capital) / results.starting_capital

    # Win / loss breakdown
    win_rate = len(winners) / len(trades) if trades else 0
    avg_win = np.mean([t.pnl for t in winners]) if winners else 0
    avg_loss = np.mean([t.pnl for t in losers]) if losers else 0
    avg_win_pct = np.mean([t.pnl_percent for t in winners]) if winners else 0
    avg_loss_pct = np.mean([t.pnl_percent for t in losers]) if losers else 0

    # Profit factor
    gross_profit = sum(t.pnl for t in winners)
    gross_loss = abs(sum(t.pnl for t in losers))
    profit_factor = gross_profit / gross_loss if gross_loss > 0 else float("inf")

    # Max drawdown from equity curve
    max_drawdown_pct = 0.0
    if not results.equity_curve.empty:
        equity = results.equity_curve["equity"]
        running_max = equity.cummax()
        drawdown = (equity - running_max) / running_max
        max_drawdown_pct = drawdown.min()

    # Sharpe ratio (annualized, assuming ~252 trading days)
    if len(pnl_pcts) > 1:
        returns_series = pd.Series(pnl_pcts)
        sharpe = (returns_series.mean() / returns_series.std()) * np.sqrt(252)
    else:
        sharpe = 0.0

    # Holding periods
    holding_mins = [
        (t.exit_time - t.entry_time).total_seconds() / 60 for t in trades
    ]
    avg_hold = np.mean(holding_mins)

    # Exit breakdown
    tp_exits = len([t for t in trades if t.side == "TP"])
    sl_exits = len([t for t in trades if t.side == "SL"])
    trail_exits = len([t for t in trades if t.side == "TRAIL"])
    forced_closes = len([t for t in trades if t.side == "CLOSE"])

    # Trailing stop P&L breakdown
    trail_pnls = [t.pnl for t in trades if t.side == "TRAIL"]
    avg_trail_pnl = sum(trail_pnls) / len(trail_pnls) if trail_pnls else 0

    # Per-symbol breakdown
    symbol_pnl: dict[str, float] = {}
    symbol_count: dict[str, int] = {}
    for t in trades:
        symbol_pnl[t.symbol] = symbol_pnl.get(t.symbol, 0) + t.pnl
        symbol_count[t.symbol] = symbol_count.get(t.symbol, 0) + 1

    return {
        "total_trades": len(trades),
        "winners": len(winners),
        "losers": len(losers),
        "win_rate": win_rate,
        "total_pnl": total_pnl,
        "total_return_pct": total_return_pct,
        "avg_win": avg_win,
        "avg_loss": avg_loss,
        "avg_win_pct": avg_win_pct,
        "avg_loss_pct": avg_loss_pct,
        "best_trade": max(pnls),
        "worst_trade": min(pnls),
        "profit_factor": profit_factor,
        "max_drawdown_pct": max_drawdown_pct,
        "sharpe_ratio": sharpe,
        "avg_hold_minutes": avg_hold,
        "tp_exits": tp_exits,
        "sl_exits": sl_exits,
        "trail_exits": trail_exits,
        "avg_trail_pnl": avg_trail_pnl,
        "forced_closes": forced_closes,
        "symbol_pnl": symbol_pnl,
        "symbol_count": symbol_count,
    }


# ── Terminal Report ──────────────────────────────────────────────────────────


def print_report(results: BacktestResults) -> dict:
    """Print a formatted performance summary to the terminal.

    Returns:
        The computed stats dictionary for further use.
    """
    stats = _compute_stats(results)

    if "error" in stats:
        logger.warning(stats["error"])
        return stats

    divider = "─" * 60

    print(f"\n{divider}")
    print("  VWAP REVERSION ENGINE — BACKTEST RESULTS")
    print(divider)

    print(f"\n  {'Starting Capital:':<28} ${results.starting_capital:>14,.2f}")
    print(f"  {'Ending Capital:':<28} ${results.ending_capital:>14,.2f}")
    print(f"  {'Net P&L:':<28} ${stats['total_pnl']:>14,.2f}")
    print(f"  {'Total Return:':<28} {stats['total_return_pct']:>14.2%}")

    print(f"\n{divider}")
    print("  TRADE STATISTICS")
    print(divider)

    print(f"\n  {'Total Trades:':<28} {stats['total_trades']:>14}")
    print(f"  {'Winners:':<28} {stats['winners']:>14}")
    print(f"  {'Losers:':<28} {stats['losers']:>14}")
    print(f"  {'Win Rate:':<28} {stats['win_rate']:>14.1%}")

    print(f"\n  {'Avg Win:':<28} ${stats['avg_win']:>14,.2f}  ({stats['avg_win_pct']:+.2%})")
    print(f"  {'Avg Loss:':<28} ${stats['avg_loss']:>14,.2f}  ({stats['avg_loss_pct']:+.2%})")
    print(f"  {'Best Trade:':<28} ${stats['best_trade']:>14,.2f}")
    print(f"  {'Worst Trade:':<28} ${stats['worst_trade']:>14,.2f}")

    print(f"\n{divider}")
    print("  RISK METRICS")
    print(divider)

    print(f"\n  {'Profit Factor:':<28} {stats['profit_factor']:>14.2f}")
    print(f"  {'Max Drawdown:':<28} {stats['max_drawdown_pct']:>14.2%}")
    print(f"  {'Sharpe Ratio:':<28} {stats['sharpe_ratio']:>14.2f}")
    print(f"  {'Avg Holding Period:':<28} {stats['avg_hold_minutes']:>11.0f} min")

    print(f"\n{divider}")
    print("  EXIT BREAKDOWN")
    print(divider)

    print(f"\n  {'Take-Profit Exits:':<28} {stats['tp_exits']:>14}")
    print(f"  {'Trailing Stop Exits:':<28} {stats['trail_exits']:>14}")
    if stats['trail_exits'] > 0:
        print(f"  {'Avg Trail P&L:':<28} ${stats['avg_trail_pnl']:>13,.2f}")
    print(f"  {'Stop-Loss Exits:':<28} {stats['sl_exits']:>14}")
    print(f"  {'Forced Closes (EOB):':<28} {stats['forced_closes']:>14}")

    print(f"\n{divider}")
    print("  PER-SYMBOL BREAKDOWN")
    print(divider)

    print(f"\n  {'Symbol':<10} {'Trades':>8} {'P&L':>14}")
    print(f"  {'──────':<10} {'──────':>8} {'─────────':>14}")
    for symbol in sorted(stats["symbol_pnl"].keys()):
        count = stats["symbol_count"][symbol]
        pnl = stats["symbol_pnl"][symbol]
        print(f"  {symbol:<10} {count:>8} ${pnl:>13,.2f}")

    print(f"\n{divider}")

    # Parameters used
    params = results.parameters
    print("  PARAMETERS")
    print(divider)
    print(f"\n  {'Lookback:':<28} {params['lookback_days']} days")
    sym_display = params['symbols'] if isinstance(params['symbols'], str) else ', '.join(params['symbols'])
    print(f"  {'Symbols:':<28} {sym_display}")
    print(f"  {'Allocation / Trade:':<28} {params['allocation_pct']:.0%}")
    print(f"  {'RSI Threshold:':<28} {params['rsi_threshold']}")
    print(f"  {'Max Positions:':<28} {params['max_positions']}")
    if "exits" in params:
        print(f"  {'Exit Strategy:':<28} {params['exits']}")
    else:
        print(f"  {'Take-Profit:':<28} {params['tp_pct']:.1%}")
        print(f"  {'Stop-Loss:':<28} {params['sl_pct']:.1%}")
    if "initial_stop" in params:
        print(f"  {'Initial Stop:':<28} {params['initial_stop']}")
    if "time_filter" in params:
        print(f"  {'Trading Window:':<28} {params['time_filter']}")
    if "cooldown" in params:
        print(f"  {'SL Cooldown:':<28} {params['cooldown']}")
    if "daily_loss_limit" in params:
        print(f"  {'Daily Loss Limit:':<28} {params['daily_loss_limit']}")
    print(f"\n{divider}\n")

    return stats


# ── Equity Curve Chart ───────────────────────────────────────────────────────


def save_equity_chart(results: BacktestResults, filename: str = "equity_curve.png") -> Path | None:
    """Save an equity curve PNG to the reports/ directory.

    Returns:
        Path to the saved file, or ``None`` if the curve is empty.
    """
    if results.equity_curve.empty:
        logger.warning("No equity data to chart.")
        return None

    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        import matplotlib.dates as mdates
        import matplotlib.ticker as mticker
    except ImportError:
        logger.error("matplotlib is required for charting. Install with: pip install matplotlib")
        return None

    REPORTS_DIR.mkdir(exist_ok=True)
    filepath = REPORTS_DIR / filename

    equity = results.equity_curve["equity"]
    running_max = equity.cummax()
    drawdown = (equity - running_max) / running_max * 100

    fig, (ax1, ax2) = plt.subplots(
        2, 1, figsize=(14, 8), height_ratios=[3, 1],
        sharex=True, gridspec_kw={"hspace": 0.08},
    )

    fig.patch.set_facecolor("#1a1a2e")

    # ── Equity curve ─────────────────────────────────────────────────
    ax1.set_facecolor("#1a1a2e")
    ax1.plot(equity.index, equity.values, color="#00d4aa", linewidth=1.5, label="Portfolio Equity")
    ax1.axhline(
        y=results.starting_capital, color="#555577", linestyle="--",
        linewidth=0.8, label=f"Starting Capital (${results.starting_capital:,.0f})",
    )
    ax1.fill_between(
        equity.index, results.starting_capital, equity.values,
        where=equity.values >= results.starting_capital,
        alpha=0.15, color="#00d4aa",
    )
    ax1.fill_between(
        equity.index, results.starting_capital, equity.values,
        where=equity.values < results.starting_capital,
        alpha=0.15, color="#ff4757",
    )

    ax1.set_ylabel("Portfolio Value ($)", color="#cccccc", fontsize=11)
    ax1.yaxis.set_major_formatter(mticker.FuncFormatter(lambda x, _: f"${x:,.0f}"))
    ax1.tick_params(colors="#888888")
    ax1.legend(loc="upper left", fontsize=9, facecolor="#1a1a2e", edgecolor="#333355", labelcolor="#cccccc")
    ax1.grid(True, alpha=0.15, color="#444466")
    ax1.spines["top"].set_visible(False)
    ax1.spines["right"].set_visible(False)
    ax1.spines["left"].set_color("#333355")
    ax1.spines["bottom"].set_color("#333355")

    # Title with key stats
    total_ret = (results.ending_capital - results.starting_capital) / results.starting_capital
    title_color = "#00d4aa" if total_ret >= 0 else "#ff4757"
    ax1.set_title(
        f"VWAP Reversion Engine  │  {total_ret:+.2%} Return  │  "
        f"{len(results.trades)} Trades  │  6-Month Backtest",
        color=title_color, fontsize=13, fontweight="bold", pad=15,
    )

    # ── Drawdown ─────────────────────────────────────────────────────
    ax2.set_facecolor("#1a1a2e")
    ax2.fill_between(drawdown.index, 0, drawdown.values, color="#ff4757", alpha=0.4)
    ax2.plot(drawdown.index, drawdown.values, color="#ff4757", linewidth=0.8)
    ax2.set_ylabel("Drawdown (%)", color="#cccccc", fontsize=11)
    ax2.set_xlabel("Date", color="#cccccc", fontsize=11)
    ax2.tick_params(colors="#888888")
    ax2.grid(True, alpha=0.15, color="#444466")
    ax2.spines["top"].set_visible(False)
    ax2.spines["right"].set_visible(False)
    ax2.spines["left"].set_color("#333355")
    ax2.spines["bottom"].set_color("#333355")
    ax2.xaxis.set_major_formatter(mdates.DateFormatter("%b %Y"))

    plt.savefig(filepath, dpi=150, bbox_inches="tight", facecolor=fig.get_facecolor())
    plt.close(fig)

    logger.info("Equity curve saved to %s", filepath)
    return filepath
