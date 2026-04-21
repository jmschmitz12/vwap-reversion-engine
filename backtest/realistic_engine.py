"""
Realistic backtesting engine for the VWAP Reversion Engine.

Models the frictions the original backtest ignored:

  1. LIMIT ORDER FILL RATE — A limit buy at signal_price + $buffer only
     fills if the next candle actually trades at or below that level.
     Many signals fire on bars where price bounces immediately, so the
     order never fills.  This matches live behavior.

  2. ENTRY SLIPPAGE — Even when a limit fills, the fill isn't always
     at the limit price.  Fill price = min(limit, next_bar_open) to
     simulate realistic price improvement / lack thereof.

  3. STOP-LOSS SLIPPAGE — When a stop triggers, you don't get the
     stop price exactly — you get the next available liquidity, which
     is worse.  We model this as stop_price - sl_slippage.

  4. TAKE-PROFIT PROTECTION — TP is a limit sell, so it either fills
     at the TP price or not at all.  No negative slippage on TP fills.

  5. CORRECT SYMBOL UNIVERSE — Tests against the exact 15 symbols
     the live bot is configured for, not a different optimized set.

Two preset profiles:

  MODERATE — Matches observed live behavior:
    - Limit buffer $0.05, fills if next bar low <= signal + $0.05
    - Entry fill at min(limit, next_bar_open)
    - SL slippage $0.02
    - TP fills exactly at target

  CONSERVATIVE — Worst reasonable case:
    - Limit buffer $0.05, strict: next bar low <= signal (not signal + buffer)
    - Entry fill at exactly the limit price (no price improvement)
    - SL slippage $0.05
    - TP fills with 10% partial-fill haircut (simulates crossed market)

A third preset, IDEALIZED, replicates the original backtest assumptions
so you can see the delta between fantasy and reality.
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import Enum

import pandas as pd

from config.settings import (
    ALLOCATION_PERCENT,
    API_KEY,
    COOLDOWN_MINUTES,
    DAILY_LOSS_LIMIT_PCT,
    MAX_OPEN_POSITIONS,
    RSI_OVERSOLD,
    SECRET_KEY,
    SL_ATR_MULTIPLIER,
    TARGET_SYMBOLS,
    TIMEFRAME,
    TP_ATR_MULTIPLIER,
    TRADING_END_HOUR_UTC,
    TRADING_END_MINUTE_UTC,
    TRADING_START_HOUR_UTC,
)
from src.indicators import apply_indicators
from utils.logger import logger


# ── Realism Profiles ─────────────────────────────────────────────────────────


class RealismProfile(Enum):
    IDEALIZED = "idealized"      # Original backtest assumptions
    MODERATE = "moderate"        # Matches observed live behavior
    CONSERVATIVE = "conservative"  # Worst reasonable case


@dataclass
class FrictionConfig:
    """Per-profile friction parameters."""
    limit_buffer: float           # Dollars above signal price for limit
    require_strict_dip: bool      # If True, fills only if next low <= signal
    use_price_improvement: bool   # If True, fill = min(limit, next_open)
    sl_slippage: float            # Dollars worse than stop price on SL fill
    tp_partial_fill_pct: float    # Fraction of TP gain lost to partial fill
    use_any_fills: bool           # If False, assume 100% fill at signal close

    @classmethod
    def for_profile(cls, profile: RealismProfile) -> "FrictionConfig":
        if profile == RealismProfile.IDEALIZED:
            return cls(
                limit_buffer=0.0,
                require_strict_dip=False,
                use_price_improvement=False,
                sl_slippage=0.0,
                tp_partial_fill_pct=0.0,
                use_any_fills=False,  # 100% fill at close
            )
        if profile == RealismProfile.MODERATE:
            return cls(
                limit_buffer=0.05,
                require_strict_dip=False,
                use_price_improvement=True,
                sl_slippage=0.02,
                tp_partial_fill_pct=0.0,
                use_any_fills=True,
            )
        # CONSERVATIVE
        return cls(
            limit_buffer=0.05,
            require_strict_dip=True,
            use_price_improvement=False,
            sl_slippage=0.05,
            tp_partial_fill_pct=0.10,
            use_any_fills=True,
        )


# ── Data Structures ──────────────────────────────────────────────────────────


@dataclass
class Trade:
    """Record of a completed trade."""
    symbol: str
    entry_time: datetime
    exit_time: datetime
    signal_price: float
    entry_price: float
    exit_price: float
    qty: int
    side: str  # "TP", "SL", "CLOSE"
    pnl: float = 0.0
    pnl_percent: float = 0.0

    def __post_init__(self) -> None:
        self.pnl = (self.exit_price - self.entry_price) * self.qty
        self.pnl_percent = (
            (self.exit_price - self.entry_price) / self.entry_price
            if self.entry_price > 0 else 0.0
        )


@dataclass
class OpenPosition:
    symbol: str
    entry_time: datetime
    signal_price: float
    entry_price: float
    qty: int
    take_profit: float
    stop_loss: float


@dataclass
class SignalStats:
    """Track how signals translate to trades."""
    total_signals: int = 0
    filled: int = 0
    rejected_no_dip: int = 0       # Price didn't come back to limit
    rejected_capacity: int = 0      # At position cap
    rejected_cooldown: int = 0      # In cooldown
    rejected_capital: int = 0       # Not enough cash


@dataclass
class BacktestResults:
    trades: list[Trade]
    equity_curve: pd.DataFrame
    starting_capital: float
    ending_capital: float
    signal_stats: SignalStats
    profile: RealismProfile
    symbol_pnl: dict[str, float] = field(default_factory=dict)
    symbol_trades: dict[str, int] = field(default_factory=dict)


# ── Engine ───────────────────────────────────────────────────────────────────


class RealisticBacktestEngine:
    """Event-driven backtester with realistic fill modeling."""

    def __init__(
        self,
        profile: RealismProfile = RealismProfile.MODERATE,
        starting_capital: float = 25_000.0,
        lookback_days: int = 180,
        symbols: list[str] | None = None,
    ) -> None:
        self.profile = profile
        self.friction = FrictionConfig.for_profile(profile)
        self.starting_capital = starting_capital
        self.cash = starting_capital
        self.lookback_days = lookback_days
        self.symbols = symbols or TARGET_SYMBOLS

        self.open_positions: list[OpenPosition] = []
        self.closed_trades: list[Trade] = []
        self.equity_snapshots: list[dict] = []
        self.signal_stats = SignalStats()

        # Risk state
        self._cooldowns: dict[str, datetime] = {}
        self._daily_pnl: float = 0.0
        self._current_day: str = ""
        self._daily_circuit_tripped: bool = False

    # ── Data Fetch ───────────────────────────────────────────────────────

    def _fetch_data(self) -> pd.DataFrame | None:
        from alpaca.data.historical import StockHistoricalDataClient
        from alpaca.data.requests import StockBarsRequest

        logger.info(
            "[%s] Fetching %d days of %d-min bars for %d symbols ...",
            self.profile.value, self.lookback_days,
            TIMEFRAME.amount, len(self.symbols),
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
            logger.info("[%s] Raw data shape: %s", self.profile.value, df.shape)
            return df
        except Exception as exc:
            logger.error("[%s] Data fetch failed: %s", self.profile.value, exc)
            return None

    # ── Fill Simulation ──────────────────────────────────────────────────

    def _simulate_limit_fill(
        self,
        signal_price: float,
        next_candle: pd.Series,
    ) -> float | None:
        """Determine if a limit buy fills and at what price.

        Returns:
            Fill price, or ``None`` if the order doesn't fill.
        """
        if not self.friction.use_any_fills:
            # Idealized mode: fill at signal price always
            return signal_price

        limit_price = signal_price + self.friction.limit_buffer
        next_low = next_candle["low"]
        next_open = next_candle["open"]

        # Strict mode: only fill if price actively dipped to/below signal
        fill_threshold = signal_price if self.friction.require_strict_dip else limit_price

        if next_low > fill_threshold:
            # Price never came back down — order doesn't fill
            return None

        # Order fills.  Determine fill price:
        if self.friction.use_price_improvement:
            # Get the better of limit or open price
            fill_price = min(limit_price, next_open)
        else:
            # Conservative: fill at the limit exactly (no improvement)
            fill_price = limit_price

        # Cap fill at the candle's high (can't fill above what traded)
        fill_price = min(fill_price, next_candle["high"])
        return round(fill_price, 2)

    def _simulate_exit(
        self, pos: OpenPosition, candle: pd.Series,
    ) -> tuple[float, str] | None:
        """Check if TP or SL triggered this bar, and at what price."""
        high = candle["high"]
        low = candle["low"]

        hit_tp = high >= pos.take_profit
        hit_sl = low <= pos.stop_loss

        if not hit_tp and not hit_sl:
            return None

        # Ambiguous candles — conservatively assume SL hit first
        if hit_sl:
            sl_fill = pos.stop_loss - self.friction.sl_slippage
            sl_fill = max(sl_fill, low)  # Can't fill below the candle's low
            return round(sl_fill, 2), "SL"

        # TP hit
        tp_fill = pos.take_profit
        if self.friction.tp_partial_fill_pct > 0:
            # Conservative mode: simulate partial fill
            gain = pos.take_profit - pos.entry_price
            haircut = gain * self.friction.tp_partial_fill_pct
            tp_fill -= haircut
        return round(tp_fill, 2), "TP"

    # ── Main Loop ────────────────────────────────────────────────────────

    def run(self) -> BacktestResults | None:
        raw_df = self._fetch_data()
        if raw_df is None:
            return None
        df = apply_indicators(raw_df)
        if df is None:
            return None

        # Build symbol-indexed views for efficient next-bar lookup
        symbol_frames: dict[str, pd.DataFrame] = {}
        for symbol in self.symbols:
            try:
                symbol_frames[symbol] = df.xs(symbol, level="symbol").sort_index()
            except KeyError:
                continue

        timestamps = df.index.get_level_values("timestamp").unique().sort_values()
        total_bars = len(timestamps)

        logger.info(
            "[%s] Running backtest: %d bars × %d symbols, $%.0f capital",
            self.profile.value, total_bars, len(self.symbols), self.starting_capital,
        )

        trading_start = TRADING_START_HOUR_UTC * 60
        trading_end = TRADING_END_HOUR_UTC * 60 + TRADING_END_MINUTE_UTC

        for i, ts in enumerate(timestamps):
            # Day boundary reset
            ts_day = ts.strftime("%Y-%m-%d") if hasattr(ts, "strftime") else str(ts)[:10]
            if ts_day != self._current_day:
                self._current_day = ts_day
                self._daily_pnl = 0.0
                self._daily_circuit_tripped = False

            # Collect current-bar candles
            candles: dict[str, pd.Series] = {}
            for symbol in self.symbols:
                try:
                    candles[symbol] = df.loc[(symbol, ts)]
                except KeyError:
                    continue

            # 1. Process exits first
            self._check_exits(ts, candles)

            # 2. Process entry signals (with next-bar fill simulation)
            ts_minutes = ts.hour * 60 + ts.minute
            in_window = trading_start <= ts_minutes <= trading_end
            if in_window and not self._daily_circuit_tripped:
                self._check_entries(ts, candles, symbol_frames, timestamps, i)

            # 3. Snapshot equity periodically
            if i % 50 == 0 or i == total_bars - 1:
                prices = {s: c["close"] for s, c in candles.items()}
                self._snapshot_equity(ts, prices)

        # Close any remaining positions
        self._close_all_at_end(df, timestamps[-1])

        equity_df = pd.DataFrame(self.equity_snapshots)
        if not equity_df.empty:
            equity_df.set_index("timestamp", inplace=True)

        # Per-symbol breakdown
        symbol_pnl: dict[str, float] = {}
        symbol_trades: dict[str, int] = {}
        for t in self.closed_trades:
            symbol_pnl[t.symbol] = symbol_pnl.get(t.symbol, 0.0) + t.pnl
            symbol_trades[t.symbol] = symbol_trades.get(t.symbol, 0) + 1

        results = BacktestResults(
            trades=self.closed_trades,
            equity_curve=equity_df,
            starting_capital=self.starting_capital,
            ending_capital=self.cash,
            signal_stats=self.signal_stats,
            profile=self.profile,
            symbol_pnl=symbol_pnl,
            symbol_trades=symbol_trades,
        )
        logger.info(
            "[%s] Complete: %d signals, %d filled, %d trades",
            self.profile.value,
            self.signal_stats.total_signals,
            self.signal_stats.filled,
            len(self.closed_trades),
        )
        return results

    # ── Exit Check ───────────────────────────────────────────────────────

    def _check_exits(self, ts: datetime, candles: dict[str, pd.Series]) -> None:
        still_open: list[OpenPosition] = []
        for pos in self.open_positions:
            if pos.symbol not in candles:
                still_open.append(pos)
                continue

            exit_result = self._simulate_exit(pos, candles[pos.symbol])
            if exit_result is None:
                still_open.append(pos)
                continue

            exit_price, side = exit_result
            trade = Trade(
                symbol=pos.symbol,
                entry_time=pos.entry_time,
                exit_time=ts,
                signal_price=pos.signal_price,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                qty=pos.qty,
                side=side,
            )
            self.closed_trades.append(trade)
            self.cash += exit_price * pos.qty
            self._daily_pnl += trade.pnl

            if trade.pnl < 0:
                self._cooldowns[pos.symbol] = ts + timedelta(minutes=COOLDOWN_MINUTES)

            if self._daily_pnl <= -(self.starting_capital * DAILY_LOSS_LIMIT_PCT):
                self._daily_circuit_tripped = True

        self.open_positions = still_open

    # ── Entry Check ──────────────────────────────────────────────────────

    def _check_entries(
        self,
        ts: datetime,
        candles: dict[str, pd.Series],
        symbol_frames: dict[str, pd.DataFrame],
        timestamps: pd.Index,
        current_idx: int,
    ) -> None:
        held_symbols = {pos.symbol for pos in self.open_positions}

        for symbol in self.symbols:
            if symbol not in candles:
                continue
            if symbol in held_symbols:
                continue

            candle = candles[symbol]
            rsi = candle.get("rsi")
            vwap = candle.get("vwap")
            atr = candle.get("atr")
            close = candle["close"]

            if pd.isna(rsi) or pd.isna(vwap):
                continue

            # Core signal
            if rsi >= RSI_OVERSOLD:
                continue
            if close >= vwap:
                continue

            # Signal fired
            self.signal_stats.total_signals += 1

            # Capacity / cooldown / capital checks
            if len(self.open_positions) >= MAX_OPEN_POSITIONS:
                self.signal_stats.rejected_capacity += 1
                continue
            if symbol in self._cooldowns and ts < self._cooldowns[symbol]:
                self.signal_stats.rejected_cooldown += 1
                continue

            atr_val = atr if (not pd.isna(atr) and atr > 0) else 0.0
            if atr_val == 0:
                continue

            # Size the position at signal price (cash constraint)
            qty = int((self.cash * ALLOCATION_PERCENT) // close)
            if qty <= 0 or close * qty > self.cash:
                self.signal_stats.rejected_capital += 1
                continue

            # Simulate the limit order on the NEXT bar
            if current_idx + 1 >= len(timestamps):
                continue
            next_ts = timestamps[current_idx + 1]

            try:
                next_candle = symbol_frames[symbol].loc[next_ts]
            except KeyError:
                continue

            fill_price = self._simulate_limit_fill(close, next_candle)
            if fill_price is None:
                self.signal_stats.rejected_no_dip += 1
                continue

            # Calculate exits from FILL price (what live bot does)
            tp_price = round(fill_price + (atr_val * TP_ATR_MULTIPLIER), 2)
            sl_price = round(fill_price - (atr_val * SL_ATR_MULTIPLIER), 2)

            # Sanity check
            if tp_price <= fill_price or sl_price >= fill_price:
                continue

            cost = fill_price * qty
            if cost > self.cash:
                self.signal_stats.rejected_capital += 1
                continue

            self.cash -= cost
            self.open_positions.append(OpenPosition(
                symbol=symbol,
                entry_time=next_ts,
                signal_price=close,
                entry_price=fill_price,
                qty=qty,
                take_profit=tp_price,
                stop_loss=sl_price,
            ))
            held_symbols.add(symbol)
            self.signal_stats.filled += 1

    # ── Helpers ──────────────────────────────────────────────────────────

    def _snapshot_equity(self, ts: datetime, prices: dict[str, float]) -> None:
        position_value = sum(
            pos.qty * prices.get(pos.symbol, pos.entry_price)
            for pos in self.open_positions
        )
        total = self.cash + position_value
        self.equity_snapshots.append({"timestamp": ts, "equity": total})

    def _close_all_at_end(self, df: pd.DataFrame, final_ts: datetime) -> None:
        for pos in self.open_positions:
            try:
                last_candle = df.xs(pos.symbol, level="symbol").iloc[-1]
                exit_price = last_candle["close"]
            except (KeyError, IndexError):
                exit_price = pos.entry_price

            trade = Trade(
                symbol=pos.symbol,
                entry_time=pos.entry_time,
                exit_time=final_ts,
                signal_price=pos.signal_price,
                entry_price=pos.entry_price,
                exit_price=exit_price,
                qty=pos.qty,
                side="CLOSE",
            )
            self.closed_trades.append(trade)
            self.cash += exit_price * pos.qty

        self.open_positions.clear()
