"""
Centralized logging configuration.

Produces timestamped output to both stdout and a rotating log file.
The module-level `logger` instance is imported throughout the project
so every module shares a single, consistently formatted logger.
"""

import logging
import sys
from pathlib import Path

LOG_DIR = Path("logs")
LOG_FILE = LOG_DIR / "engine.log"
LOG_FORMAT = "%(asctime)s | %(levelname)-8s | %(message)s"


def _setup_logger() -> logging.Logger:
    """Build and return the application-wide logger.

    Called once at import time.  A guard clause prevents duplicate
    handlers if the module is accidentally re-imported.
    """
    _logger = logging.getLogger("VWAPReversionEngine")

    if _logger.handlers:
        return _logger

    _logger.setLevel(logging.INFO)
    formatter = logging.Formatter(LOG_FORMAT)

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    _logger.addHandler(console_handler)

    # File handler (creates logs/ directory if missing)
    LOG_DIR.mkdir(exist_ok=True)
    file_handler = logging.FileHandler(LOG_FILE)
    file_handler.setFormatter(formatter)
    _logger.addHandler(file_handler)

    return _logger


logger: logging.Logger = _setup_logger()
