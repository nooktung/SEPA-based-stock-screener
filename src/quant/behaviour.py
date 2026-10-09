"""Accumulation-like and distribution-like behaviour (§19).

The names are deliberate and the distinction is not pedantic. Price and volume
cannot show who is buying. A stock absorbing supply on heavy volume looks
identical whether the buyer is a fund building a position, an index tracker
rebalancing, or a thousand retail accounts. Claiming "institutions are
accumulating" asserts something the data does not contain.

So this module reports *behaviour that resembles* accumulation or
distribution, built from six observable components each side:

    up/down volume        was volume clearly heavier on up sessions or down
    closing position      did sessions close in the upper or lower half
    range behaviour       is volatility compressing or expanding
    level behaviour       did price hold support, or get rejected at resistance
    relative strength     is it outperforming or lagging the index
    failed breaks         did breakdowns fail (bullish) or breakouts fail (bearish)

Each is a fact about the window. The score is their weighted sum, and the
contributions are reported alongside it, so a reader can see which components
carried it rather than trusting a number.

The two scores are independent by design. A quiet, directionless stock scores
low on both, which is the correct answer -- forcing them to sum to 100 would
manufacture a signal out of an absence of one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Mapping

import numpy as np
import pandas as pd

from src.config import load_config
from src.quant import indicators as ind
from src.quant.levels import SIDE_RESISTANCE, SIDE_SUPPORT, Zone, nearest_zone
from src.quant.patterns import close_position_in_range
from src.quant.store import price_series

log = logging.getLogger(__name__)

ACCUMULATION_LIKE = "ACCUMULATION_LIKE"
DISTRIBUTION_LIKE = "DISTRIBUTION_LIKE"
NEUTRAL = "NEUTRAL"


@dataclass(frozen=True)
class BehaviourConfig:
    window: int
    accumulation_weights: dict[str, float]
    distribution_weights: dict[str, float]
    range_contraction_ratio: float
    range_expansion_ratio: float
    up_down_volume_bullish: float
    up_down_volume_bearish: float
    close_position_bullish: float
    close_position_bearish: float

    @classmethod
    def from_thresholds(cls, thresholds: Mapping[str, Any] | None = None) -> "BehaviourConfig":
        raw = dict((thresholds or load_config().thresholds["patterns"])["behaviour"])
        return cls(
            window=int(raw["window"]),
            accumulation_weights=dict(raw["accumulation_weights"]),
            distribution_weights=dict(raw["distribution_weights"]),
            range_contraction_ratio=float(raw["range_contraction_ratio"]),
            range_expansion_ratio=float(raw["range_expansion_ratio"]),
            up_down_volume_bullish=float(raw["up_down_volume_bullish"]),
            up_down_volume_bearish=float(raw["up_down_volume_bearish"]),
            close_position_bullish=float(raw["close_position_bullish"]),
            close_position_bearish=float(raw["close_position_bearish"]),
        )


@dataclass
class BehaviourRead:
    accumulation_like_score: float = 0.0
    distribution_like_score: float = 0.0
    verdict: str = NEUTRAL
    accumulation_components: dict[str, bool] = field(default_factory=dict)
    distribution_components: dict[str, bool] = field(default_factory=dict)
    evidence: dict[str, float | None] = field(default_factory=dict)
    sessions: int = 0

    def contributions(self, side: str, config: BehaviourConfig) -> dict[str, float]:
        """Which components actually produced the score."""
        weights = config.accumulation_weights if side == ACCUMULATION_LIKE else config.distribution_weights
        components = self.accumulation_components if side == ACCUMULATION_LIKE else self.distribution_components
        return {name: weights.get(name, 0.0) for name, present in components.items() if present}

    def as_dict(self) -> dict[str, Any]:
        return {
            "accumulation_like_score": self.accumulation_like_score,
            "distribution_like_score": self.distribution_like_score,
            "verdict": self.verdict,
            "accumulation_components": dict(self.accumulation_components),
            "distribution_components": dict(self.distribution_components),
            "evidence": {k: (None if v is None else round(v, 4)) for k, v in self.evidence.items()},
            "sessions": self.sessions,
        }


def up_down_volume_ratio(frame: pd.DataFrame) -> float | None:
    """Volume on advancing sessions divided by volume on declining ones.

    Above 1 means the heavier trade happened while price rose. It is the
    oldest measurement of who was more urgent, and it says nothing about who
    they were.
    """
    close = price_series(frame)
    volume = frame["volume"].astype("float64")
    change = close.diff()

    up = float(volume[change > 0].sum())
    down = float(volume[change < 0].sum())
    if down <= 0:
        return None if up <= 0 else float("inf")
    return up / down


def average_close_position(frame: pd.DataFrame) -> float | None:
    """Mean position of the close within each session's range, 0 to 1."""
    high = frame["high"].astype("float64")
    low = frame["low"].astype("float64")
    close = price_series(frame)
    span = high - low
    positions = ((close - low) / span).where(span > 0)
    values = positions.dropna()
    return float(values.mean()) if len(values) else None


def range_ratio(
    frame: pd.DataFrame,
    window: int,
    atr_period: int = 14,
    atr: pd.Series | None = None,
) -> float | None:
    """Recent average true range against the range of the window before it.

    Below 1 is compression -- the stock going quiet, which is what a base looks
    like. Above 1 is expansion.
    """
    if len(frame) < window * 2:
        return None
    # A caller walking the history can hand in the ATR computed once, instead
    # of rebuilding Wilder's recursion from the first bar on every session.
    if atr is None:
        atr = ind.atr(
            frame["high"].astype("float64"),
            frame["low"].astype("float64"),
            price_series(frame),
            atr_period,
        )
    atr = atr.loc[: frame.index[-1]].dropna()
    if len(atr) < window * 2:
        return None
    recent = float(atr.iloc[-window:].mean())
    earlier = float(atr.iloc[-window * 2 : -window].mean())
    if earlier <= 0:
        return None
    return recent / earlier


def compute_behaviour(
    frame: pd.DataFrame,
    zones: list[Zone] | None = None,
    relative_strength: float | None = None,
    failed_breakdown: bool = False,
    failed_breakout: bool = False,
    config: BehaviourConfig | None = None,
    atr: pd.Series | None = None,
) -> BehaviourRead:
    """Score accumulation-like and distribution-like behaviour over the window.

    Pure: it reads no database and no clock. `frame` must already end at the
    session being evaluated.
    """
    settings = config or BehaviourConfig.from_thresholds()
    read = BehaviourRead()

    if frame.empty:
        return read

    window = frame.iloc[-settings.window :]
    read.sessions = len(window)
    if read.sessions < 2:
        return read

    volume_ratio = up_down_volume_ratio(window)
    close_position = average_close_position(window)
    ranges = range_ratio(frame, settings.window, atr=atr)

    close = price_series(window)
    support = nearest_zone(zones or [], SIDE_SUPPORT)
    resistance = nearest_zone(zones or [], SIDE_RESISTANCE)

    # Did price test support and hold, or test resistance and get turned away?
    holds_support = False
    rejects_resistance = False
    if support is not None:
        touched = (window["low"].astype("float64") <= support.zone_high).any()
        closed_below = (close < support.zone_low).any()
        holds_support = bool(touched and not closed_below)
    if resistance is not None:
        touched = (window["high"].astype("float64") >= resistance.zone_low).any()
        closed_above = (close > resistance.zone_high).any()
        rejects_resistance = bool(touched and not closed_above)

    read.evidence = {
        "up_down_volume_ratio": None if volume_ratio in (None, float("inf")) else volume_ratio,
        "avg_close_position": close_position,
        "range_ratio": ranges,
        "relative_strength": relative_strength,
    }

    read.accumulation_components = {
        "up_down_volume": volume_ratio is not None and volume_ratio >= settings.up_down_volume_bullish,
        "closes_in_upper_half": close_position is not None and close_position >= settings.close_position_bullish,
        "range_contraction": ranges is not None and ranges <= settings.range_contraction_ratio,
        "holds_support": holds_support,
        "relative_strength": relative_strength is not None and relative_strength > 0,
        "failed_breakdown": failed_breakdown,
    }
    read.distribution_components = {
        "up_down_volume": volume_ratio is not None and volume_ratio <= settings.up_down_volume_bearish,
        "closes_in_lower_half": close_position is not None and close_position <= settings.close_position_bearish,
        "range_expansion": ranges is not None and ranges >= settings.range_expansion_ratio,
        "rejects_resistance": rejects_resistance,
        "relative_weakness": relative_strength is not None and relative_strength < 0,
        "failed_breakout": failed_breakout,
    }

    read.accumulation_like_score = round(
        sum(
            settings.accumulation_weights.get(name, 0.0)
            for name, present in read.accumulation_components.items()
            if present
        ),
        1,
    )
    read.distribution_like_score = round(
        sum(
            settings.distribution_weights.get(name, 0.0)
            for name, present in read.distribution_components.items()
            if present
        ),
        1,
    )

    # A stock can look like neither. Forcing a verdict out of an absence of
    # evidence is exactly what this module is built not to do.
    spread = read.accumulation_like_score - read.distribution_like_score
    if spread >= 20:
        read.verdict = ACCUMULATION_LIKE
    elif spread <= -20:
        read.verdict = DISTRIBUTION_LIKE
    else:
        read.verdict = NEUTRAL

    return read
