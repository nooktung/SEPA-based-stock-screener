"""Divergence between price and an oscillator (§18).

A divergence is a disagreement between two *confirmed swing pivots* and the
oscillator readings at those same pivots. Price made a lower low; RSI made a
higher low. That is the whole idea, and the discipline is entirely in what
counts as a pivot.

Comparing adjacent candles instead would find a divergence on almost any pair
of days, which is why this module refuses to look at anything but pivots the
structure engine has confirmed.

The timing rule is the one that matters for backtesting. A swing low is not
knowable on the day it prints -- it needs `swing_strength` sessions after it to
become a pivot at all. So a divergence between two lows carries two dates:

    trade_date     the session the second pivot printed
    confirmed_on   the session the divergence could first be seen

Everything downstream -- status, backtest entry, statistics -- keys off
`confirmed_on`. Using `trade_date` as the moment of knowledge would hand the
strategy several free sessions of hindsight and make every result meaningless.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from typing import Any, Literal, Mapping, Sequence

import numpy as np
import pandas as pd

from src.config import load_config
from src.quant import indicators as ind
from src.quant.patterns import (
    STATUS_CANDIDATE,
    STATUS_CONFIRMED,
    STATUS_INVALIDATED,
    PatternConfig,
    Pivot,
    find_pivots,
    pivots_known_by,
)
from src.quant.store import price_series
from src.quant.technical import TechnicalConfig

log = logging.getLogger(__name__)

STATUS_EXPIRED = "EXPIRED"

BULLISH = "BULLISH"
BEARISH = "BEARISH"

INDICATOR_RSI = "rsi"
INDICATOR_MACD_HIST = "macd_hist"


@dataclass(frozen=True)
class DivergenceConfig:
    min_price_change_pct: float
    min_indicator_change: float
    max_pivot_gap_sessions: int
    confirmation_window: int
    expiry_sessions: int

    @classmethod
    def from_thresholds(cls, thresholds: Mapping[str, Any] | None = None) -> "DivergenceConfig":
        raw = dict((thresholds or load_config().thresholds["patterns"])["divergence"])
        return cls(
            min_price_change_pct=float(raw["min_price_change_pct"]),
            min_indicator_change=float(raw["min_indicator_change"]),
            max_pivot_gap_sessions=int(raw["max_pivot_gap_sessions"]),
            confirmation_window=int(raw["confirmation_window"]),
            expiry_sessions=int(raw["expiry_sessions"]),
        )


@dataclass
class Divergence:
    kind: Literal["BULLISH", "BEARISH"]
    indicator: str
    price_pivot_1: float
    price_pivot_2: float
    pivot_1_date: date
    pivot_2_date: date
    indicator_value_1: float
    indicator_value_2: float
    trade_date: date          # when the second pivot printed
    confirmed_on: date        # when the divergence first became knowable
    status: str = STATUS_CANDIDATE
    strength: float = 0.0
    resolved_on: date | None = None

    @property
    def price_change_pct(self) -> float:
        return (self.price_pivot_2 - self.price_pivot_1) / abs(self.price_pivot_1) * 100.0

    @property
    def indicator_change(self) -> float:
        return self.indicator_value_2 - self.indicator_value_1

    @property
    def knowledge_lag_sessions(self) -> int:
        """How many sessions passed between the pattern printing and being visible."""
        return (self.confirmed_on - self.trade_date).days

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "indicator": self.indicator,
            "price_pivot_1": round(self.price_pivot_1, 2),
            "price_pivot_2": round(self.price_pivot_2, 2),
            "pivot_1_date": self.pivot_1_date.isoformat(),
            "pivot_2_date": self.pivot_2_date.isoformat(),
            "indicator_value_1": round(self.indicator_value_1, 4),
            "indicator_value_2": round(self.indicator_value_2, 4),
            "trade_date": self.trade_date.isoformat(),
            "confirmed_on": self.confirmed_on.isoformat(),
            "status": self.status,
            "strength": self.strength,
            "resolved_on": self.resolved_on.isoformat() if self.resolved_on else None,
        }


def indicator_series(frame: pd.DataFrame, indicator: str, config: TechnicalConfig) -> tuple[pd.Series, float]:
    """The oscillator and the unit its changes should be measured in.

    RSI is already a bounded 0-100 scale, so a change of one means one point.
    The MACD histogram has no fixed scale -- it depends on the price level of
    whatever is being measured -- so its changes are expressed in standard
    deviations of its own recent history. Without that, one threshold could not
    serve both a 13,000-dong stock and a 72,000-dong one.
    """
    close = price_series(frame)
    if indicator == INDICATOR_RSI:
        return ind.rsi(close, config.rsi_period), 1.0
    if indicator == INDICATOR_MACD_HIST:
        _, _, histogram = ind.macd(close, config.macd_fast, config.macd_slow, config.macd_signal)
        scale = float(histogram.std(ddof=0))
        return histogram, scale if scale > 0 else 1.0
    raise ValueError(f"unknown divergence indicator '{indicator}'")


def _pair_pivots(pivots: Sequence[Pivot], kind: str) -> list[tuple[Pivot, Pivot]]:
    same = [p for p in pivots if p.kind == kind]
    return list(zip(same, same[1:]))


def find_divergences(
    frame: pd.DataFrame,
    as_of: date | None = None,
    indicators: Sequence[str] = (INDICATOR_RSI, INDICATOR_MACD_HIST),
    config: DivergenceConfig | None = None,
    pattern_config: PatternConfig | None = None,
    technical_config: TechnicalConfig | None = None,
) -> list[Divergence]:
    """Every divergence knowable on or before `as_of`, with its status settled.

    Pure with respect to time: called with the same frame and the same `as_of`
    it returns the same answer, and it cannot see a pivot that had not been
    confirmed by then.
    """
    settings = config or DivergenceConfig.from_thresholds()
    patterns = pattern_config or PatternConfig.from_thresholds()
    technical = technical_config or TechnicalConfig.from_thresholds()

    if frame.empty:
        return []
    as_of = as_of or frame.index[-1].date()

    # Only bars up to as_of exist. Everything after is the future.
    visible = frame.loc[: pd.Timestamp(as_of)]
    if visible.empty:
        return []

    pivots = pivots_known_by(find_pivots(visible, patterns.swing_strength), as_of)
    if len(pivots) < 2:
        return []

    positions = {stamp.date(): i for i, stamp in enumerate(visible.index)}
    close = price_series(visible)
    found: list[Divergence] = []

    for indicator in indicators:
        series, unit = indicator_series(visible, indicator, technical)

        for kind, pivot_kind in ((BEARISH, "HIGH"), (BULLISH, "LOW")):
            for first, second in _pair_pivots(pivots, pivot_kind):
                gap = positions[second.trade_date] - positions[first.trade_date]
                if gap > settings.max_pivot_gap_sessions:
                    continue

                value_1 = series.get(pd.Timestamp(first.trade_date))
                value_2 = series.get(pd.Timestamp(second.trade_date))
                if value_1 is None or value_2 is None or pd.isna(value_1) or pd.isna(value_2):
                    continue

                price_change = (second.price - first.price) / abs(first.price) * 100.0
                indicator_change = (float(value_2) - float(value_1)) / unit

                if abs(price_change) < settings.min_price_change_pct:
                    continue
                if abs(indicator_change) < settings.min_indicator_change:
                    continue

                # Bearish: price higher, oscillator lower. Bullish: the mirror.
                if kind == BEARISH and not (price_change > 0 and indicator_change < 0):
                    continue
                if kind == BULLISH and not (price_change < 0 and indicator_change > 0):
                    continue

                divergence = Divergence(
                    kind=kind,
                    indicator=indicator,
                    price_pivot_1=float(first.price),
                    price_pivot_2=float(second.price),
                    pivot_1_date=first.trade_date,
                    pivot_2_date=second.trade_date,
                    indicator_value_1=float(value_1),
                    indicator_value_2=float(value_2),
                    trade_date=second.trade_date,
                    confirmed_on=second.confirmed_on,
                    strength=round(min(100.0, abs(indicator_change) * 20.0), 1),
                )
                _resolve(divergence, visible, close, positions, settings)
                found.append(divergence)

    found.sort(key=lambda d: (d.confirmed_on, d.kind, d.indicator))
    return found


def _resolve(
    divergence: Divergence,
    frame: pd.DataFrame,
    close: pd.Series,
    positions: Mapping[date, int],
    config: DivergenceConfig,
) -> None:
    """Settle a divergence using only the sessions after it became knowable.

    Confirmation for a bullish divergence is a close above the reaction high
    that sat between the two lows -- price actually turning, not merely
    diverging. Invalidation is a close below the lower low, which says the
    divergence was noise. Neither within the expiry window leaves it EXPIRED,
    which is a real outcome and must be counted as one: dropping unresolved
    signals from a sample flatters every statistic computed from it.
    """
    start = positions.get(divergence.confirmed_on)
    if start is None:
        return

    after = close.iloc[start + 1 : start + 1 + config.expiry_sessions]
    if after.empty:
        divergence.status = STATUS_CANDIDATE
        return

    between = frame.loc[pd.Timestamp(divergence.pivot_1_date) : pd.Timestamp(divergence.pivot_2_date)]
    if divergence.kind == BULLISH:
        trigger = float(between["high"].max())
        invalidation = divergence.price_pivot_2
        confirmed = after > trigger
        invalidated = after < invalidation
    else:
        trigger = float(between["low"].min())
        invalidation = divergence.price_pivot_2
        confirmed = after < trigger
        invalidated = after > invalidation

    first_confirm = _first_true_date(confirmed)
    first_invalid = _first_true_date(invalidated)

    if first_confirm and (not first_invalid or first_confirm <= first_invalid):
        divergence.status = STATUS_CONFIRMED
        divergence.resolved_on = first_confirm
    elif first_invalid:
        divergence.status = STATUS_INVALIDATED
        divergence.resolved_on = first_invalid
    elif len(after) >= config.expiry_sessions:
        divergence.status = STATUS_EXPIRED
        divergence.resolved_on = after.index[-1].date()
    else:
        divergence.status = STATUS_CANDIDATE


def _first_true_date(mask: pd.Series) -> date | None:
    hits = mask[mask]
    return hits.index[0].date() if len(hits) else None


def latest_divergences(
    frame: pd.DataFrame,
    as_of: date | None = None,
    within_sessions: int = 20,
    **kwargs: Any,
) -> list[Divergence]:
    """Divergences that became knowable within the last `within_sessions`."""
    found = find_divergences(frame, as_of=as_of, **kwargs)
    if not found or frame.empty:
        return []

    as_of = as_of or frame.index[-1].date()
    visible = frame.loc[: pd.Timestamp(as_of)]
    if len(visible) <= within_sessions:
        return found
    cutoff = visible.index[-within_sessions].date()
    return [d for d in found if d.confirmed_on >= cutoff]
