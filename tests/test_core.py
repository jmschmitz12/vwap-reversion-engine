"""
Unit tests for the VWAP Reversion Engine.

These tests cover pure-logic functions that don't require a live Alpaca
connection.  Integration tests against the paper trading API should be
added separately once CI infrastructure is in place.

Run with:  pytest tests/
"""

import csv
from pathlib import Path
from unittest.mock import patch

import pytest

from src.bot import _calculate_position_size
from utils.exceptions import ConfigurationError
from utils.journal import JOURNAL_FILE, _FIELDNAMES, record_trade
from utils.validation import validate_environment


# ── Position Sizing ──────────────────────────────────────────────────────────


class TestCalculatePositionSize:
    """Verify share-count math under various account/price scenarios."""

    def test_standard_allocation(self) -> None:
        qty = _calculate_position_size(buying_power=100_000, current_price=250.0)
        # 10% of 100k = 10k → 10_000 / 250 = 40 shares
        assert qty == 40

    def test_rounds_down_to_whole_shares(self) -> None:
        qty = _calculate_position_size(buying_power=10_000, current_price=333.0)
        # 10% of 10k = 1000 → 1000 / 333 = 3.003 → floor to 3
        assert qty == 3

    def test_returns_zero_when_allocation_too_small(self) -> None:
        qty = _calculate_position_size(buying_power=500, current_price=600.0)
        # 10% of 500 = 50 → 50 / 600 = 0.08 → floor to 0
        assert qty == 0

    def test_custom_allocation_percent(self) -> None:
        qty = _calculate_position_size(
            buying_power=50_000, current_price=100.0, allocation_percent=0.20
        )
        # 20% of 50k = 10k → 10_000 / 100 = 100 shares
        assert qty == 100

    def test_high_priced_stock(self) -> None:
        qty = _calculate_position_size(buying_power=25_000, current_price=4_500.0)
        # 10% of 25k = 2500 → 2500 / 4500 = 0.55 → floor to 0
        assert qty == 0


# ── Environment Validation ───────────────────────────────────────────────────


class TestValidateEnvironment:
    """Verify startup validation catches missing config."""

    @patch("utils.validation.API_KEY", "")
    @patch("utils.validation.SECRET_KEY", "valid_secret")
    def test_missing_api_key_raises(self) -> None:
        with pytest.raises(ConfigurationError):
            validate_environment()

    @patch("utils.validation.API_KEY", "valid_key")
    @patch("utils.validation.SECRET_KEY", "")
    def test_missing_secret_key_raises(self) -> None:
        with pytest.raises(ConfigurationError):
            validate_environment()

    @patch("utils.validation.TARGET_SYMBOLS", [])
    @patch("utils.validation.API_KEY", "valid_key")
    @patch("utils.validation.SECRET_KEY", "valid_secret")
    def test_empty_symbols_raises(self) -> None:
        with pytest.raises(ConfigurationError):
            validate_environment()

    @patch("utils.validation.API_KEY", "valid_key")
    @patch("utils.validation.SECRET_KEY", "valid_secret")
    @patch("utils.validation.TARGET_SYMBOLS", ["AAPL"])
    def test_valid_config_passes(self) -> None:
        validate_environment()  # Should not raise


# ── Trade Journal ────────────────────────────────────────────────────────────


class TestTradeJournal:
    """Verify CSV journal writes correct records."""

    @pytest.fixture(autouse=True)
    def _use_tmp_journal(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """Redirect journal output to a temp directory for each test."""
        tmp_journal = tmp_path / "trade_journal.csv"
        monkeypatch.setattr("utils.journal.JOURNAL_DIR", tmp_path)
        monkeypatch.setattr("utils.journal.JOURNAL_FILE", tmp_journal)
        self.journal_path = tmp_journal

    def test_creates_file_with_headers(self) -> None:
        record_trade(
            symbol="AAPL", side="BUY", qty=10,
            entry_price=150.0, take_profit=150.75,
            stop_loss=149.25, order_id="test-001",
        )
        with open(self.journal_path) as f:
            reader = csv.DictReader(f)
            assert reader.fieldnames == _FIELDNAMES

    def test_records_correct_values(self) -> None:
        record_trade(
            symbol="NVDA", side="BUY", qty=5,
            entry_price=800.0, take_profit=804.0,
            stop_loss=796.0, order_id="test-002",
        )
        with open(self.journal_path) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 1
        assert rows[0]["symbol"] == "NVDA"
        assert rows[0]["qty"] == "5"
        assert rows[0]["order_id"] == "test-002"

    def test_appends_multiple_trades(self) -> None:
        for i in range(3):
            record_trade(
                symbol=f"SYM{i}", side="BUY", qty=1,
                entry_price=100.0, take_profit=100.5,
                stop_loss=99.5, order_id=f"test-{i:03d}",
            )
        with open(self.journal_path) as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == 3
