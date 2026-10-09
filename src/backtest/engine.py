"""Backtest engine (§30).

Turns each deterministic pattern into a measurable claim: given this signal, on
the date it could first have been acted on, what happened over the next 1, 3,
5, 10 and 20 sessions?

Every assumption is stated and configurable, because a backtest that does not
say how it entered, at what price, and what it paid is an illustration rather
than a measurement:

    signal timestamp   the date the pattern became KNOWABLE, never the date it
                       printed and never the date it was later confirmed
    information set    only bars up to and including the signal date
    entry              the next session's open by default (you learn a signal
                       at the close; you can trade the open after it)
    exit               the close, `horizon` sessions after the signal
    costs              brokerage both ways, the sell-side transfer tax, and
                       slippage on both fills, all deducted from the return

The look-ahead rule is enforced structurally rather than by discipline. Signals
carry the status that was knowable on their own date -- a bull trap that later
confirmed enters the sample as CANDIDATE, and its confirmation enters
separately, on the later date, as its own signal. The two are measured apart
because they are different trades: one is available on the day, the other only
after the market has already moved.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Callable, Iterable, Mapping, Sequence

import numpy as np
import pandas as pd

from src.config import load_config
from src.quant import indicators as ind
from src.quant.behaviour import (
    ACCUMULATION_LIKE,
    DISTRIBUTION_LIKE,
    BehaviourConfig,
    compute_behaviour,
)
from src.quant.divergence import (
    BEARISH,
    BULLISH,
    DivergenceConfig,
    find_divergences,
)
from src.quant.events import (
    BREAKDOWN,
    BREAKOUT,
    ShakeoutConfig,
    find_shakeouts,
    track_break_events,
)
from src.quant.patterns import (
    STATUS_CANDIDATE,
    STATUS_CONFIRMED,
    PatternConfig,
    bear_trap,
    bull_trap,
    resolve_trap,
)
from src.quant.regime import RegimeConfig, RegimeInputs, classify
from src.quant.store import price_series
from src.quant.technical import TechnicalConfig

log = logging.getLogger(__name__)

ENTRY_NEXT_OPEN = "next_open"
ENTRY_SIGNAL_CLOSE = "signal_close"

INSUFFICIENT = "INSUFFICIENT_SAMPLE"


@dataclass(frozen=True)
class BacktestConfig:
    horizons: tuple[int, ...]
    entry: str
    exit: str
    brokerage_fee_pct: float
    sell_tax_pct: float
    slippage_pct: float
    min_sample_size: int
    robust_sample_size: int
    out_of_sample_fraction: float

    @classmethod
    def from_thresholds(cls, thresholds: Mapping[str, Any] | None = None) -> "BacktestConfig":
        raw = dict(thresholds or load_config().thresholds["backtest"])
        return cls(
            horizons=tuple(raw["horizons"]),
            entry=str(raw["entry"]),
            exit=str(raw["exit"]),
            brokerage_fee_pct=float(raw["brokerage_fee_pct"]),
            sell_tax_pct=float(raw["sell_tax_pct"]),
            slippage_pct=float(raw["slippage_pct"]),
            min_sample_size=int(raw["min_sample_size"]),
            robust_sample_size=int(raw["robust_sample_size"]),
            out_of_sample_fraction=float(raw["out_of_sample_fraction"]),
        )

    @property
    def round_trip_cost_pct(self) -> float:
        """What a completed round trip costs, in percent of notional.

        Brokerage is charged on both legs, the transfer tax on the sell only,
        and slippage is assumed on both fills. Reporting gross returns as if
        they were trading returns is the most common way a backtest flatters
        itself in a market with costs this size.
        """
        return self.brokerage_fee_pct * 2 + self.sell_tax_pct + self.slippage_pct * 2

    def describe_assumptions(self) -> dict[str, Any]:
        return {
            "signal_timestamp": "the session the pattern became knowable",
            "information_set": "bars up to and including the signal date",
            "entry": self.entry,
            "exit": f"{self.exit} at signal date + horizon sessions",
            "price_field": self.exit,
            "brokerage_fee_pct": self.brokerage_fee_pct,
            "sell_tax_pct": self.sell_tax_pct,
            "slippage_pct": self.slippage_pct,
            "round_trip_cost_pct": round(self.round_trip_cost_pct, 3),
            "horizons": list(self.horizons),
        }


LONG = "LONG"
SHORT = "SHORT"

# Which way each pattern points. Measuring a long return after a distribution
# signal would score the pattern as a failure precisely when it worked.
#
# Note on SHORT: Vietnam's cash market has no retail short selling, so a SHORT
# row measures whether the signal predicted a fall -- useful for calibration
# and for deciding what to avoid -- not a strategy most participants can trade.
PATTERN_DIRECTION: dict[str, str] = {
    "breakout": LONG,
    "failed_breakout": SHORT,
    "breakdown": SHORT,
    "failed_breakdown": LONG,
    "bull_trap": SHORT,
    "bear_trap": LONG,
    "shakeout": LONG,
    "bullish_divergence": LONG,
    "bearish_divergence": SHORT,
    "accumulation_like": LONG,
    "distribution_like": SHORT,
    "baseline": LONG,
}


def direction_of(pattern: str) -> str:
    """The side a pattern implies, matched on the longest declared prefix."""
    matches = [name for name in PATTERN_DIRECTION if pattern.startswith(name)]
    if not matches:
        return LONG
    return PATTERN_DIRECTION[max(matches, key=len)]


@dataclass
class Signal:
    """One historical occurrence of a pattern, dated when it became knowable."""

    ticker: str
    pattern: str
    status: str
    signal_date: date
    regime: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    @property
    def direction(self) -> str:
        return direction_of(self.pattern)

    @property
    def key(self) -> str:
        return f"{self.pattern}:{self.status}"


@dataclass
class Outcome:
    signal: Signal
    entry_date: date | None = None
    entry_price: float | None = None
    returns: dict[int, float] = field(default_factory=dict)
    mfe: dict[int, float] = field(default_factory=dict)
    mae: dict[int, float] = field(default_factory=dict)
    max_drawdown: dict[int, float] = field(default_factory=dict)

    @property
    def tradeable(self) -> bool:
        return self.entry_price is not None and bool(self.returns)


@dataclass
class HorizonStats:
    horizon: int
    sample_size: int = 0
    win_rate: float | None = None
    median_return: float | None = None
    mean_return: float | None = None
    average_gain: float | None = None
    average_loss: float | None = None
    expectancy: float | None = None
    profit_factor: float | None = None
    max_favourable_excursion: float | None = None
    max_adverse_excursion: float | None = None
    max_drawdown: float | None = None
    stdev: float | None = None
    standard_error: float | None = None
    t_stat: float | None = None

    @property
    def distinguishable_from_noise(self) -> bool:
        """Whether the mean is more than two standard errors from zero.

        A 0.3-point edge on a sample whose per-trade spread is 6 points is
        indistinguishable from luck however many occurrences there are, and
        reporting it without this test invites reading it as a finding.
        """
        return self.t_stat is not None and abs(self.t_stat) >= 2.0

    @property
    def sufficient(self) -> bool:
        return self.sample_size > 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "horizon": self.horizon,
            "sample_size": self.sample_size,
            "win_rate": _round(self.win_rate),
            "median_return": _round(self.median_return),
            "mean_return": _round(self.mean_return),
            "average_gain": _round(self.average_gain),
            "average_loss": _round(self.average_loss),
            "expectancy": _round(self.expectancy),
            "profit_factor": _round(self.profit_factor),
            "max_favourable_excursion": _round(self.max_favourable_excursion),
            "max_adverse_excursion": _round(self.max_adverse_excursion),
            "max_drawdown": _round(self.max_drawdown),
            "stdev": _round(self.stdev),
            "standard_error": _round(self.standard_error, 3),
            "t_stat": _round(self.t_stat),
            "distinguishable_from_noise": self.distinguishable_from_noise,
        }


@dataclass
class PatternStats:
    pattern: str
    status: str
    sample_size: int = 0
    horizons: dict[int, HorizonStats] = field(default_factory=dict)
    by_regime: dict[str, int] = field(default_factory=dict)
    regime_expectancy: dict[str, float | None] = field(default_factory=dict)
    tickers: int = 0
    first_signal: date | None = None
    last_signal: date | None = None

    @property
    def label(self) -> str:
        return f"{self.pattern} [{self.status}]"

    def sample_verdict(self, config: BacktestConfig) -> str:
        if self.sample_size < config.min_sample_size:
            return INSUFFICIENT
        if self.sample_size >= config.robust_sample_size:
            return "ADEQUATE"
        return "THIN"

    def as_dict(self, config: BacktestConfig) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "status": self.status,
            "sample_size": self.sample_size,
            "sample_verdict": self.sample_verdict(config),
            "tickers": self.tickers,
            "first_signal": self.first_signal.isoformat() if self.first_signal else None,
            "last_signal": self.last_signal.isoformat() if self.last_signal else None,
            "horizons": {h: s.as_dict() for h, s in sorted(self.horizons.items())},
            "by_regime": dict(self.by_regime),
            "regime_expectancy": {k: _round(v) for k, v in self.regime_expectancy.items()},
        }


def _round(value: float | None, digits: int = 2) -> float | None:
    return None if value is None else round(float(value), digits)


def _mean_or_none(values: Sequence[float]) -> float | None:
    return statistics.fmean(values) if values else None


def _min_or_none(values: Sequence[float]) -> float | None:
    return min(values) if values else None


# ---------------------------------------------------------------------------
# Regime series
# ---------------------------------------------------------------------------

def regime_series(index_frame: pd.DataFrame, config: RegimeConfig | None = None) -> pd.Series:
    """The market regime on every historical session.

    Built from the index alone. Breadth is not available historically -- it is
    only pushed live on the websocket -- so the participation axis is missing
    from every past date and the classifier is asked to work without it. That
    is a real limitation of the regime breakdown below, not a rounding detail:
    DISTRIBUTION in particular is defined by breadth, and cannot be identified
    from an index series alone.
    """
    settings = config or RegimeConfig.from_thresholds()
    if index_frame.empty:
        return pd.Series(dtype="object")

    close = price_series(index_frame)
    ma50 = ind.sma(close, 50)
    ma200 = ind.sma(close, 200)
    roc20 = ind.rate_of_change(close, 20)
    volume = index_frame["volume"].astype("float64")
    vol_ratio = (volume / ind.sma(volume, 20)).where(ind.sma(volume, 20) > 0)

    labels: list[str | None] = []
    for stamp in index_frame.index:
        inputs = RegimeInputs(
            close=_value(close, stamp),
            ma50=_value(ma50, stamp),
            ma200=_value(ma200, stamp),
            roc_20d=_value(roc20, stamp),
            breadth_ratio=None,
            volume_vs_ma20=_value(vol_ratio, stamp),
        )
        if inputs.close is None or inputs.ma200 is None:
            labels.append(None)
            continue
        labels.append(classify(inputs, settings, stamp.date()).regime)

    return pd.Series(labels, index=index_frame.index, dtype="object")


def _value(series: pd.Series, stamp: pd.Timestamp) -> float | None:
    if stamp not in series.index:
        return None
    value = series.loc[stamp]
    return None if pd.isna(value) else float(value)


# ---------------------------------------------------------------------------
# Signal generation
# ---------------------------------------------------------------------------

def generate_signals(
    frame: pd.DataFrame,
    ticker: str,
    benchmark: pd.DataFrame | None = None,
    pattern_config: PatternConfig | None = None,
    shakeout_config: ShakeoutConfig | None = None,
    divergence_config: DivergenceConfig | None = None,
    behaviour_config: BehaviourConfig | None = None,
    technical_config: TechnicalConfig | None = None,
    breakout_windows: Sequence[int] = (20, 60),
) -> list[Signal]:
    """Every pattern occurrence in the history, dated when it became knowable.

    Each signal's date is the first session on which the pattern could have
    been seen. For an event with a lifecycle this means the setup, the
    confirmation and the failure produce three separate signals on three
    separate dates -- never one signal wearing its final outcome.
    """
    patterns = pattern_config or PatternConfig.from_thresholds()
    shakeouts = shakeout_config or ShakeoutConfig.from_thresholds()
    divergences = divergence_config or DivergenceConfig.from_thresholds()
    behaviour = behaviour_config or BehaviourConfig.from_thresholds()
    technical = technical_config or TechnicalConfig.from_thresholds()

    if frame.empty or len(frame) < 40:
        return []

    signals: list[Signal] = []
    close = price_series(frame)
    volume = frame["volume"].astype("float64")
    volume_ratio = (volume / ind.sma(volume, 20)).where(ind.sma(volume, 20) > 0)

    # -- breakout and breakdown lifecycles ---------------------------------
    for window in breakout_windows:
        for kind in (BREAKOUT, BREAKDOWN):
            for event in track_break_events(frame, window, kind, config=patterns):
                base = f"{kind.lower()}_{window}d"
                signals.append(
                    Signal(ticker, base, "ATTEMPT", event.setup_date,
                           metadata={"level": event.level, "window": window})
                )
                if event.confirmation_date:
                    signals.append(
                        Signal(ticker, base, "CONFIRMED", event.confirmation_date,
                               metadata={"level": event.level, "window": window})
                    )
                if event.failure_date:
                    signals.append(
                        Signal(ticker, f"failed_{base}", "FAILED", event.failure_date,
                               metadata={"level": event.level, "window": window})
                    )

    # -- traps, evaluated one session at a time ----------------------------
    # The rolling levels are built once for the whole history rather than
    # rebuilt inside every session's call. Walking a decade of bars with an
    # O(n) recomputation per session is quadratic, and on 2,700 sessions that
    # is the difference between a minute and an hour.
    trap_high = ind.rolling_high(frame["high"].astype("float64"), 60, exclude_current=True)
    trap_low = ind.rolling_low(frame["low"].astype("float64"), 60, exclude_current=True)

    for position in range(max(60, patterns.swing_strength * 2), len(frame)):
        window_frame = frame.iloc[: position + 1]
        today = frame.index[position].date()
        ratio = _value(volume_ratio, frame.index[position])

        for detector, name, levels in (
            (bull_trap, "bull_trap", trap_high),
            (bear_trap, "bear_trap", trap_low),
        ):
            level = levels.iloc[position]
            if pd.isna(level):
                continue
            read = detector(window_frame, patterns, window=60, volume_ratio=ratio,
                            level=float(level))
            if read.status != STATUS_CANDIDATE:
                continue
            signals.append(
                Signal(ticker, name, STATUS_CANDIDATE, today,
                       metadata={"score": read.score, "level": read.level})
            )
            # The confirmation is a separate trade on a later date. Resolving
            # it here and stamping the candidate date would be the exact
            # leakage this engine exists to prevent.
            resolution_frame = frame.iloc[: position + 1 + patterns.trap_confirmation_window]
            status = resolve_trap(resolution_frame, position, read, patterns)
            if status == STATUS_CONFIRMED:
                resolved_index = min(position + patterns.trap_confirmation_window, len(frame) - 1)
                signals.append(
                    Signal(ticker, name, STATUS_CONFIRMED, frame.index[resolved_index].date(),
                           metadata={"score": read.score, "level": read.level})
                )

    # -- shakeouts ----------------------------------------------------------
    for shakeout in find_shakeouts(frame, config=shakeouts, pattern_config=patterns,
                                   volume_ratios=volume_ratio):
        signals.append(
            Signal(ticker, "shakeout", STATUS_CANDIDATE, shakeout.setup_date,
                   metadata={"score": shakeout.score, "level": shakeout.level})
        )
        if shakeout.status == STATUS_CONFIRMED and shakeout.resolved_on:
            signals.append(
                Signal(ticker, "shakeout", STATUS_CONFIRMED, shakeout.resolved_on,
                       metadata={"score": shakeout.score, "level": shakeout.level})
            )

    # -- divergences --------------------------------------------------------
    for divergence in find_divergences(frame, config=divergences, pattern_config=patterns,
                                       technical_config=technical):
        name = "bullish_divergence" if divergence.kind == BULLISH else "bearish_divergence"
        signals.append(
            Signal(ticker, name, STATUS_CANDIDATE, divergence.confirmed_on,
                   metadata={"indicator": divergence.indicator, "strength": divergence.strength})
        )
        if divergence.status == STATUS_CONFIRMED and divergence.resolved_on:
            signals.append(
                Signal(ticker, name, STATUS_CONFIRMED, divergence.resolved_on,
                       metadata={"indicator": divergence.indicator, "strength": divergence.strength})
            )

    # -- behaviour transitions ---------------------------------------------
    relative = None
    if benchmark is not None and not benchmark.empty:
        aligned = price_series(benchmark).reindex(close.index)
        relative = ind.relative_strength(close, aligned, 20)

    atr_series = ind.atr(
        frame["high"].astype("float64"),
        frame["low"].astype("float64"),
        close,
        technical.atr_period,
    )

    previous_verdict = None
    for position in range(behaviour.window * 2, len(frame)):
        window_frame = frame.iloc[: position + 1]
        read = compute_behaviour(
            window_frame,
            relative_strength=_value(relative, frame.index[position]) if relative is not None else None,
            config=behaviour,
            atr=atr_series,
        )
        if read.verdict != previous_verdict and read.verdict in (ACCUMULATION_LIKE, DISTRIBUTION_LIKE):
            signals.append(
                Signal(ticker, read.verdict.lower(), "SETUP", frame.index[position].date(),
                       metadata={"accumulation": read.accumulation_like_score,
                                 "distribution": read.distribution_like_score})
            )
        previous_verdict = read.verdict

    signals.sort(key=lambda s: (s.signal_date, s.pattern, s.status))
    return signals


# ---------------------------------------------------------------------------
# Outcomes
# ---------------------------------------------------------------------------

def evaluate_signal(signal: Signal, frame: pd.DataFrame, config: BacktestConfig) -> Outcome:
    """Forward returns for one signal, net of costs.

    Entry is the next session's open by default: the signal is known at a
    close, so the first tradeable price is the open after it. A signal on the
    last available session has no entry and is dropped rather than filled at a
    price that does not exist.
    """
    outcome = Outcome(signal=signal)
    stamp = pd.Timestamp(signal.signal_date)
    if stamp not in frame.index:
        return outcome

    position = frame.index.get_loc(stamp)
    if config.entry == ENTRY_NEXT_OPEN:
        entry_position = position + 1
        if entry_position >= len(frame):
            return outcome
        entry_price = float(frame["open"].iloc[entry_position])
    else:
        entry_position = position
        entry_price = float(price_series(frame).iloc[position])

    if entry_price <= 0:
        return outcome

    outcome.entry_date = frame.index[entry_position].date()
    outcome.entry_price = entry_price

    close = price_series(frame)
    high = frame["high"].astype("float64")
    low = frame["low"].astype("float64")

    # A bearish pattern is scored on the fall it predicted, not on what a long
    # position would have lost. Measuring every pattern as a long marks the
    # bearish ones as failures exactly when they were right.
    sign = 1.0 if signal.direction == LONG else -1.0

    for horizon in config.horizons:
        exit_position = entry_position + horizon
        if exit_position >= len(frame):
            continue

        gross = (float(close.iloc[exit_position]) - entry_price) / entry_price * 100.0
        outcome.returns[horizon] = sign * gross - config.round_trip_cost_pct

        path = slice(entry_position, exit_position + 1)
        best = (float(high.iloc[path].max()) - entry_price) / entry_price * 100.0
        worst = (float(low.iloc[path].min()) - entry_price) / entry_price * 100.0
        # Favourable and adverse swap sides for a short.
        outcome.mfe[horizon] = best if sign > 0 else -worst
        outcome.mae[horizon] = worst if sign > 0 else -best

        closes = close.iloc[path] * sign
        running_peak = closes.cummax()
        drawdown = ((closes - running_peak) / running_peak.abs() * 100.0).min()
        outcome.max_drawdown[horizon] = float(drawdown)

    return outcome


def summarise(outcomes: Sequence[Outcome], config: BacktestConfig) -> dict[str, PatternStats]:
    """Aggregate outcomes into per-pattern, per-horizon statistics."""
    grouped: dict[str, list[Outcome]] = {}
    for outcome in outcomes:
        if outcome.tradeable:
            grouped.setdefault(outcome.signal.key, []).append(outcome)

    results: dict[str, PatternStats] = {}
    for key, group in grouped.items():
        pattern, status = key.split(":", 1)
        stats = PatternStats(pattern=pattern, status=status, sample_size=len(group))
        stats.tickers = len({o.signal.ticker for o in group})
        dates = sorted(o.signal.signal_date for o in group)
        stats.first_signal, stats.last_signal = dates[0], dates[-1]

        for regime in (o.signal.regime for o in group):
            label = regime or "UNKNOWN"
            stats.by_regime[label] = stats.by_regime.get(label, 0) + 1

        for horizon in config.horizons:
            returns = [o.returns[horizon] for o in group if horizon in o.returns]
            if not returns:
                stats.horizons[horizon] = HorizonStats(horizon=horizon)
                continue

            wins = [r for r in returns if r > 0]
            losses = [r for r in returns if r <= 0]
            gross_profit = sum(wins)
            gross_loss = abs(sum(losses))

            horizon_stats = HorizonStats(
                horizon=horizon,
                sample_size=len(returns),
                win_rate=len(wins) / len(returns) * 100.0,
                median_return=statistics.median(returns),
                mean_return=statistics.fmean(returns),
                average_gain=statistics.fmean(wins) if wins else None,
                average_loss=statistics.fmean(losses) if losses else None,
                profit_factor=(gross_profit / gross_loss) if gross_loss > 0 else None,
                # Excursions can be absent where returns are not -- an outcome
                # assembled by hand, or a horizon that ran off the end of the
                # data. Reporting None beats crashing the whole run.
                max_favourable_excursion=_mean_or_none(
                    [o.mfe[horizon] for o in group if horizon in o.mfe]
                ),
                max_adverse_excursion=_mean_or_none(
                    [o.mae[horizon] for o in group if horizon in o.mae]
                ),
                max_drawdown=_min_or_none(
                    [o.max_drawdown[horizon] for o in group if horizon in o.max_drawdown]
                ),
            )
            if len(returns) > 1:
                horizon_stats.stdev = statistics.stdev(returns)
                horizon_stats.standard_error = horizon_stats.stdev / (len(returns) ** 0.5)
                if horizon_stats.standard_error > 0:
                    horizon_stats.t_stat = horizon_stats.mean_return / horizon_stats.standard_error

            # Expectancy per trade: what one occurrence is worth on average,
            # which is the only number that combines hit rate with size.
            win_rate = len(wins) / len(returns)
            horizon_stats.expectancy = (
                win_rate * (horizon_stats.average_gain or 0.0)
                + (1 - win_rate) * (horizon_stats.average_loss or 0.0)
            )
            stats.horizons[horizon] = horizon_stats

        # Expectancy by regime, at the middle horizon.
        middle = config.horizons[len(config.horizons) // 2]
        by_regime: dict[str, list[float]] = {}
        for outcome in group:
            if middle in outcome.returns:
                by_regime.setdefault(outcome.signal.regime or "UNKNOWN", []).append(outcome.returns[middle])
        stats.regime_expectancy = {
            regime: statistics.fmean(values) if values else None for regime, values in by_regime.items()
        }

        results[key] = stats

    return dict(sorted(results.items()))


def baseline_signals(frame: pd.DataFrame, ticker: str, every: int = 1) -> list[Signal]:
    """Every session as a signal, for use as a control.

    Without this, a pattern showing -1.5% at T+5 looks like a failure. If the
    average session in the same sample also returned -1.5%, the pattern is
    simply neutral and the number was measuring the market, not the setup.
    Nothing is interpretable without it.
    """
    return [
        Signal(ticker, "baseline", "ALL_SESSIONS", stamp.date())
        for stamp in frame.index[::every]
    ]


def run_backtest(
    frames: Mapping[str, pd.DataFrame],
    benchmark: pd.DataFrame | None = None,
    config: BacktestConfig | None = None,
    regimes: pd.Series | None = None,
    include_baseline: bool = True,
    **generator_kwargs: Any,
) -> tuple[dict[str, PatternStats], list[Outcome]]:
    """Generate signals across every ticker and measure what followed."""
    settings = config or BacktestConfig.from_thresholds()
    all_outcomes: list[Outcome] = []

    for ticker, frame in frames.items():
        signals = generate_signals(frame, ticker, benchmark=benchmark, **generator_kwargs)
        if include_baseline:
            signals = signals + baseline_signals(frame, ticker)
        for signal in signals:
            if regimes is not None:
                stamp = pd.Timestamp(signal.signal_date)
                if stamp in regimes.index:
                    value = regimes.loc[stamp]
                    signal.regime = None if pd.isna(value) else str(value)
            all_outcomes.append(evaluate_signal(signal, frame, settings))

    return summarise(all_outcomes, settings), all_outcomes


def split_sample(
    outcomes: Sequence[Outcome], config: BacktestConfig
) -> tuple[list[Outcome], list[Outcome]]:
    """Chronological in-sample / out-of-sample split.

    Chronological, never random: shuffling dates lets a strategy learn from
    sessions that came after the ones it is tested on.
    """
    dated = sorted((o for o in outcomes if o.tradeable), key=lambda o: o.signal.signal_date)
    if not dated:
        return [], []
    cut = int(len(dated) * (1 - config.out_of_sample_fraction))
    return dated[:cut], dated[cut:]
