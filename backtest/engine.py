"""
Backtesting engine for the VWAP Reversion Engine.

Walks forward through historical candles in chronological order,
applying the same entry logic and bracket-order exit rules as the
live engine.  No future data is ever visible to the strategy —
decisions are made strictly on the current and prior candles.

Usage:
    from backtest.engine import BacktestEngine
    engine = BacktestEngine(starting_capital=100_000)
    results = engine.run()
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta

import pandas as pd

from config.settings import (
    ALLOCATION_PERCENT,
    API_KEY,
    CONVICTION_MAX_MULTIPLIER,
    COOLDOWN_MINUTES,
    DAILY_LOSS_LIMIT_PCT,
    MAX_OPEN_POSITIONS,
    RSI_OVERSOLD,
    SECRET_KEY,
    SL_ATR_MULTIPLIER,
    SL_PERCENT,
    TARGET_SYMBOLS,
    TIMEFRAME,
    TP_ATR_MULTIPLIER,
    TP_PERCENT,
    TRADING_END_HOUR_UTC,
    TRADING_END_MINUTE_UTC,
    TRADING_START_HOUR_UTC,
    TRAIL_ACTIVATION_ATR,
    TRAIL_DISTANCE_ATR,
    USE_ATR_EXITS,
    USE_CONVICTION_SIZING,
    USE_EMA_TREND_FILTER,
    USE_TRAILING_STOP,
    VOLUME_MULTIPLIER,
    VWAP_DISTANCE_PCT,
)
from src.indicators import apply_indicators
from utils.logger import logger

# ── Data Structures ──────────────────────────────────────────────────────────


@dataclass
class Trade:
    """Record of a single completed trade."""

    symbol: str
    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    qty: int
    side: str  # "TP" or "SL"
    pnl: float = 0.0
    pnl_percent: float = 0.0

    def __post_init__(self) -> None:
        self.pnl = (self.exit_price - self.entry_price) * self.qty
        self.pnl_percent = (self.exit_price - self.entry_price) / self.entry_price


@dataclass
class OpenPosition:
    """A currently held position with bracket exit levels and trailing state."""

    symbol: str
    entry_time: datetime
    entry_price: float
    qty: int
    take_profit: float          # Fixed TP ceiling (ignored when trailing is active)
    stop_loss: float            # Current stop level (moves up when trailing)
    initial_stop: float = 0.0   # Original stop level — never moves
    high_water: float = 0.0     # Highest price seen since entry
    trail_active: bool = False  # True once activation threshold is hit
    atr_at_entry: float = 0.0   # ATR value at entry time for trail calculations


@dataclass
class BacktestResults:
    """Container for all backtest outputs."""

    trades: list[Trade]
    equity_curve: pd.DataFrame
    starting_capital: float
    ending_capital: float
    parameters: dict = field(default_factory=dict)


# ── Engine ───────────────────────────────────────────────────────────────────


class BacktestEngine:
    """Event-driven backtester that simulates the mean-reversion strategy.

    Args:
        starting_capital: Initial account balance in dollars.
        lookback_days:    Calendar days of history to fetch.
        symbols:          Tickers to test (defaults to settings.TARGET_SYMBOLS).
        allocation_pct:   Fraction of capital per trade.
        tp_pct:           Take-profit distance as decimal.
        sl_pct:           Stop-loss distance as decimal.
        rsi_threshold:    RSI level below which a buy signal fires.
        max_positions:    Maximum concurrent open positions.
    """

    def __init__(
        self,
        starting_capital: float = 100_000.0,
        lookback_days: int = 180,
        symbols: list[str] | None = None,
        allocation_pct: float = ALLOCATION_PERCENT,
        tp_pct: float = TP_PERCENT,
        sl_pct: float = SL_PERCENT,
        rsi_threshold: int = RSI_OVERSOLD,
        max_positions: int = MAX_OPEN_POSITIONS,
        use_time_filter: bool = True,
        trading_start_utc: int = TRADING_START_HOUR_UTC * 60,
        trading_end_utc: int = TRADING_END_HOUR_UTC * 60 + TRADING_END_MINUTE_UTC,
        use_atr_exits: bool = USE_ATR_EXITS,
        tp_atr_mult: float = TP_ATR_MULTIPLIER,
        sl_atr_mult: float = SL_ATR_MULTIPLIER,
        cooldown_minutes: int = COOLDOWN_MINUTES,
        daily_loss_limit_pct: float = DAILY_LOSS_LIMIT_PCT,
        vwap_distance_pct: float = VWAP_DISTANCE_PCT,
        volume_multiplier: float = VOLUME_MULTIPLIER,
        use_ema_filter: bool = USE_EMA_TREND_FILTER,
        use_conviction_sizing: bool = USE_CONVICTION_SIZING,
        conviction_max_mult: float = CONVICTION_MAX_MULTIPLIER,
        use_trailing_stop: bool = USE_TRAILING_STOP,
        trail_activation_atr: float = TRAIL_ACTIVATION_ATR,
        trail_distance_atr: float = TRAIL_DISTANCE_ATR,
    ) -> None:
        self.starting_capital = starting_capital
        self.cash = starting_capital
        self.lookback_days = lookback_days
        self.symbols = symbols or TARGET_SYMBOLS
        self.allocation_pct = allocation_pct
        self.tp_pct = tp_pct
        self.sl_pct = sl_pct
        self.rsi_threshold = rsi_threshold
        self.max_positions = max_positions
        self.use_time_filter = use_time_filter
        self.trading_start_utc = trading_start_utc
        self.trading_end_utc = trading_end_utc
        self.use_atr_exits = use_atr_exits
        self.tp_atr_mult = tp_atr_mult
        self.sl_atr_mult = sl_atr_mult
        self.cooldown_minutes = cooldown_minutes
        self.daily_loss_limit_pct = daily_loss_limit_pct
        self.vwap_distance_pct = vwap_distance_pct
        self.volume_multiplier = volume_multiplier
        self.use_ema_filter = use_ema_filter
        self.use_conviction_sizing = use_conviction_sizing
        self.conviction_max_mult = conviction_max_mult
        self.use_trailing_stop = use_trailing_stop
        self.trail_activation_atr = trail_activation_atr
        self.trail_distance_atr = trail_distance_atr

        self.open_positions: list[OpenPosition] = []
        self.closed_trades: list[Trade] = []
        self.equity_snapshots: list[dict] = []

        # Risk state tracking
        self._cooldowns: dict[str, datetime] = {}      # symbol → earliest re-entry time
        self._daily_pnl: float = 0.0                   # running P&L for current day
        self._current_day: str = ""                    # tracks day boundaries
        self._daily_circuit_tripped: bool = False      # True = no more entries today

    # ── Data Fetching ────────────────────────────────────────────────────

    def _fetch_data(self) -> pd.DataFrame | None:
        """Pull historical bars from Alpaca and compute indicators."""
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest

        logger.info(
            "Fetching %d days of %d-min bars for %s ...",
            self.lookback_days,
            TIMEFRAME.amount,
            self.symbols,
        )

        client = StockHistoricalDataClient(API_KEY, SECRET_KEY)
        end = datetime.now()
        start = end - timedelta(days=self.lookback_days)

        request = StockBarsRequest(
            symbol_or_symbols=self.symbols,
            timeframe=TIMEFRAME,
            start=start,
            end=end,
        )

        try:
            bars = client.get_stock_bars(request)
            df = bars.df
            logger.info("Raw data shape: %s", df.shape)
            return df
        except Exception as exc:
            logger.error("Data fetch failed: %s", exc)
            return None

    # ── Position Sizing ──────────────────────────────────────────────────

    def _calculate_qty(self, price: float, rsi: float = 28.0) -> int:
        """Determine share count from current cash, allocation %, and conviction.

        When conviction sizing is enabled, deeper oversold readings
        (lower RSI) produce larger positions on a linear scale.
        """
        alloc = self.allocation_pct

        if self.use_conviction_sizing:
            # Linear scale: RSI at threshold = 1.0×, RSI at 0 = max multiplier
            rsi_clamped = max(0.0, min(rsi, self.rsi_threshold))
            conviction = 1.0 + (1.0 - rsi_clamped / self.rsi_threshold) * (self.conviction_max_mult - 1.0)
            alloc = self.allocation_pct * conviction

        allocated = self.cash * alloc
        return int(allocated // price)

    # ── Equity Tracking ──────────────────────────────────────────────────

    def _snapshot_equity(self, timestamp: datetime, prices: dict[str, float]) -> None:
        """Record total portfolio value at a point in time."""
        position_value = sum(
            pos.qty * prices.get(pos.symbol, pos.entry_price)
            for pos in self.open_positions
        )
        total = self.cash + position_value
        self.equity_snapshots.append({"timestamp": timestamp, "equity": total})

    # ── Exit Logic ───────────────────────────────────────────────────────

    def _check_exits(self, timestamp: datetime, candles: dict[str, pd.Series]) -> None:
        """Check exits with trailing stop support.

        When trailing is enabled, the exit flow is:
            1. Update each position's high-water mark from the candle high.
            2. If price has moved up by trail_activation_atr × ATR from entry,
               activate the trailing stop.
            3. Once active, the stop trails at trail_distance_atr × ATR below
               the high-water mark — it only moves up, never down.
            4. The initial stop-loss still applies as an absolute floor.
            5. If trailing is disabled, fall back to fixed TP/SL behavior.
        """
        still_open: list[OpenPosition] = []

        for pos in self.open_positions:
            if pos.symbol not in candles:
                still_open.append(pos)
                continue

            candle = candles[pos.symbol]
            high = candle["high"]
            low = candle["low"]

            # ── Update trailing state ────────────────────────────────
            if self.use_trailing_stop and pos.atr_at_entry > 0:
                # Update high-water mark
                if high > pos.high_water:
                    pos.high_water = high

                # Check activation: price moved up enough from entry
                activation_price = pos.entry_price + (pos.atr_at_entry * self.trail_activation_atr)
                if not pos.trail_active and high >= activation_price:
                    pos.trail_active = True
                    logger.info(
                        "  TRAIL ACTIVATED %s | HWM: $%.2f | Entry: $%.2f",
                        pos.symbol, pos.high_water, pos.entry_price,
                    )

                # Update trailing stop level
                if pos.trail_active:
                    trail_stop = pos.high_water - (pos.atr_at_entry * self.trail_distance_atr)
                    # Only ratchet up, never down — and never below initial stop
                    if trail_stop > pos.stop_loss:
                        pos.stop_loss = round(trail_stop, 2)

                # When trailing is active, there's no fixed TP ceiling
                hit_sl = low <= pos.stop_loss
                hit_tp = False

                if not pos.trail_active:
                    # Before activation, use the fixed TP as a ceiling
                    hit_tp = high >= pos.take_profit
            else:
                # Fixed TP/SL mode
                hit_tp = high >= pos.take_profit
                hit_sl = low <= pos.stop_loss

            # ── Determine exit ───────────────────────────────────────
            if hit_sl and hit_tp:
                exit_price = pos.stop_loss
                side = "TRAIL" if pos.trail_active else "SL"
            elif hit_sl:
                exit_price = pos.stop_loss
                side = "TRAIL" if pos.trail_active else "SL"
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
            self._daily_pnl += trade.pnl

            # Set cooldown on losing exits only
            if trade.pnl < 0:
                cooldown_until = timestamp + timedelta(minutes=self.cooldown_minutes)
                self._cooldowns[pos.symbol] = cooldown_until

            # Check daily loss circuit breaker
            if self._daily_pnl <= -(self.starting_capital * self.daily_loss_limit_pct):
                if not self._daily_circuit_tripped:
                    logger.info(
                        "  CIRCUIT BREAKER — daily loss $%.2f exceeds %.1f%% limit.",
                        self._daily_pnl,
                        self.daily_loss_limit_pct * 100,
                    )
                    self._daily_circuit_tripped = True

            logger.info(
                "  EXIT %s | %s @ $%.2f → $%.2f | P&L: $%.2f (%s) | HWM: $%.2f",
                trade.symbol,
                side,
                trade.entry_price,
                trade.exit_price,
                trade.pnl,
                f"{trade.pnl_percent:+.2%}",
                pos.high_water,
            )

        self.open_positions = still_open

    # ── Entry Logic ──────────────────────────────────────────────────────

    def _check_entries(
        self, timestamp: datetime, candles: dict[str, pd.Series]
    ) -> None:
        """Scan for mean-reversion entries on the current candle."""
        # Time-of-day filter
        if self.use_time_filter:
            ts_minutes = timestamp.hour * 60 + timestamp.minute
            if ts_minutes < self.trading_start_utc or ts_minutes > self.trading_end_utc:
                return

        # Daily loss circuit breaker
        if self._daily_circuit_tripped:
            return

        held_symbols = {pos.symbol for pos in self.open_positions}

        for symbol in self.symbols:
            if len(self.open_positions) >= self.max_positions:
                break
            if symbol in held_symbols:
                continue
            if symbol not in candles:
                continue

            # Cooldown check — skip if this symbol was recently stopped out
            if symbol in self._cooldowns and timestamp < self._cooldowns[symbol]:
                continue

            candle = candles[symbol]
            rsi = candle.get("rsi")
            vwap = candle.get("vwap")
            atr = candle.get("atr")
            ema_200 = candle.get("ema_200")
            vol = candle.get("volume", 0)
            vol_avg = candle.get("vol_avg_20")
            close = candle["close"]

            if pd.isna(rsi) or pd.isna(vwap):
                continue

            # ── Core signal: RSI oversold + below VWAP ───────────────
            if rsi >= self.rsi_threshold:
                continue
            if close >= vwap:
                continue

            # ── Filter 1: VWAP distance — reject trivial dips ────────
            vwap_dist = (vwap - close) / vwap
            if vwap_dist < self.vwap_distance_pct:
                continue

            # ── Filter 2: Volume confirmation — need real participation
            if not pd.isna(vol_avg) and vol_avg > 0:
                if vol < vol_avg * self.volume_multiplier:
                    continue

            # ── Filter 3: EMA trend — only buy dips in uptrends ──────
            if self.use_ema_filter and not pd.isna(ema_200):
                if close < ema_200:
                    continue

            # ── Position sizing with conviction ──────────────────────
            qty = self._calculate_qty(close, rsi)
            if qty <= 0:
                continue

            cost = close * qty
            if cost > self.cash:
                continue

            # ── Exit levels — ATR-based or fixed ─────────────────────
            atr_val = atr if (not pd.isna(atr) and atr > 0) else 0.0

            if self.use_atr_exits and atr_val > 0:
                sl_price = round(close - (atr_val * self.sl_atr_mult), 2)
                if self.use_trailing_stop:
                    # TP set high as initial ceiling (before trail activates)
                    tp_price = round(close + (atr_val * self.tp_atr_mult), 2)
                    exit_mode = "TRAIL"
                else:
                    tp_price = round(close + (atr_val * self.tp_atr_mult), 2)
                    exit_mode = "ATR"
            else:
                tp_price = round(close * (1 + self.tp_pct), 2)
                sl_price = round(close * (1 - self.sl_pct), 2)
                exit_mode = "FIXED"

            self.cash -= cost
            self.open_positions.append(
                OpenPosition(
                    symbol=symbol,
                    entry_time=timestamp,
                    entry_price=close,
                    qty=qty,
                    take_profit=tp_price,
                    stop_loss=sl_price,
                    initial_stop=sl_price,
                    high_water=close,
                    trail_active=False,
                    atr_at_entry=atr_val,
                )
            )
            held_symbols.add(symbol)
            logger.info(
                "  ENTRY %s | %d × $%.2f | TP: $%.2f | SL: $%.2f | RSI: %.1f | %s",
                symbol,
                qty,
                close,
                tp_price,
                sl_price,
                rsi,
                exit_mode,
            )

    # ── Main Loop ────────────────────────────────────────────────────────

    def run(self) -> BacktestResults | None:
        """Execute the full backtest and return results.

        Returns:
            A :class:`BacktestResults` object, or ``None`` if data
            fetching or indicator calculation fails.
        """
        raw_df = self._fetch_data()
        if raw_df is None:
            return None

        df = apply_indicators(raw_df)
        if df is None:
            return None

        # Build a time-ordered iteration structure.
        # For each timestamp, collect candle data keyed by symbol.
        timestamps = df.index.get_level_values("timestamp").unique().sort_values()
        total_bars = len(timestamps)

        logger.info(
            "Running backtest: %d bars across %d symbols, $%.0f starting capital ...",
            total_bars,
            len(self.symbols),
            self.starting_capital,
        )

        for i, ts in enumerate(timestamps):
            # Reset daily tracking at day boundaries
            ts_day = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
            if ts_day != self._current_day:
                self._current_day = ts_day
                self._daily_pnl = 0.0
                self._daily_circuit_tripped = False

            # Build {symbol: candle_series} for this timestamp
            candles: dict[str, pd.Series] = {}
            for symbol in self.symbols:
                try:
                    candles[symbol] = df.loc[(symbol, ts)]
                except KeyError:
                    continue

            # 1. Check exits first (bracket orders fill intrabar)
            self._check_exits(ts, candles)

            # 2. Check for new entries
            self._check_entries(ts, candles)

            # 3. Snapshot equity every 50 bars to keep the curve manageable
            prices = {s: c["close"] for s, c in candles.items()}
            if i % 50 == 0 or i == total_bars - 1:
                self._snapshot_equity(ts, prices)

        # Close any remaining positions at last available price
        if self.open_positions:
            last_candles: dict[str, pd.Series] = {}
            for symbol in self.symbols:
                try:
                    symbol_data = df.xs(symbol, level="symbol")
                    last_candles[symbol] = symbol_data.iloc[-1]
                except KeyError:
                    continue

            for pos in self.open_positions:
                last_price = last_candles.get(pos.symbol)
                if last_price is not None:
                    exit_price = last_price["close"]
                else:
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

        ending_capital = self.cash

        results = BacktestResults(
            trades=self.closed_trades,
            equity_curve=equity_df,
            starting_capital=self.starting_capital,
            ending_capital=ending_capital,
            parameters={
                "lookback_days": self.lookback_days,
                "symbols": self.symbols,
                "allocation_pct": self.allocation_pct,
                "tp_pct": self.tp_pct,
                "sl_pct": self.sl_pct,
                "rsi_threshold": self.rsi_threshold,
                "max_positions": self.max_positions,
                "time_filter": f"{self.trading_start_utc // 60:02d}:{self.trading_start_utc % 60:02d}–{self.trading_end_utc // 60:02d}:{self.trading_end_utc % 60:02d} UTC" if self.use_time_filter else "OFF",
                "exits": f"Trailing (activate: {self.trail_activation_atr}× ATR, trail: {self.trail_distance_atr}× ATR)" if self.use_trailing_stop else (f"ATR ({self.tp_atr_mult}×/{self.sl_atr_mult}×)" if self.use_atr_exits else f"Fixed ({self.tp_pct:.1%}/{self.sl_pct:.1%})"),
                "initial_stop": f"{self.sl_atr_mult}× ATR",
                "cooldown": f"{self.cooldown_minutes} min",
                "daily_loss_limit": f"{self.daily_loss_limit_pct:.1%}",
            },
        )

        logger.info("Backtest complete: %d trades executed.", len(self.closed_trades))
        return results
