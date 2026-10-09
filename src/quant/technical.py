"""The technical engine.

Turns a price history into the compact feature vector the analyst layer reads:
around forty named numbers for one session, instead of years of bars. Every
period, ratio and confirmation threshold comes from `config/thresholds.yaml` --
nothing here decides that RSI is 14 periods, the config does.

Two rules shape the output.

**A feature with insufficient history is None, never a shortened calculation.**
A 200-session average computed off 60 bars is not a rough 200-session average,
it is a different indicator wearing the same name. Nulls flow through to the
completeness score and from there into confidence.

**Nothing here interprets.** `rsi14 = 71.4` is a fact about the price series.
Whether that is overbought, or healthy momentum in a strong uptrend, is a
judgement the analyst layer makes with the rest of the evidence in view.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from src.config import load_config
from src.market_calendar import VN_TZ, session_is_complete  # re-exported: the
# engine's callers ask it about session completeness, not the calendar module.
from src.quant import indicators as ind
from src.quant.store import (
    load_index,
    load_prices,
    price_series,
    upsert_features,
    uses_adjusted_prices,
)

log = logging.getLogger(__name__)

# Trading sessions in a Vietnamese year, once weekends and public holidays are
# removed. A calendar constant, not a tunable: it defines what "52 week" and
# "one year percentile" mean. Overridable in config for a market that differs.
SESSIONS_PER_YEAR = 250


@dataclass(frozen=True)
class TechnicalConfig:
    ma_periods: tuple[int, ...]
    ema_periods: tuple[int, ...]
    rsi_period: int
    macd_fast: int
    macd_slow: int
    macd_signal: int
    atr_period: int
    bollinger_period: int
    bollinger_std: float
    volume_ma_periods: tuple[int, ...]
    volume_expansion_ratio: float
    volume_contraction_ratio: float
    breakout_lookback: int
    breakout_confirm_pct: float
    gap_min_pct: float
    rs_lookbacks: tuple[int, ...]
    sessions_per_year: int = SESSIONS_PER_YEAR

    @classmethod
    def from_thresholds(cls, thresholds: Mapping[str, Any] | None = None) -> "TechnicalConfig":
        raw = dict(thresholds or load_config().thresholds["technical"])
        macd = raw.get("macd", {})
        bollinger = raw.get("bollinger", {})
        return cls(
            ma_periods=tuple(raw["ma_periods"]),
            ema_periods=tuple(raw.get("ema_periods", ())),
            rsi_period=int(raw["rsi_period"]),
            macd_fast=int(macd["fast"]),
            macd_slow=int(macd["slow"]),
            macd_signal=int(macd["signal"]),
            atr_period=int(raw["atr_period"]),
            bollinger_period=int(bollinger["period"]),
            bollinger_std=float(bollinger["std"]),
            volume_ma_periods=tuple(raw.get("volume_ma_periods", ())),
            volume_expansion_ratio=float(raw["volume_expansion_ratio"]),
            volume_contraction_ratio=float(raw["volume_contraction_ratio"]),
            breakout_lookback=int(raw["breakout_lookback_sessions"]),
            breakout_confirm_pct=float(raw["breakout_confirm_close_pct"]),
            gap_min_pct=float(raw["gap_min_pct"]),
            rs_lookbacks=tuple(raw.get("relative_strength_lookback", ())),
            sessions_per_year=int(raw.get("sessions_per_year", SESSIONS_PER_YEAR)),
        )


@dataclass
class FeatureVector:
    ticker: str
    trade_date: date
    features: dict[str, float | None] = field(default_factory=dict)
    bars_available: int = 0
    adjusted_prices: bool = False
    benchmark: str | None = None
    partial_session_excluded: bool = False

    @property
    def completeness(self) -> float:
        """Share of features that could actually be computed, 0..1."""
        if not self.features:
            return 0.0
        present = sum(1 for value in self.features.values() if value is not None)
        return present / len(self.features)

    @property
    def missing(self) -> list[str]:
        return sorted(name for name, value in self.features.items() if value is None)

    def compact(self, digits: int = 4) -> dict[str, float]:
        """What travels onward: computed features only, rounded.

        Nulls are dropped here on purpose. The analyst layer should see a
        smaller vector, not a list of blanks to reason about; the count of what
        went missing already reached it as `completeness`.
        """
        return {
            name: round(value, digits)
            for name, value in self.features.items()
            if value is not None
        }


def _latest(series: pd.Series | None) -> float | None:
    """Last non-NaN-safe value of a series, or None."""
    if series is None or len(series) == 0:
        return None
    value = series.iloc[-1]
    if pd.isna(value):
        return None
    return float(value)


def _flag(condition: bool | None) -> float | None:
    return None if condition is None else (1.0 if condition else 0.0)


def compute_features(
    prices: pd.DataFrame,
    config: TechnicalConfig,
    ticker: str,
    benchmark_prices: pd.DataFrame | None = None,
    benchmark_name: str | None = None,
) -> FeatureVector:
    """Compute one session's features from a price history.

    `prices` must be ascending by trade date and end on the session being
    computed. Pure: it reads no database and no clock.
    """
    if prices.empty:
        raise ValueError(f"{ticker}: no price history to compute features from")

    close = price_series(prices)
    high = prices["high"].astype("float64")
    low = prices["low"].astype("float64")
    open_ = prices["open"].astype("float64")
    volume = prices["volume"].astype("float64")

    vector = FeatureVector(
        ticker=ticker.upper(),
        trade_date=prices.index[-1].date(),
        bars_available=len(prices),
        adjusted_prices=uses_adjusted_prices(prices),
        benchmark=benchmark_name,
    )
    out: dict[str, float | None] = {}

    # -- price and trend ----------------------------------------------------
    out["close"] = _latest(close)
    out["volume"] = _latest(volume)

    moving_averages: dict[int, float | None] = {}
    for period in config.ma_periods:
        line = ind.sma(close, period)
        moving_averages[period] = _latest(line)
        out[f"ma{period}"] = moving_averages[period]
        out[f"price_vs_ma{period}_pct"] = _latest(ind.distance_pct(close, line))

    # Is the trend line itself rising, as distinct from price being above it?
    shortest_ma = min(config.ma_periods) if config.ma_periods else None
    if shortest_ma:
        out[f"ma{shortest_ma}_slope_5d_pct"] = _latest(ind.slope_pct(ind.sma(close, shortest_ma), 5))

    ordered = sorted(config.ma_periods)
    if len(ordered) >= 2:
        values = [moving_averages[p] for p in ordered]
        if any(value is None for value in values):
            out["ma_alignment_bullish"] = None
        else:
            out["ma_alignment_bullish"] = _flag(
                all(earlier > later for earlier, later in zip(values, values[1:]))
            )

    for span in config.ema_periods:
        out[f"ema{span}"] = _latest(ind.ema(close, span))

    # -- momentum -----------------------------------------------------------
    rsi_line = ind.rsi(close, config.rsi_period)
    out[f"rsi{config.rsi_period}"] = _latest(rsi_line)
    out[f"rsi{config.rsi_period}_delta_5d"] = _latest(rsi_line - rsi_line.shift(5))

    macd_line, signal_line, histogram = ind.macd(
        close, config.macd_fast, config.macd_slow, config.macd_signal
    )
    out["macd"] = _latest(macd_line)
    out["macd_signal"] = _latest(signal_line)
    out["macd_hist"] = _latest(histogram)
    out["macd_hist_delta"] = _latest(histogram - histogram.shift(1))
    previous_hist = _latest(histogram.shift(1))
    current_hist = _latest(histogram)
    if previous_hist is None or current_hist is None:
        out["macd_cross_up"] = None
        out["macd_cross_down"] = None
    else:
        out["macd_cross_up"] = _flag(previous_hist <= 0 < current_hist)
        out["macd_cross_down"] = _flag(previous_hist >= 0 > current_hist)

    for period in (5, 20, 60):
        out[f"roc_{period}d"] = _latest(ind.rate_of_change(close, period))

    # -- volatility ---------------------------------------------------------
    atr_line = ind.atr(high, low, close, config.atr_period)
    atr_now = _latest(atr_line)
    out[f"atr{config.atr_period}"] = atr_now
    close_now = out["close"]
    out[f"atr{config.atr_period}_pct"] = (
        atr_now / close_now * 100.0 if atr_now is not None and close_now else None
    )
    out["atr_percentile_1y"] = _latest(ind.rolling_percentile(atr_line, config.sessions_per_year))
    out["realized_vol_20d_pct"] = _latest(ind.realized_volatility(close, 20))

    upper, middle, lower = ind.bollinger(close, config.bollinger_period, config.bollinger_std)
    out["bb_upper"] = _latest(upper)
    out["bb_lower"] = _latest(lower)
    out["bb_percent_b"] = _latest(ind.percent_b(close, upper, lower))
    out["bb_width_pct"] = _latest(((upper - lower) / middle * 100.0).where(middle != 0))

    # -- volume -------------------------------------------------------------
    volume_ma20: pd.Series | None = None
    for period in config.volume_ma_periods:
        line = ind.sma(volume, period)
        out[f"vol_ma{period}"] = _latest(line)
        if period == 20:
            volume_ma20 = line

    volume_ratio = None
    if volume_ma20 is not None:
        ratio_line = (volume / volume_ma20).where(volume_ma20 > 0)
        volume_ratio = _latest(ratio_line)
    out["vol_ratio_20"] = volume_ratio
    out["vol_expansion"] = (
        None if volume_ratio is None else _flag(volume_ratio >= config.volume_expansion_ratio)
    )
    out["vol_contraction"] = (
        None if volume_ratio is None else _flag(volume_ratio <= config.volume_contraction_ratio)
    )

    # Traded value is not on the OHLC feed, so this is close x volume: an
    # estimate of turnover, named as one. The liquidity gate in thresholds.yaml
    # reads a real matched value when a source that carries it is wired up.
    turnover = (close * volume).rolling(window=20, min_periods=20).mean()
    out["avg_turnover_20d_est_vnd"] = _latest(turnover)

    # -- position in range --------------------------------------------------
    year = config.sessions_per_year
    high_52w = ind.rolling_high(high, year)
    low_52w = ind.rolling_low(low, year)
    out["high_52w"] = _latest(high_52w)
    out["low_52w"] = _latest(low_52w)
    out["pct_from_52w_high"] = _latest(ind.distance_pct(close, high_52w))
    out["pct_from_52w_low"] = _latest(ind.distance_pct(close, low_52w))

    # Resistance is the highest price that actually traded in the prior
    # window, not the highest close: a level built from closes gets compared
    # against today's intraday high, which measures two different things.
    breakout_high = ind.rolling_high(high, config.breakout_lookback, exclude_current=True)
    out[f"high_{config.breakout_lookback}d"] = _latest(breakout_high)
    out[f"pct_from_{config.breakout_lookback}d_high"] = _latest(ind.distance_pct(close, breakout_high))
    breakout_level = _latest(breakout_high)
    out[f"breakout_{config.breakout_lookback}d"] = (
        None
        if breakout_level is None or close_now is None
        else _flag(close_now > breakout_level * (1.0 + config.breakout_confirm_pct))
    )

    gap_line = ind.gap_pct(open_, close)
    gap_now = _latest(gap_line)
    out["gap_pct"] = gap_now
    out["gap_flag"] = (
        None if gap_now is None else _flag(abs(gap_now) >= config.gap_min_pct * 100.0)
    )

    # -- relative strength --------------------------------------------------
    for lookback in config.rs_lookbacks:
        key = f"rs_{(benchmark_name or 'benchmark').lower()}_{lookback}"
        if benchmark_prices is None or benchmark_prices.empty:
            out[key] = None
            continue
        # Reindexed onto the ticker's sessions: a benchmark that did not trade
        # on a given day must produce NaN, not a stale index level.
        aligned = price_series(benchmark_prices).reindex(close.index)
        out[key] = _latest(ind.relative_strength(close, aligned, lookback))

    vector.features = out
    return vector


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------

@dataclass
class TechnicalOutcome:
    ticker: str
    trade_date: date | None = None
    completeness: float = 0.0
    bars: int = 0
    written: int = 0
    adjusted_prices: bool = False
    error: str = ""      # could not compute at all
    note: str = ""       # computed, but with a caveat worth carrying forward

    @property
    def ok(self) -> bool:
        return self.trade_date is not None and not self.error


@dataclass
class TechnicalReport:
    outcomes: list[TechnicalOutcome] = field(default_factory=list)

    @property
    def ok_count(self) -> int:
        return sum(1 for outcome in self.outcomes if outcome.ok)

    @property
    def failed(self) -> list[TechnicalOutcome]:
        return [outcome for outcome in self.outcomes if not outcome.ok]


def drop_partial_session(prices: pd.DataFrame, now: datetime | None = None) -> tuple[pd.DataFrame, bool]:
    """Remove a trailing bar for a session that has not closed yet.

    Returns (frame, dropped). Applied to the benchmark too -- comparing a
    finished ticker session against a live index reading would misstate
    relative strength by whatever the index does after lunch.
    """
    if prices.empty:
        return prices, False
    last = prices.index[-1].date()
    if session_is_complete(last, now=now):
        return prices, False
    return prices.iloc[:-1], True


def compute_for_ticker(
    ticker: str,
    as_of: date | None = None,
    config: TechnicalConfig | None = None,
    benchmark_name: str | None = None,
    include_partial_session: bool = False,
    now: datetime | None = None,
    path: Path | None = None,
) -> FeatureVector:
    """Load history for one ticker and compute its feature vector."""
    settings = config or TechnicalConfig.from_thresholds()
    app_config = load_config()
    benchmark = benchmark_name or (
        app_config.watchlist.benchmarks[0] if app_config.watchlist.benchmarks else None
    )

    prices = load_prices(ticker, end=as_of, path=path)
    if prices.empty:
        raise ValueError(
            f"{ticker.upper()}: no bars in prices_daily"
            f"{' up to ' + as_of.isoformat() if as_of else ''}. Run `collect` first."
        )

    partial_dropped = False
    if not include_partial_session:
        prices, partial_dropped = drop_partial_session(prices, now=now)
        if prices.empty:
            raise ValueError(
                f"{ticker.upper()}: the only bar available is an unfinished session. "
                "Wait for the close, or pass include_partial_session to compute on it anyway."
            )

    benchmark_prices = load_index(benchmark, end=as_of, path=path) if benchmark else None
    if benchmark_prices is not None and partial_dropped:
        benchmark_prices, _ = drop_partial_session(benchmark_prices, now=now)
    vector = compute_features(
        prices=prices,
        config=settings,
        ticker=ticker,
        benchmark_prices=benchmark_prices,
        benchmark_name=benchmark,
    )
    vector.partial_session_excluded = partial_dropped
    # Self-describing output: a stored vector says whether the session behind
    # it had closed. Without this, an intraday snapshot and a settled session
    # are the same row, and only the wall clock at write time told them apart.
    vector.features["session_partial"] = _flag(not session_is_complete(vector.trade_date, now=now))
    return vector


def run_technical(
    tickers: Sequence[str] | None = None,
    as_of: date | None = None,
    persist: bool = True,
    include_partial_session: bool = False,
    now: datetime | None = None,
    path: Path | None = None,
) -> TechnicalReport:
    """Compute and store features for every active ticker."""
    app_config = load_config()
    settings = TechnicalConfig.from_thresholds()
    scope = list(tickers) if tickers is not None else list(app_config.watchlist.active_tickers)
    report = TechnicalReport()

    for ticker in scope:
        outcome = TechnicalOutcome(ticker=ticker.upper())
        try:
            vector = compute_for_ticker(
                ticker,
                as_of=as_of,
                config=settings,
                include_partial_session=include_partial_session,
                now=now,
                path=path,
            )
        except ValueError as exc:
            outcome.error = str(exc)
            log.warning("%s: %s", ticker, exc)
            report.outcomes.append(outcome)
            continue

        outcome.trade_date = vector.trade_date
        outcome.completeness = vector.completeness
        outcome.bars = vector.bars_available
        outcome.adjusted_prices = vector.adjusted_prices
        notes = []
        if not vector.adjusted_prices:
            notes.append("unadjusted closes -- corporate actions will distort every average")
        if vector.partial_session_excluded:
            notes.append("today's unfinished session excluded")
        outcome.note = "; ".join(notes)
        if persist:
            outcome.written = upsert_features(
                vector.ticker, vector.trade_date, vector.features, path=path
            )
        report.outcomes.append(outcome)

    return report
