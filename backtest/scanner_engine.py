"""
Scanner-aware backtester.

Unlike the standard backtest engine which tests a fixed symbol list,
this variant replays the premarket scanner's historical picks.  For
each trading day it uses only the symbols the scanner would have
selected that morning, then applies the same entry/exit logic.

This answers the critical question: "If the scanner had been running
for the past 6 months, how would the full system have performed?"

Usage:
    python run_scanner_backtest.py
"""

from datetime import datetime, timedelta

import pandas as pd

from backtest.engine import BacktestResults, OpenPosition, Trade
from config.settings import (
    ALLOCATION_PERCENT,
    API_KEY,
    MAX_OPEN_POSITIONS,
    RSI_OVERSOLD,
    SECRET_KEY,
    SL_PERCENT,
    TIMEFRAME,
    TP_PERCENT,
)
from scanner.premarket import scan_historical
from src.indicators import apply_indicators
from utils.logger import logger


class ScannerBacktestEngine:
    """Backtester that uses dynamically scanned symbols per day.

    Args:
        starting_capital: Initial account balance.
        lookback_days:    Calendar days of history.
        allocation_pct:   Fraction of capital per trade.
        tp_pct:           Take-profit distance.
        sl_pct:           Stop-loss distance.
        rsi_threshold:    RSI entry threshold.
        max_positions:    Maximum concurrent positions.
        max_candidates:   Max scanner picks per day.
    """

    def __init__(
        self,
        starting_capital: float = 100_000.0,
        lookback_days: int = 180,
        allocation_pct: float = ALLOCATION_PERCENT,
        tp_pct: float = TP_PERCENT,
        sl_pct: float = SL_PERCENT,
        rsi_threshold: int = RSI_OVERSOLD,
        max_positions: int = MAX_OPEN_POSITIONS,
        max_candidates: int = 8,
    ) -> None:
        self.starting_capital = starting_capital
        self.cash = starting_capital
        self.lookback_days = lookback_days
        self.allocation_pct = allocation_pct
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.rsi_threshold = rsi_threshold
        self.max_positions = max_positions
        self.max_candidates = max_candidates

        self.open_positions: list[OpenPosition] = []
        self.closed_trades: list[Trade] = []
        self.equity_snapshots: list[dict] = []

    def _calculate_qty(self, price: float) -> int:
        allocated = self.cash * self.allocation_pct
        return int(allocated // price)

    def _snapshot_equity(self, timestamp: datetime, prices: dict[str, float]) -> None:
        position_value = sum(
            pos.qty * prices.get(pos.symbol, pos.entry_price)
            for pos in self.open_positions
        )
        total = self.cash + position_value
        self.equity_snapshots.append({"timestamp": timestamp, "equity": total})

    def _check_exits(self, timestamp: datetime, candles: dict[str, pd.Series]) -> None:
        still_open: list[OpenPosition] = []

        for pos in self.open_positions:
            if pos.symbol not in candles:
                still_open.append(pos)
                continue

            candle = candles[pos.symbol]
            high = candle["high"]
            low = candle["low"]

            hit_tp = high >= pos.take_profit
            hit_sl = low <= pos.stop_loss

            if hit_sl and hit_tp:
                exit_price = pos.stop_loss
                side = "SL"
            elif hit_sl:
                exit_price = pos.stop_loss
                side = "SL"
            elif hit_tp:
                exit_price = pos.take_profit
                side = "TP"
            else:
                still_open.append(pos)
                continue

            trade = Trade(
                symbol=pos.symbol,
                entry_time=pos.entry_time,
                exit_time=timestamp,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                qty=pos.qty,
                side=side,
            )
            self.closed_trades.append(trade)
            self.cash += exit_price * pos.qty + trade.pnl

        self.open_positions = still_open

    def _check_entries(
        self,
        timestamp: datetime,
        candles: dict[str, pd.Series],
        today_symbols: list[str],
    ) -> None:
        """Check entries only against today's scanner-selected symbols."""
        held_symbols = {pos.symbol for pos in self.open_positions}

        for symbol in today_symbols:
            if len(self.open_positions) >= self.max_positions:
                break
            if symbol in held_symbols:
                continue
            if symbol not in candles:
                continue

            candle = candles[symbol]
            rsi = candle.get("rsi")
            vwap = candle.get("vwap")
            close = candle["close"]

            if pd.isna(rsi) or pd.isna(vwap):
                continue

            if rsi < self.rsi_threshold and close < vwap:
                qty = self._calculate_qty(close)
                if qty <= 0:
                    continue

                cost = close * qty
                if cost > self.cash:
                    continue

                tp_price = round(close * (1 + self.tp_pct), 2)
                sl_price = round(close * (1 - self.sl_pct), 2)

                self.cash -= cost
                self.open_positions.append(
                    OpenPosition(
                        symbol=symbol,
                        entry_time=timestamp,
                        entry_price=close,
                        qty=qty,
                        take_profit=tp_price,
                        stop_loss=sl_price,
                    )
                )
                held_symbols.add(symbol)
                logger.info(
                    "  ENTRY %s | %d × $%.2f | RSI: %.1f | Gap-down pick",
                    symbol,
                    qty,
                    close,
                    rsi,
                )

    def run(self) -> BacktestResults | None:
        """Execute the scanner-aware backtest.

        Steps:
            1. Run the historical scanner to get daily picks.
            2. Collect all unique symbols that were ever picked.
            3. Fetch 5-min data for those symbols.
            4. Walk forward day by day, using each day's picks.

        Returns:
            A :class:`BacktestResults` object, or ``None`` on failure.
        """
        # Step 1: Get historical scanner picks
        daily_picks = scan_historical(
            lookback_days=self.lookback_days,
            max_candidates=self.max_candidates,
        )

        if not daily_picks:
            logger.error("Historical scanner produced no picks.")
            return None

        # Step 2: Collect all unique symbols ever picked
        all_picked_symbols: set[str] = set()
        for symbols in daily_picks.values():
            all_picked_symbols.update(symbols)

        symbol_list = sorted(all_picked_symbols)
        logger.info(
            "Scanner selected %d unique symbols across %d trading days.",
            len(symbol_list),
            len(daily_picks),
        )

        # Step 3: Fetch 5-min data for all picked symbols
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest

        client = StockHistoricalDataClient(API_KEY, SECRET_KEY)
        end = datetime.now()
        start = end - timedelta(days=self.lookback_days)

        logger.info("Fetching 5-min data for %d symbols ...", len(symbol_list))

        # Fetch in batches to avoid API limits
        batch_size = 25
        all_dfs: list[pd.DataFrame] = []

        for i in range(0, len(symbol_list), batch_size):
            batch = symbol_list[i : i + batch_size]
            logger.info(
                "  Batch %d/%d (%d symbols) ...",
                i // batch_size + 1,
                (len(symbol_list) + batch_size - 1) // batch_size,
                len(batch),
            )

            request = StockBarsRequest(
                symbol_or_symbols=batch,
                timeframe=TIMEFRAME,
                start=start,
                end=end,
            )

            try:
                bars = client.get_stock_bars(request)
                batch_df = bars.df
                if not batch_df.empty:
                    all_dfs.append(batch_df)
            except Exception as exc:
                logger.warning("Batch fetch failed for %s: %s", batch[:3], exc)
                continue

        if not all_dfs:
            logger.error("No 5-min data retrieved.")
            return None

        raw_df = pd.concat(all_dfs)
        logger.info("Combined 5-min data shape: %s", raw_df.shape)

        # Step 4: Calculate indicators
        df = apply_indicators(raw_df)
        if df is None:
            return None

        # Step 5: Walk forward through each bar
        timestamps = df.index.get_level_values("timestamp").unique().sort_values()
        available_symbols = df.index.get_level_values("symbol").unique().tolist()
        total_bars = len(timestamps)

        logger.info(
            "Running scanner backtest: %d bars, %d unique symbols, $%.0f capital ...",
            total_bars,
            len(available_symbols),
            self.starting_capital,
        )

        current_day: str = ""
        today_symbols: list[str] = []

        for i, ts in enumerate(timestamps):
            # Determine which day we're on and look up scanner picks
            ts_date = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]

            if ts_date != current_day:
                current_day = ts_date
                today_symbols = daily_picks.get(current_day, [])
                if today_symbols:
                    logger.info(
                        "Day %s — scanner picks: %s",
                        current_day,
                        ", ".join(today_symbols),
                    )

            # Build candle dict for this timestamp
            candles: dict[str, pd.Series] = {}
            for symbol in available_symbols:
                try:
                    candles[symbol] = df.loc[(symbol, ts)]
                except KeyError:
                    continue

            # Exits checked against ALL symbols (positions may be from prior days)
            self._check_exits(ts, candles)

            # Entries checked only against today's scanner picks
            self._check_entries(ts, candles, today_symbols)

            # Equity snapshot
            prices = {s: c["close"] for s, c in candles.items()}
            if i % 100 == 0 or i == total_bars - 1:
                self._snapshot_equity(ts, prices)

        # Close any remaining positions
        if self.open_positions:
            for pos in self.open_positions:
                try:
                    last_candle = df.xs(pos.symbol, level="symbol").iloc[-1]
                    exit_price = last_candle["close"]
                except (KeyError, IndexError):
                    exit_price = pos.entry_price

                trade = Trade(
                    symbol=pos.symbol,
                    entry_time=pos.entry_time,
                    exit_time=timestamps[-1],
                    entry_price=pos.entry_price,
                    exit_price=exit_price,
                    qty=pos.qty,
                    side="CLOSE",
                )
                self.closed_trades.append(trade)
                self.cash += exit_price * pos.qty

            self.open_positions.clear()

        equity_df = pd.DataFrame(self.equity_snapshots)
        if not equity_df.empty:
            equity_df.set_index("timestamp", inplace=True)

        results = BacktestResults(
            trades=self.closed_trades,
            equity_curve=equity_df,
            starting_capital=self.starting_capital,
            ending_capital=self.cash,
            parameters={
                "lookback_days": self.lookback_days,
                "symbols": f"Dynamic ({len(all_picked_symbols)} unique)",
                "allocation_pct": self.allocation_pct,
                "tp_pct": self.tp_pct,
                "sl_pct": self.sl_pct,
                "rsi_threshold": self.rsi_threshold,
                "max_positions": self.max_positions,
                "max_candidates": self.max_candidates,
                "days_with_picks": len(daily_picks),
            },
        )

        logger.info(
            "Scanner backtest complete: %d trades across %d unique symbols.",
            len(self.closed_trades),
            len(all_picked_symbols),
        )
        return results
