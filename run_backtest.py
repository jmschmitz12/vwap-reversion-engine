"""
Entry point for running a backtest of the VWAP Reversion Engine.

Usage:
    python run_backtest.py
"""

from backtest.engine import BacktestEngine
from backtest.report import print_report, save_equity_chart
from utils.logger import logger
from utils.validation import validate_environment


def main() -> None:
    """Run a 6-month backtest and output results."""
    logger.info("═══ VWAP Reversion Engine — Backtest Mode ═══")

    try:
        validate_environment()
    except Exception:
        return

    engine = BacktestEngine(
        starting_capital=100_000,
        lookback_days=180,
    )

    results = engine.run()
    if results is None:
        logger.error("Backtest failed — no results produced.")
        return

    print_report(results)
    save_equity_chart(results)

    logger.info("Done. Check reports/equity_curve.png for the chart.")


if __name__ == "__main__":
    main()
