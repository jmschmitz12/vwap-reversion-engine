"""
Entry point for the scanner-aware backtest.

Replays 6 months of trading using dynamically scanned symbols
instead of a hardcoded list.  This validates that the scanner +
strategy combination works on names selected by gap-down criteria
rather than hand-picked tickers.

Usage:
    python run_scanner_backtest.py
"""

from backtest.report import print_report, save_equity_chart
from backtest.scanner_engine import ScannerBacktestEngine
from utils.logger import logger
from utils.validation import validate_environment


def main() -> None:
    """Run the scanner-aware backtest."""
    logger.info("═══ VWAP Reversion Engine — Scanner Backtest ═══")

    try:
        validate_environment()
    except Exception:
        return

    engine = ScannerBacktestEngine(
        starting_capital=100_000,
        lookback_days=180,
        max_candidates=5,
    )

    results = engine.run()
    if results is None:
        logger.error("Scanner backtest failed — no results.")
        return

    print_report(results)
    save_equity_chart(results, filename="equity_curve_scanner.png")

    logger.info("Done. Check reports/equity_curve_scanner.png for the chart.")


if __name__ == "__main__":
    main()
