"""
Trade journal — persistent CSV log of every order the engine submits.

Each row captures the timestamp, symbol, quantity, entry price, exit
targets, and order ID.  This gives you an auditable record for
performance review, tax reporting, and strategy refinement without
needing to dig through Alpaca's dashboard.
"""

import csv
from datetime import datetime, timezone
from pathlib import Path

from utils.logger import logger

JOURNAL_DIR = Path("logs")
JOURNAL_FILE = JOURNAL_DIR / "trade_journal.csv"

_FIELDNAMES = [
    "timestamp",
    "symbol",
    "side",
    "qty",
    "entry_price",
    "take_profit",
    "stop_loss",
    "order_id",
]


def _ensure_journal_exists() -> None:
    """Create the journal CSV with headers if it doesn't already exist."""
    JOURNAL_DIR.mkdir(exist_ok=True)
    if not JOURNAL_FILE.exists():
        with open(JOURNAL_FILE, "w", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
            writer.writeheader()


def record_trade(
    symbol: str,
    side: str,
    qty: int,
    entry_price: float,
    take_profit: float,
    stop_loss: float,
    order_id: str,
) -> None:
    """Append a single trade record to the journal CSV.

    Args:
        symbol:       Ticker traded.
        side:         ``"BUY"`` or ``"SELL"``.
        qty:          Number of shares.
        entry_price:  Approximate fill price (last close at signal time).
        take_profit:  Limit price on the take-profit leg.
        stop_loss:    Stop price on the stop-loss leg.
        order_id:     Alpaca order ID for cross-referencing.
    """
    _ensure_journal_exists()

    row = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol,
        "side": side,
        "qty": qty,
        "entry_price": entry_price,
        "take_profit": take_profit,
        "stop_loss": stop_loss,
        "order_id": order_id,
    }

    try:
        with open(JOURNAL_FILE, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=_FIELDNAMES)
            writer.writerow(row)
        logger.info("Trade journaled: %s %d × %s @ $%.2f", side, qty, symbol, entry_price)
    except OSError as exc:
        logger.error("Failed to write trade journal: %s", exc)
