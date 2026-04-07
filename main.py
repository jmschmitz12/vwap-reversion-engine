"""
Entry point for the VWAP Reversion Engine.

Runs an infinite loop that fires one analysis cycle per interval,
aligned to wall-clock boundaries so timing drift from long-running
iterations does not accumulate across cycles.

When scanner mode is enabled (USE_SCANNER = True in settings), the
engine runs a premarket scan at the start of each trading day to
build a dynamic watchlist.  Otherwise it uses the static
TARGET_SYMBOLS list.
"""

import time
from datetime import date

from config.settings import (
    CYCLE_INTERVAL_SEC,
    SCANNER_MAX_CANDIDATES,
    TARGET_SYMBOLS,
    USE_SCANNER,
)
from src.bot import run_bot_iteration
from utils.exceptions import ConfigurationError
from utils.logger import logger
from utils.validation import validate_environment


def _seconds_until_next_boundary(interval: int) -> float:
    """Return the number of seconds until the next clean clock boundary.

    For a 300-second interval this ensures the bot wakes up at
    :00, :05, :10, ... rather than drifting by the duration of each
    iteration.
    """
    now = time.time()
    elapsed = now % interval
    return interval - elapsed


def _run_premarket_scan() -> list[str]:
    """Execute the premarket scanner and return today's watchlist.

    Falls back to TARGET_SYMBOLS if the scanner returns no candidates
    (e.g. on a day with no significant gap-downs).
    """
    from scanner.premarket import scan_premarket

    logger.info("-- Running premarket scanner --")
    candidates = scan_premarket(max_candidates=SCANNER_MAX_CANDIDATES)

    if candidates:
        symbols = [c.symbol for c in candidates]
        logger.info("Today's watchlist: %s", ", ".join(symbols))
        return symbols

    logger.info("Scanner found no candidates -- falling back to static list.")
    return TARGET_SYMBOLS


def main() -> None:
    """Run the engine loop until interrupted with Ctrl-C."""
    logger.info("=== VWAP Reversion Engine Initialized ===")

    if USE_SCANNER:
        logger.info("Scanner mode: ENABLED -- watchlist updates daily.")
    else:
        logger.info("Scanner mode: DISABLED -- using static symbols: %s", TARGET_SYMBOLS)

    try:
        validate_environment()
    except ConfigurationError:
        return

    today_symbols: list[str] = TARGET_SYMBOLS
    last_scan_date: date | None = None

    try:
        while True:
            current_date = date.today()

            # Re-scan at the start of each new trading day
            if USE_SCANNER and current_date != last_scan_date:
                today_symbols = _run_premarket_scan()
                last_scan_date = current_date

            run_bot_iteration(symbols=today_symbols)

            sleep_sec = _seconds_until_next_boundary(CYCLE_INTERVAL_SEC)
            logger.info(
                "Cycle complete -- sleeping %.0f s until next boundary.", sleep_sec
            )
            time.sleep(sleep_sec)

    except KeyboardInterrupt:
        logger.info("Engine stopped by user -- shutting down.")


if __name__ == "__main__":
    main()
