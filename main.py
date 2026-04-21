"""
Entry point for the VWAP Reversion Engine.

Runs an infinite loop that fires one analysis cycle per interval,
aligned to wall-clock boundaries so timing drift from long-running
iterations does not accumulate across cycles.
"""

import time

from config.settings import CYCLE_INTERVAL_SEC, TARGET_SYMBOLS
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


def main() -> None:
    """Run the engine loop until interrupted with Ctrl-C."""
    logger.info("=== VWAP Reversion Engine Initialized ===")
    logger.info("Trading symbols: %s", ", ".join(TARGET_SYMBOLS))

    try:
        validate_environment()
    except ConfigurationError:
        return

    try:
        while True:
            run_bot_iteration(symbols=TARGET_SYMBOLS)

            sleep_sec = _seconds_until_next_boundary(CYCLE_INTERVAL_SEC)
            logger.info(
                "Cycle complete -- sleeping %.0f s until next boundary.", sleep_sec
            )
            time.sleep(sleep_sec)

    except KeyboardInterrupt:
        logger.info("Engine stopped by user -- shutting down.")


if __name__ == "__main__":
    main()
