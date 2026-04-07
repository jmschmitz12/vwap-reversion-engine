"""
Custom exception hierarchy for the VWAP Reversion Engine.

A small set of domain-specific exceptions makes error handling in the
bot loop explicit and grep-friendly.  Every exception inherits from
:class:`EngineError` so callers can catch broadly or narrowly.
"""


class EngineError(Exception):
    """Base exception for all engine-related errors."""


class ConfigurationError(EngineError):
    """Raised when required configuration is missing or invalid."""


class MarketDataError(EngineError):
    """Raised when market data cannot be fetched or is malformed."""


class OrderExecutionError(EngineError):
    """Raised when an order submission fails."""


class InsufficientFundsError(EngineError):
    """Raised when buying power is below the required threshold."""
