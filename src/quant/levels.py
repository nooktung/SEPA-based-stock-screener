"""Support and resistance zones (§11).

Levels are zones, not lines. A stock does not turn at 34,600 -- it turns
somewhere around 34,600, and quoting a single number implies a precision the
data cannot support. Every level here is a band, and nearby bands are merged
rather than reported as separate levels that are really one.

Sources are labelled because they mean different things. A swing high is a
place where price actually reversed. A breakout level is a place price fought
through, which often becomes support afterwards. A zone tested five times over
a year is different evidence from a zone touched once last week, so strength
combines how often it was tested with how recently.

The look-ahead rule is the same as everywhere else: a swing pivot cannot be
used as a level before its `confirmed_on` date, and touches are only counted
from sessions up to `as_of`. A zone built from tomorrow's pivot would make
every historical test of it look prescient.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from datetime import date
from typing import Any, Literal, Mapping, Sequence

import pandas as pd

from src.config import load_config
from src.quant import indicators as ind
from src.quant.patterns import PatternConfig, find_pivots, pivots_known_by
from src.quant.store import price_series

log = logging.getLogger(__name__)

SOURCE_SWING_HIGH = "SWING_HIGH"
SOURCE_SWING_LOW = "SWING_LOW"
SOURCE_BREAKOUT_LEVEL = "BREAKOUT_LEVEL"
SOURCE_BREAKDOWN_LEVEL = "BREAKDOWN_LEVEL"
SOURCE_REPEATED_REACTION = "REPEATED_REACTION"

SIDE_SUPPORT = "SUPPORT"
SIDE_RESISTANCE = "RESISTANCE"


@dataclass(frozen=True)
class ZoneConfig:
    merge_within_pct: float
    zone_half_width_pct: float
    touch_tolerance_pct: float
    max_zones_per_side: int
    recency_half_life_sessions: int

    @classmethod
    def from_thresholds(cls, thresholds: Mapping[str, Any] | None = None) -> "ZoneConfig":
        raw = dict((thresholds or load_config().thresholds["patterns"])["zones"])
        return cls(
            merge_within_pct=float(raw["merge_within_pct"]),
            zone_half_width_pct=float(raw["zone_half_width_pct"]),
            touch_tolerance_pct=float(raw["touch_tolerance_pct"]),
            max_zones_per_side=int(raw["max_zones_per_side"]),
            recency_half_life_sessions=int(raw["recency_half_life_sessions"]),
        )


@dataclass
class Zone:
    zone_low: float
    zone_high: float
    center: float
    source: str
    sources: list[str] = field(default_factory=list)
    touch_count: int = 0
    last_tested: date | None = None
    strength: float = 0.0
    distance_from_price: float | None = None
    side: str | None = None

    def contains(self, price: float) -> bool:
        return self.zone_low <= price <= self.zone_high

    def as_dict(self) -> dict[str, Any]:
        return {
            "zone_low": round(self.zone_low, 2),
            "zone_high": round(self.zone_high, 2),
            "center": round(self.center, 2),
            "strength": self.strength,
            "touch_count": self.touch_count,
            "last_tested": self.last_tested.isoformat() if self.last_tested else None,
            "distance_from_price": None if self.distance_from_price is None else round(self.distance_from_price, 2),
            "source": self.source,
            "sources": sorted(set(self.sources)),
            "side": self.side,
        }


@dataclass
class LevelCandidate:
    price: float
    source: str
    known_on: date


def collect_levels(
    frame: pd.DataFrame,
    as_of: date,
    config: ZoneConfig,
    pattern_config: PatternConfig,
    breakout_windows: Sequence[int] = (20, 60),
) -> list[LevelCandidate]:
    """Raw candidate levels, each carrying the date it became usable."""
    visible = frame.loc[: pd.Timestamp(as_of)]
    if visible.empty:
        return []

    candidates: list[LevelCandidate] = []

    for pivot in pivots_known_by(find_pivots(visible, pattern_config.swing_strength), as_of):
        candidates.append(
            LevelCandidate(
                price=pivot.price,
                source=SOURCE_SWING_HIGH if pivot.kind == "HIGH" else SOURCE_SWING_LOW,
                known_on=pivot.confirmed_on,
            )
        )

    # A level price fought through is often the level it later leans on. These
    # are knowable the day the break happened -- no pivot confirmation needed.
    close = price_series(visible)
    high = visible["high"].astype("float64")
    low = visible["low"].astype("float64")

    for window in breakout_windows:
        if len(visible) < window + 2:
            continue
        resistance = ind.rolling_high(high, window, exclude_current=True)
        support = ind.rolling_low(low, window, exclude_current=True)

        broke_up = close > resistance
        broke_down = close < support
        for stamp in visible.index[broke_up.fillna(False)]:
            candidates.append(
                LevelCandidate(float(resistance.loc[stamp]), SOURCE_BREAKOUT_LEVEL, stamp.date())
            )
        for stamp in visible.index[broke_down.fillna(False)]:
            candidates.append(
                LevelCandidate(float(support.loc[stamp]), SOURCE_BREAKDOWN_LEVEL, stamp.date())
            )

    return [c for c in candidates if c.known_on <= as_of and c.price > 0]


def merge_levels(candidates: Sequence[LevelCandidate], config: ZoneConfig) -> list[Zone]:
    """Cluster nearby levels into zones.

    Two levels 0.4% apart are one level observed twice, not two levels. Merging
    them keeps the count of distinct zones honest and stops a single reaction
    area from being reported five times because five pivots landed in it.
    """
    if not candidates:
        return []

    ordered = sorted(candidates, key=lambda c: c.price)
    clusters: list[list[LevelCandidate]] = [[ordered[0]]]

    for candidate in ordered[1:]:
        current = clusters[-1]
        center = sum(c.price for c in current) / len(current)
        if abs(candidate.price - center) / center * 100.0 <= config.merge_within_pct:
            current.append(candidate)
        else:
            clusters.append([candidate])

    zones: list[Zone] = []
    for cluster in clusters:
        center = sum(c.price for c in cluster) / len(cluster)
        half = center * config.zone_half_width_pct / 100.0
        sources = [c.source for c in cluster]
        # The label is whichever kind of evidence appears most often, but a
        # zone built from several kinds says so through `sources`.
        dominant = max(set(sources), key=sources.count)
        zones.append(
            Zone(
                zone_low=center - half,
                zone_high=center + half,
                center=center,
                source=SOURCE_REPEATED_REACTION if len(set(sources)) > 1 else dominant,
                sources=sources,
            )
        )
    return zones


def score_zones(
    zones: Sequence[Zone],
    frame: pd.DataFrame,
    as_of: date,
    config: ZoneConfig,
) -> list[Zone]:
    """Count how often each zone was tested, and how recently.

    A test is a session whose range reached into the zone. Strength weighs the
    number of tests, decayed by age: a level defended four times last quarter
    is live evidence, the same level from two years ago is history.
    """
    visible = frame.loc[: pd.Timestamp(as_of)]
    if visible.empty:
        return list(zones)

    high = visible["high"].astype("float64")
    low = visible["low"].astype("float64")
    total_sessions = len(visible)

    for zone in zones:
        tolerance = zone.center * config.touch_tolerance_pct / 100.0
        upper = zone.zone_high + tolerance
        lower = zone.zone_low - tolerance
        touched = (low <= upper) & (high >= lower)

        zone.touch_count = int(touched.sum())
        if zone.touch_count:
            zone.last_tested = visible.index[touched][-1].date()
            age = total_sessions - 1 - visible.index.get_loc(visible.index[touched][-1])
            recency = math.pow(0.5, age / max(config.recency_half_life_sessions, 1))
        else:
            recency = 0.0

        # Touches saturate: the difference between one and four tests matters,
        # between forty and fifty does not.
        depth = min(zone.touch_count / 8.0, 1.0)
        variety = min(len(set(zone.sources)) / 3.0, 1.0)
        zone.strength = round(min(100.0, (depth * 60.0 + recency * 25.0 + variety * 15.0)), 1)

    return list(zones)


def build_zones(
    frame: pd.DataFrame,
    as_of: date | None = None,
    config: ZoneConfig | None = None,
    pattern_config: PatternConfig | None = None,
) -> list[Zone]:
    """Support and resistance zones as they stood on `as_of`, strongest first."""
    settings = config or ZoneConfig.from_thresholds()
    patterns = pattern_config or PatternConfig.from_thresholds()

    if frame.empty:
        return []
    as_of = as_of or frame.index[-1].date()
    visible = frame.loc[: pd.Timestamp(as_of)]
    if visible.empty:
        return []

    candidates = collect_levels(visible, as_of, settings, patterns)
    zones = score_zones(merge_levels(candidates, settings), visible, as_of, settings)

    price = float(price_series(visible).iloc[-1])
    for zone in zones:
        zone.distance_from_price = (zone.center - price) / price * 100.0
        zone.side = SIDE_RESISTANCE if zone.center > price else SIDE_SUPPORT

    supports = sorted(
        (z for z in zones if z.side == SIDE_SUPPORT),
        key=lambda z: (-z.strength, abs(z.distance_from_price or 0)),
    )[: settings.max_zones_per_side]
    resistances = sorted(
        (z for z in zones if z.side == SIDE_RESISTANCE),
        key=lambda z: (-z.strength, abs(z.distance_from_price or 0)),
    )[: settings.max_zones_per_side]

    return sorted(supports + resistances, key=lambda z: z.center)


def nearest_zone(zones: Sequence[Zone], side: str) -> Zone | None:
    """The closest zone on one side of price, or None if there is none."""
    candidates = [z for z in zones if z.side == side and z.distance_from_price is not None]
    if not candidates:
        return None
    return min(candidates, key=lambda z: abs(z.distance_from_price))


def price_location(zones: Sequence[Zone], price: float, near_pct: float = 3.0) -> str:
    """Where price sits relative to its zones, in words the report can use."""
    resistance = nearest_zone(zones, SIDE_RESISTANCE)
    support = nearest_zone(zones, SIDE_SUPPORT)

    for zone in zones:
        if zone.contains(price):
            return f"AT_{zone.side}"

    to_resistance = abs(resistance.distance_from_price) if resistance else None
    to_support = abs(support.distance_from_price) if support else None

    if to_resistance is not None and to_resistance <= near_pct and (
        to_support is None or to_resistance <= to_support
    ):
        return "NEAR_RESISTANCE"
    if to_support is not None and to_support <= near_pct:
        return "NEAR_SUPPORT"
    return "MIDDLE"
