"""
Pre-flight validation run once at engine startup.

Catches misconfiguration (missing API keys, empty symbol lists) before
the first trading cycle so the operator gets a clear, immediate error
instead of a cryptic 401 five minutes later.
"""

from config.settings import API_KEY, SECRET_KEY, TARGET_SYMBOLS
from utils.exceptions import ConfigurationError
from utils.logger import logger


def validate_environment() -> None:
    """Verify that all required configuration is present and sane.

    Raises:
        ConfigurationError: If any critical setting is missing or invalid.
    """
    errors: list[str] = []

    if not API_KEY:
        errors.append("ALPACA_API_KEY is not set in .env")
    if not SECRET_KEY:
        errors.append("ALPACA_SECRET_KEY is not set in .env")
    if not TARGET_SYMBOLS:
        errors.append("TARGET_SYMBOLS is empty — nothing to scan")

    if errors:
        for msg in errors:
            logger.error("CONFIG ERROR: %s", msg)
        raise ConfigurationError(
            "Startup aborted — fix the above configuration errors."
        )

    logger.info("Environment validated — %d symbols configured.", len(TARGET_SYMBOLS))
