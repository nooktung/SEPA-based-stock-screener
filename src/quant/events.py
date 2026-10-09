"""Breakout and breakdown lifecycles (§15, §16) and shakeouts (§17).

A breakout is not an instant, it is a sequence with three separate moments:

    setup_date          price traded through the level
    confirmation_date   a close held above it
    failure_date        a later close gave it back

Keeping the three apart is what makes the backtest honest. Labelling the setup
date as "failed breakout" because it later failed is the single most common way
a pattern study fools itself: the label uses information from days that had not
happened yet, and the resulting statistics describe a strategy nobody could
have traded. `status_as_of()` exists so that any date can be asked what was
known *then*, and it never answers with anything later.

A shakeout is the mirror idea on the support side: a violation that does not
stick. It differs from the others in one respect worth noting -- it is fully
visible on the day it happens, because the break, the recovery and the wick
are all inside one session. Confirmation is still a separate, later question.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Mapping, Sequence

import pandas as pd

from src.config import load_config
from src.quant import indicators as ind
from src.quant.patterns import (
    STATUS_CANDIDATE,
    STATUS_CONFIRMED,
    STATUS_INVALIDATED,
    STATUS_NONE,
    PatternConfig,
    close_position_in_range,
    lower_wick_ratio,
)
from src.quant.store import price_series

log = logging.getLogger(__name__)

BREAKOUT = "BREAKOUT"
BREAKDOWN = "BREAKDOWN"

STATE_ATTEMPT = "ATTEMPT"
STATE_CONFIRMED = "CONFIRMED"
STATE_FAILED = "FAILED"
STATE_EXPIRED = "EXPIRED"


@dataclass(frozen=True)
class ShakeoutConfig:
    window: int
    confirmation_window: int
    min_lower_wick: float
    min_close_position: float

    @classmethod
    def from_thresholds(cls, thresholds: Mapping[str, Any] | None = None) -> "ShakeoutConfig":
        raw = dict((thresholds or load_config().thresholds["patterns"])["shakeout"])
        return cls(
            window=int(raw["window"]),
            confirmation_window=int(raw["confirmation_window"]),
            min_lower_wick=float(raw["min_lower_wick"]),
            min_close_position=float(raw["min_close_position"]),
        )


@dataclass
class BreakEvent:
    """One breakout or breakdown, from first attempt to final outcome."""

    kind: Literal["BREAKOUT", "BREAKDOWN"]
    window: int
    level: float
    setup_date: date
    confirmation_date: date | None = None
    failure_date: date | None = None
    expiry_date: date | None = None
    max_excursion_pct: float = 0.0

    @property
    def status(self) -> str:
        """Final state, using everything the event has seen."""
        if self.failure_date:
            return STATE_FAILED
        if self.confirmation_date:
            return STATE_CONFIRMED
        if self.expiry_date:
            return STATE_EXPIRED
        return STATE_ATTEMPT

    def status_as_of(self, moment: date) -> str | None:
        """What was known about this event on `moment`, and nothing more.

        Returns None before the event existed at all. This is the method the
        backtest uses: asking an event for its final status on its setup date
        would import the future into the signal.
        """
        if moment < self.setup_date:
            return None
        if self.failure_date and moment >= self.failure_date:
            return STATE_FAILED
        if self.confirmation_date and moment >= self.confirmation_date:
            return STATE_CONFIRMED
        if self.expiry_date and moment >= self.expiry_date:
            return STATE_EXPIRED
        return STATE_ATTEMPT

    def as_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "window": self.window,
            "level": round(self.level, 2),
            "setup_date": self.setup_date.isoformat(),
            "confirmation_date": self.confirmation_date.isoformat() if self.confirmation_date else None,
            "failure_date": self.failure_date.isoformat() if self.failure_date else None,
            "expiry_date": self.expiry_date.isoformat() if self.expiry_date else None,
            "status": self.status,
            "max_excursion_pct": round(self.max_excursion_pct, 2),
        }


def track_break_events(
    frame: pd.DataFrame,
    window: int,
    kind: str = BREAKOUT,
    as_of: date | None = None,
    config: PatternConfig | None = None,
) -> list[BreakEvent]:
    """Walk the history and record every break of the rolling level.

    One pass, forward in time, opening an event when price trades through the
    level and closing it when it confirms and later fails, or when it lapses
    without ever confirming. Nothing is written back to an earlier date.
    """
    settings = config or PatternConfig.from_thresholds()
    if frame.empty:
        return []

    as_of = as_of or frame.index[-1].date()
    visible = frame.loc[: pd.Timestamp(as_of)]
    if len(visible) < window + 2:
        return []

    close = price_series(visible)
    high = visible["high"].astype("float64")
    low = visible["low"].astype("float64")

    if kind == BREAKOUT:
        levels = ind.rolling_high(high, window, exclude_current=True)
    else:
        levels = ind.rolling_low(low, window, exclude_current=True)

    events: list[BreakEvent] = []
    open_event: BreakEvent | None = None
    sessions_since_setup = 0

    for position, stamp in enumerate(visible.index):
        level = levels.iloc[position]
        if pd.isna(level):
            continue

        today = stamp.date()
        bar_close = float(close.iloc[position])
        bar_high = float(high.iloc[position])
        bar_low = float(low.iloc[position])
        level = float(level)

        if open_event is not None:
            sessions_since_setup += 1
            reference = open_event.level

            if open_event.kind == BREAKOUT:
                excursion = (bar_high - reference) / reference * 100.0
                open_event.max_excursion_pct = max(open_event.max_excursion_pct, excursion)
                if open_event.confirmation_date is None:
                    if bar_close > reference * (1.0 + settings.breakout_confirm_pct):
                        open_event.confirmation_date = today
                    elif sessions_since_setup > settings.breakout_failure_window:
                        open_event.expiry_date = today
                        events.append(open_event)
                        open_event, sessions_since_setup = None, 0
                elif bar_close < reference:
                    open_event.failure_date = today
                    events.append(open_event)
                    open_event, sessions_since_setup = None, 0
            else:
                excursion = (reference - bar_low) / reference * 100.0
                open_event.max_excursion_pct = max(open_event.max_excursion_pct, excursion)
                if open_event.confirmation_date is None:
                    if bar_close < reference * (1.0 - settings.breakout_confirm_pct):
                        open_event.confirmation_date = today
                    elif sessions_since_setup > settings.breakout_failure_window:
                        open_event.expiry_date = today
                        events.append(open_event)
                        open_event, sessions_since_setup = None, 0
                elif bar_close > reference:
                    open_event.failure_date = today
                    events.append(open_event)
                    open_event, sessions_since_setup = None, 0

        if open_event is None:
            broke = bar_high > level if kind == BREAKOUT else bar_low < level
            if broke:
                open_event = BreakEvent(kind=kind, window=window, level=level, setup_date=today)
                sessions_since_setup = 0

    if open_event is not None:
        events.append(open_event)
    return events


def events_known_by(events: Sequence[BreakEvent], as_of: date) -> list[BreakEvent]:
    """Events that had already begun by `as_of`."""
    return [e for e in events if e.setup_date <= as_of]


def failed_breaks(events: Sequence[BreakEvent], as_of: date, within_sessions_of: date | None = None) -> list[BreakEvent]:
    """Events whose failure was known by `as_of` -- never before."""
    return [e for e in events if e.failure_date is not None and e.failure_date <= as_of]


# ---------------------------------------------------------------------------
# Shakeout
# ---------------------------------------------------------------------------

@dataclass
class Shakeout:
    setup_date: date
    level: float
    score: float = 0.0
    status: str = STATUS_NONE
    components: dict[str, bool] = field(default_factory=dict)
    resolved_on: date | None = None
    reasons: list[str] = field(default_factory=list)

    def status_as_of(self, moment: date) -> str | None:
        if moment < self.setup_date:
            return None
        if self.resolved_on and moment >= self.resolved_on:
            return self.status
        return STATUS_CANDIDATE

    def as_dict(self) -> dict[str, Any]:
        return {
            "setup_date": self.setup_date.isoformat(),
            "level": round(self.level, 2),
            "score": self.score,
            "status": self.status,
            "components": dict(self.components),
            "resolved_on": self.resolved_on.isoformat() if self.resolved_on else None,
        }


def find_shakeouts(
    frame: pd.DataFrame,
    as_of: date | None = None,
    config: ShakeoutConfig | None = None,
    pattern_config: PatternConfig | None = None,
    volume_ratios: pd.Series | None = None,
) -> list[Shakeout]:
    """Support violations that did not stick.

    Four conditions, all inside one session: the low broke the rolling support,
    the close recovered above it, a long lower wick was left, and the close sat
    in the upper part of the range. A long lower wick alone is not a shakeout --
    a stock can print one on the way to a much lower low -- which is why the
    recovery above the level is required rather than assumed.
    """
    settings = config or ShakeoutConfig.from_thresholds()
    patterns = pattern_config or PatternConfig.from_thresholds()

    if frame.empty:
        return []
    as_of = as_of or frame.index[-1].date()
    visible = frame.loc[: pd.Timestamp(as_of)]
    if len(visible) < settings.window + 2:
        return []

    close = price_series(visible)
    high = visible["high"].astype("float64")
    low = visible["low"].astype("float64")
    open_ = visible["open"].astype("float64")
    supports = ind.rolling_low(low, settings.window, exclude_current=True)

    found: list[Shakeout] = []
    for position, stamp in enumerate(visible.index):
        level = supports.iloc[position]
        if pd.isna(level):
            continue
        level = float(level)

        bar_low = float(low.iloc[position])
        bar_high = float(high.iloc[position])
        bar_close = float(close.iloc[position])
        bar_open = float(open_.iloc[position])

        if not (bar_low < level and bar_close > level):
            continue

        wick = lower_wick_ratio(bar_open, bar_high, bar_low, bar_close)
        position_in_range = close_position_in_range(bar_high, bar_low, bar_close)
        volume_ratio = None
        if volume_ratios is not None and stamp in volume_ratios.index:
            value = volume_ratios.loc[stamp]
            volume_ratio = None if pd.isna(value) else float(value)

        components = {
            "broke_support": True,
            "closed_above": True,
            "long_lower_wick": wick is not None and wick >= settings.min_lower_wick,
            "strong_close": position_in_range is not None and position_in_range >= settings.min_close_position,
            "volume_expansion": volume_ratio is not None and volume_ratio >= patterns.volume_expansion_ratio,
        }

        # The wick and the close are what separate a shakeout from an ordinary
        # dip. Without either, this was just a session that traded lower.
        if not (components["long_lower_wick"] or components["strong_close"]):
            continue

        shakeout = Shakeout(
            setup_date=stamp.date(),
            level=level,
            components=components,
            score=round(
                25.0 * components["broke_support"]
                + 25.0 * components["closed_above"]
                + 20.0 * components["long_lower_wick"]
                + 15.0 * components["strong_close"]
                + 15.0 * components["volume_expansion"],
                1,
            ),
            status=STATUS_CANDIDATE,
        )
        shakeout.reasons.append(
            f"low {bar_low:,.0f} broke the {settings.window}-session support "
            f"{level:,.0f}, close {bar_close:,.0f} recovered above it"
        )
        _resolve_shakeout(shakeout, visible, close, position, settings)
        found.append(shakeout)

    return found


def _resolve_shakeout(
    shakeout: Shakeout,
    frame: pd.DataFrame,
    close: pd.Series,
    position: int,
    config: ShakeoutConfig,
) -> None:
    """Settle the shakeout from later sessions only."""
    after = close.iloc[position + 1 : position + 1 + config.confirmation_window]
    if after.empty:
        shakeout.status = STATUS_CANDIDATE
        return

    candidate_high = float(frame["high"].iloc[position])
    candidate_low = float(frame["low"].iloc[position])

    for stamp, value in after.items():
        if float(value) > candidate_high:
            shakeout.status = STATUS_CONFIRMED
            shakeout.resolved_on = stamp.date()
            return
        if float(value) < candidate_low:
            shakeout.status = STATUS_INVALIDATED
            shakeout.resolved_on = stamp.date()
            return
    shakeout.status = STATUS_CANDIDATE
