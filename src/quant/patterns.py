"""Price structure and pattern detection.

Everything an indicator cannot say: where the swings are, whether the trend is
making higher highs, whether a break of resistance held, and whether a break
that failed did so in the specific way that traps buyers.

Three rules govern this module, and they are the reason it exists as separate
code rather than as a paragraph of interpretation.

**Nothing here uses information from the future.** A swing pivot needs bars on
both sides to be a pivot, which means it cannot be known on the day it prints
-- it is *confirmed* `swing_strength` sessions later, and that is the timestamp
it carries. A trap is a candidate on the day of the failed break and only
becomes confirmed when a later session follows through. Reading a pattern with
hindsight makes every one of them look reliable.

**A pattern is a set of observable facts, not a verdict.** A trap score is the
sum of things that either happened or did not: the high exceeded the level, the
close did not hold it, the close sat low in its own range, the wick was long,
the volume was heavy. Each is checkable against the bars. Whether that means
sell is not decided here.

**Every threshold comes from config.** What counts as a weak close, how many
sessions a breakout must hold, how much each component contributes -- all in
`thresholds.yaml`, so a claim can be re-tested at other settings rather than
being true only at the one that was chosen.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Mapping, Sequence

import numpy as np
import pandas as pd

from src.config import load_config
from src.quant import indicators as ind
from src.quant.store import price_series

log = logging.getLogger(__name__)

# Structure
HIGHER_HIGH, HIGHER_LOW, LOWER_HIGH, LOWER_LOW = "HH", "HL", "LH", "LL"
BULLISH_STRUCTURE, NEUTRAL_STRUCTURE, BEARISH_STRUCTURE = "BULLISH", "NEUTRAL", "BEARISH"

# Breakout states
BREAKOUT_NONE = "NONE"
BREAKOUT_ATTEMPT = "ATTEMPT"        # traded above the level, did not close above
BREAKOUT_CONFIRMED = "CONFIRMED"    # closed above the level by the confirmation margin
BREAKOUT_FAILED = "FAILED"          # had confirmed, then closed back below

# Pattern states
STATUS_NONE = "NONE"
STATUS_CANDIDATE = "CANDIDATE"
STATUS_CONFIRMED = "CONFIRMED"
STATUS_INVALIDATED = "INVALIDATED"

PatternStatus = Literal["NONE", "CANDIDATE", "CONFIRMED", "INVALIDATED"]


@dataclass(frozen=True)
class PatternConfig:
    swing_strength: int
    breakout_windows: tuple[int, ...]
    breakout_failure_window: int
    weak_close_position: float
    strong_close_position: float
    long_wick_ratio: float
    bull_trap_weights: dict[str, float]
    bear_trap_weights: dict[str, float]
    trap_confirmation_window: int
    breakout_confirm_pct: float
    volume_expansion_ratio: float

    @classmethod
    def from_thresholds(cls, thresholds: Mapping[str, Any] | None = None) -> "PatternConfig":
        all_thresholds = thresholds or load_config().thresholds.raw
        raw = dict(all_thresholds["patterns"])
        technical = dict(all_thresholds["technical"])
        return cls(
            swing_strength=int(raw["swing_strength"]),
            breakout_windows=tuple(raw["breakout_windows"]),
            breakout_failure_window=int(raw["breakout_failure_window"]),
            weak_close_position=float(raw["weak_close_position"]),
            strong_close_position=float(raw["strong_close_position"]),
            long_wick_ratio=float(raw["long_wick_ratio"]),
            bull_trap_weights=dict(raw["bull_trap_weights"]),
            bear_trap_weights=dict(raw["bear_trap_weights"]),
            trap_confirmation_window=int(raw["trap_confirmation_window"]),
            # Shared with the technical engine so a breakout means the same
            # thing in both places.
            breakout_confirm_pct=float(technical["breakout_confirm_close_pct"]),
            volume_expansion_ratio=float(technical["volume_expansion_ratio"]),
        )


# ---------------------------------------------------------------------------
# Candle geometry
# ---------------------------------------------------------------------------

def close_position_in_range(high: float | None, low: float | None, close: float | None) -> float | None:
    """Where the close sits between the day's low (0) and high (1).

    The single most informative thing about a session that is not its return.
    A session that travelled up and closed at its low was rejected; the same
    return with a close at the high was accepted.
    """
    if high is None or low is None or close is None:
        return None
    span = high - low
    if span <= 0:
        return None
    return float((close - low) / span)


def upper_wick_ratio(open_: float | None, high: float | None, low: float | None, close: float | None) -> float | None:
    """Share of the day's range spent above the candle body."""
    if None in (open_, high, low, close):
        return None
    span = high - low
    if span <= 0:
        return None
    return float((high - max(open_, close)) / span)


def lower_wick_ratio(open_: float | None, high: float | None, low: float | None, close: float | None) -> float | None:
    if None in (open_, high, low, close):
        return None
    span = high - low
    if span <= 0:
        return None
    return float((min(open_, close) - low) / span)


# ---------------------------------------------------------------------------
# Swing pivots and market structure
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Pivot:
    kind: Literal["HIGH", "LOW"]
    trade_date: date
    price: float
    confirmed_on: date

    @property
    def confirmation_lag_sessions(self) -> int:
        return 0  # filled by the finder; kept for clarity of intent


def find_pivots(frame: pd.DataFrame, strength: int) -> list[Pivot]:
    """Swing highs and lows, each carrying the date it could first be known.

    A pivot needs `strength` bars on both sides. The right-hand bars are the
    catch: on the day a swing high prints, nobody knows it is one. It becomes
    knowable `strength` sessions later, and `confirmed_on` records exactly
    that. Anything downstream that treats `trade_date` as the moment of
    knowledge has reintroduced look-ahead.
    """
    if frame.empty or strength < 1 or len(frame) < strength * 2 + 1:
        return []

    highs = frame["high"].astype("float64").to_numpy()
    lows = frame["low"].astype("float64").to_numpy()
    dates = [stamp.date() for stamp in frame.index]

    pivots: list[Pivot] = []
    for i in range(strength, len(frame) - strength):
        window = slice(i - strength, i + strength + 1)
        confirmed_on = dates[i + strength]
        if highs[i] == np.nanmax(highs[window]) and highs[i] > np.nanmax(
            np.delete(highs[window], strength)
        ):
            pivots.append(Pivot("HIGH", dates[i], float(highs[i]), confirmed_on))
        elif lows[i] == np.nanmin(lows[window]) and lows[i] < np.nanmin(
            np.delete(lows[window], strength)
        ):
            pivots.append(Pivot("LOW", dates[i], float(lows[i]), confirmed_on))
    return pivots


def pivots_known_by(pivots: Sequence[Pivot], as_of: date) -> list[Pivot]:
    """Only the pivots that had been confirmed on or before `as_of`."""
    return [p for p in pivots if p.confirmed_on <= as_of]


@dataclass
class StructureRead:
    structure: str = NEUTRAL_STRUCTURE
    last_swing_high: float | None = None
    last_swing_low: float | None = None
    swing_sequence: list[str] = field(default_factory=list)
    break_of_structure: bool = False
    change_of_character: bool = False

    @property
    def is_bullish(self) -> bool:
        return self.structure == BULLISH_STRUCTURE


def read_structure(frame: pd.DataFrame, config: PatternConfig, as_of: date | None = None) -> StructureRead:
    """Higher highs and higher lows, or the opposite, from confirmed pivots.

    `break_of_structure` is the close taking out the most recent confirmed
    swing high (or low). `change_of_character` is that break happening against
    the prevailing structure -- the first sign that the pattern of swings is
    changing, rather than continuing.
    """
    read = StructureRead()
    if frame.empty:
        return read

    as_of = as_of or frame.index[-1].date()
    pivots = pivots_known_by(find_pivots(frame, config.swing_strength), as_of)
    if not pivots:
        return read

    highs = [p for p in pivots if p.kind == "HIGH"]
    lows = [p for p in pivots if p.kind == "LOW"]
    read.last_swing_high = highs[-1].price if highs else None
    read.last_swing_low = lows[-1].price if lows else None

    sequence: list[str] = []
    if len(highs) >= 2:
        sequence.append(HIGHER_HIGH if highs[-1].price > highs[-2].price else LOWER_HIGH)
    if len(lows) >= 2:
        sequence.append(HIGHER_LOW if lows[-1].price > lows[-2].price else LOWER_LOW)
    read.swing_sequence = sequence

    if HIGHER_HIGH in sequence and HIGHER_LOW in sequence:
        read.structure = BULLISH_STRUCTURE
    elif LOWER_HIGH in sequence and LOWER_LOW in sequence:
        read.structure = BEARISH_STRUCTURE

    close = float(price_series(frame).loc[:pd.Timestamp(as_of)].iloc[-1])
    if read.last_swing_high is not None and close > read.last_swing_high:
        read.break_of_structure = True
        read.change_of_character = read.structure == BEARISH_STRUCTURE
    elif read.last_swing_low is not None and close < read.last_swing_low:
        read.break_of_structure = True
        read.change_of_character = read.structure == BULLISH_STRUCTURE

    return read


# ---------------------------------------------------------------------------
# Breakouts
# ---------------------------------------------------------------------------

@dataclass
class BreakoutRead:
    window: int
    level: float | None = None
    state: str = BREAKOUT_NONE
    distance_pct: float | None = None
    sessions_since_confirmed: int | None = None

    @property
    def attempted(self) -> bool:
        return self.state in (BREAKOUT_ATTEMPT, BREAKOUT_CONFIRMED, BREAKOUT_FAILED)


def read_breakout(frame: pd.DataFrame, window: int, config: PatternConfig) -> BreakoutRead:
    """Classify the latest session against the prior `window`-session high.

    The level deliberately excludes the current bar: measuring a breakout
    against a range that already contains today makes every new high a
    breakout by construction.

    ATTEMPT means price traded above the level but did not close there.
    CONFIRMED means it closed above by the configured margin. FAILED means a
    confirmation happened within the failure window and price has since closed
    back below -- which is what separates a breakout that did not work from one
    that never happened.
    """
    read = BreakoutRead(window=window)
    if len(frame) < window + 2:
        return read

    close = price_series(frame)
    high = frame["high"].astype("float64")
    # The level is the prior window's highest traded price. Built from closes
    # instead, it would sit below the real resistance and every ordinary
    # session that poked above a previous close would register as an attempt.
    levels = ind.rolling_high(high, window, exclude_current=True)

    level = levels.iloc[-1]
    if pd.isna(level):
        return read

    read.level = float(level)
    confirm_at = read.level * (1.0 + config.breakout_confirm_pct)
    latest_close = float(close.iloc[-1])
    latest_high = float(high.iloc[-1])
    read.distance_pct = (latest_close - read.level) / read.level * 100.0

    if latest_close > confirm_at:
        read.state = BREAKOUT_CONFIRMED
        read.sessions_since_confirmed = 0
        return read

    # Did a confirmation happen recently, and has price since given it back?
    lookback = min(config.breakout_failure_window, len(frame) - 1)
    for back in range(1, lookback + 1):
        past_level = levels.iloc[-1 - back]
        if pd.isna(past_level):
            continue
        if float(close.iloc[-1 - back]) > float(past_level) * (1.0 + config.breakout_confirm_pct):
            if latest_close < float(past_level):
                read.state = BREAKOUT_FAILED
                read.sessions_since_confirmed = back
                return read
            break

    if latest_high > read.level:
        read.state = BREAKOUT_ATTEMPT
    return read


# ---------------------------------------------------------------------------
# Traps
# ---------------------------------------------------------------------------

@dataclass
class TrapRead:
    kind: Literal["BULL", "BEAR"]
    score: float = 0.0
    status: PatternStatus = STATUS_NONE
    level: float | None = None
    window: int | None = None
    components: dict[str, bool] = field(default_factory=dict)
    reasons: list[str] = field(default_factory=list)

    @property
    def is_candidate(self) -> bool:
        return self.status in (STATUS_CANDIDATE, STATUS_CONFIRMED)


def bull_trap(
    frame: pd.DataFrame,
    config: PatternConfig,
    window: int | None = None,
    volume_ratio: float | None = None,
    level: float | None = None,
) -> TrapRead:
    """Score how much the latest session looks like a failed upside break.

    The components are facts about one candle measured against a level that
    existed before it: the high exceeded the level, the close did not hold it,
    the close sat low in the day's own range, a long upper wick was left, and
    volume expanded. None of them is an opinion, and the score is their sum.

    Status is deliberately conservative. On the day itself the most that can be
    said is CANDIDATE: whether buyers were actually trapped depends on what
    happens next, and claiming otherwise on the day would be look-ahead
    wearing a confident tone.
    """
    read = TrapRead(kind="BULL")
    window = window or max(config.breakout_windows[:1] or (20,))
    read.window = window

    if len(frame) < window + 2:
        return read

    close = price_series(frame)
    # `level` may be supplied by a caller that already computed the rolling
    # series once for the whole history -- the same number, without rebuilding
    # it on every session.
    if level is None:
        levels = ind.rolling_high(frame["high"].astype("float64"), window, exclude_current=True)
        level = levels.iloc[-1]
    if level is None or pd.isna(level):
        return read
    read.level = float(level)

    last = frame.iloc[-1]
    high, low, open_ = float(last["high"]), float(last["low"]), float(last["open"])
    latest_close = float(close.iloc[-1])

    position = close_position_in_range(high, low, latest_close)
    wick = upper_wick_ratio(open_, high, low, latest_close)

    components = {
        "broke_intraday": high > read.level,
        "closed_below": latest_close <= read.level,
        "weak_close": position is not None and position <= config.weak_close_position,
        "upper_wick": wick is not None and wick >= config.long_wick_ratio,
        "volume_expansion": volume_ratio is not None and volume_ratio >= config.volume_expansion_ratio,
    }
    read.components = components

    # Without a break attempt there is nothing to trap anyone with.
    if not (components["broke_intraday"] and components["closed_below"]):
        return read

    read.score = round(
        sum(config.bull_trap_weights.get(name, 0.0) for name, present in components.items() if present),
        1,
    )
    read.status = STATUS_CANDIDATE
    read.reasons.append(
        f"high {high:,.0f} cleared the {window}-session level {read.level:,.0f} "
        f"but the close {latest_close:,.0f} did not hold it"
    )
    if components["weak_close"]:
        read.reasons.append(f"closed at {position:.0%} of the session range")
    if components["upper_wick"]:
        read.reasons.append(f"upper wick was {wick:.0%} of the range")
    if components["volume_expansion"]:
        read.reasons.append(f"volume {volume_ratio:.1f}x its 20-session average")
    return read


def bear_trap(
    frame: pd.DataFrame,
    config: PatternConfig,
    window: int | None = None,
    volume_ratio: float | None = None,
    level: float | None = None,
) -> TrapRead:
    """Mirror of bull_trap: a failed downside break that catches sellers."""
    read = TrapRead(kind="BEAR")
    window = window or max(config.breakout_windows[:1] or (20,))
    read.window = window

    if len(frame) < window + 2:
        return read

    close = price_series(frame)
    if level is None:
        levels = ind.rolling_low(frame["low"].astype("float64"), window, exclude_current=True)
        level = levels.iloc[-1]
    if level is None or pd.isna(level):
        return read
    read.level = float(level)

    last = frame.iloc[-1]
    high, low, open_ = float(last["high"]), float(last["low"]), float(last["open"])
    latest_close = float(close.iloc[-1])

    position = close_position_in_range(high, low, latest_close)
    wick = lower_wick_ratio(open_, high, low, latest_close)

    components = {
        "broke_intraday": low < read.level,
        "closed_above": latest_close >= read.level,
        "strong_close": position is not None and position >= config.strong_close_position,
        "lower_wick": wick is not None and wick >= config.long_wick_ratio,
        "volume_expansion": volume_ratio is not None and volume_ratio >= config.volume_expansion_ratio,
    }
    read.components = components

    if not (components["broke_intraday"] and components["closed_above"]):
        return read

    read.score = round(
        sum(config.bear_trap_weights.get(name, 0.0) for name, present in components.items() if present),
        1,
    )
    read.status = STATUS_CANDIDATE
    read.reasons.append(
        f"low {low:,.0f} broke the {window}-session level {read.level:,.0f} "
        f"but the close {latest_close:,.0f} recovered above it"
    )
    if components["strong_close"]:
        read.reasons.append(f"closed at {position:.0%} of the session range")
    if components["lower_wick"]:
        read.reasons.append(f"lower wick was {wick:.0%} of the range")
    return read


def resolve_trap(frame: pd.DataFrame, candidate_index: int, trap: TrapRead, config: PatternConfig) -> PatternStatus:
    """Settle a trap candidate using the sessions that came after it.

    Confirmation for a bull trap is a close below the candidate session's low;
    invalidation is a close back above the level it failed at. Called with a
    frame that ends at the evaluation date, so the answer at any point in
    history is the answer that was available then.
    """
    if trap.status == STATUS_NONE or trap.level is None:
        return trap.status

    after = frame.iloc[candidate_index + 1 : candidate_index + 1 + config.trap_confirmation_window]
    if after.empty:
        return STATUS_CANDIDATE

    candidate = frame.iloc[candidate_index]
    closes = price_series(after)

    if trap.kind == "BULL":
        if (closes > trap.level).any():
            return STATUS_INVALIDATED
        if (closes < float(candidate["low"])).any():
            return STATUS_CONFIRMED
    else:
        if (closes < trap.level).any():
            return STATUS_INVALIDATED
        if (closes > float(candidate["high"])).any():
            return STATUS_CONFIRMED
    return STATUS_CANDIDATE
