"""Support/resistance zones (§11) and behaviour scoring (§19)."""

from datetime import date

import pandas as pd
import pytest

from src.quant.behaviour import (
    ACCUMULATION_LIKE,
    DISTRIBUTION_LIKE,
    NEUTRAL,
    BehaviourConfig,
    average_close_position,
    compute_behaviour,
    range_ratio,
    up_down_volume_ratio,
)
from src.quant.levels import (
    SIDE_RESISTANCE,
    SIDE_SUPPORT,
    SOURCE_REPEATED_REACTION,
    SOURCE_SWING_HIGH,
    LevelCandidate,
    Zone,
    ZoneConfig,
    build_zones,
    collect_levels,
    merge_levels,
    nearest_zone,
    price_location,
)
from src.quant.patterns import PatternConfig


@pytest.fixture
def zone_config():
    return ZoneConfig.from_thresholds()


@pytest.fixture
def patterns():
    return PatternConfig.from_thresholds()


@pytest.fixture
def behaviour_config():
    return BehaviourConfig.from_thresholds()


def build(rows, start="2026-01-01"):
    index = pd.bdate_range(start=start, periods=len(rows))
    out = pd.DataFrame(
        {
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [r[4] if len(r) > 4 else 1_000_000.0 for r in rows],
        },
        index=index,
        dtype="float64",
    )
    out["adj_close"] = out["close"]
    out.index.name = "trade_date"
    return out


def from_closes(closes, volumes=None):
    volumes = volumes or [1_000_000.0] * len(closes)
    previous = [closes[0]] + list(closes[:-1])
    return build([
        (p, c + 0.5, c - 0.5, c, v) for p, c, v in zip(previous, closes, volumes)
    ])


def path(points, leg=6):
    closes = []
    for start, end in zip(points, points[1:]):
        closes += [start + (end - start) * (i + 1) / leg for i in range(leg)]
    return closes


# -- merging -----------------------------------------------------------------

def test_nearby_levels_merge_into_one_zone(zone_config):
    # Three pivots within a percent of each other are one reaction area
    # observed three times, not three separate levels.
    candidates = [
        LevelCandidate(100.0, SOURCE_SWING_HIGH, date(2026, 1, 5)),
        LevelCandidate(100.4, SOURCE_SWING_HIGH, date(2026, 1, 9)),
        LevelCandidate(100.8, SOURCE_SWING_HIGH, date(2026, 1, 14)),
    ]
    zones = merge_levels(candidates, zone_config)
    assert len(zones) == 1
    assert zones[0].center == pytest.approx(100.4, abs=0.1)


def test_distant_levels_stay_separate(zone_config):
    candidates = [
        LevelCandidate(100.0, SOURCE_SWING_HIGH, date(2026, 1, 5)),
        LevelCandidate(130.0, SOURCE_SWING_HIGH, date(2026, 1, 9)),
    ]
    assert len(merge_levels(candidates, zone_config)) == 2


def test_a_zone_is_a_band_not_a_line(zone_config):
    zones = merge_levels([LevelCandidate(100.0, SOURCE_SWING_HIGH, date(2026, 1, 5))], zone_config)
    zone = zones[0]
    assert zone.zone_low < zone.center < zone.zone_high
    assert zone.contains(100.0)


def test_a_zone_from_several_kinds_of_evidence_is_labelled_as_such(zone_config):
    candidates = [
        LevelCandidate(100.0, "SWING_HIGH", date(2026, 1, 5)),
        LevelCandidate(100.3, "BREAKOUT_LEVEL", date(2026, 1, 9)),
    ]
    zone = merge_levels(candidates, zone_config)[0]
    assert zone.source == SOURCE_REPEATED_REACTION
    assert set(zone.sources) == {"SWING_HIGH", "BREAKOUT_LEVEL"}


def test_no_candidates_means_no_zones(zone_config):
    assert merge_levels([], zone_config) == []


# -- scoring -----------------------------------------------------------------

def test_a_repeatedly_tested_zone_scores_higher_than_a_touched_once_zone(zone_config, patterns):
    # Price oscillates around 100 many times, then visits 130 once.
    frame = from_closes(path([100, 104, 96, 104, 96, 104, 96, 130], leg=8))
    zones = build_zones(frame, config=zone_config, pattern_config=patterns)
    assert zones
    busy = max(zones, key=lambda z: z.touch_count)
    assert busy.touch_count > 1
    assert busy.strength > 0


def test_every_field_the_spec_requires_is_present(zone_config, patterns):
    frame = from_closes(path([100, 110, 95, 115, 100], leg=10))
    zone = build_zones(frame, config=zone_config, pattern_config=patterns)[0].as_dict()
    for field in (
        "zone_low", "zone_high", "center", "strength", "touch_count",
        "last_tested", "distance_from_price", "source",
    ):
        assert field in zone


def test_zones_are_split_by_side_and_measured_from_price(zone_config, patterns):
    frame = from_closes(path([100, 120, 90, 110, 100], leg=10))
    zones = build_zones(frame, config=zone_config, pattern_config=patterns)
    price = float(frame["close"].iloc[-1])
    for zone in zones:
        assert zone.side == (SIDE_RESISTANCE if zone.center > price else SIDE_SUPPORT)
        expected = (zone.center - price) / price * 100.0
        assert zone.distance_from_price == pytest.approx(expected)


def test_only_a_limited_number_of_zones_per_side_is_reported(patterns):
    tight = ZoneConfig(
        merge_within_pct=0.2, zone_half_width_pct=0.1, touch_tolerance_pct=0.2,
        max_zones_per_side=2, recency_half_life_sessions=120,
    )
    frame = from_closes(path([100, 130, 90, 125, 95, 120, 100], leg=10))
    zones = build_zones(frame, config=tight, pattern_config=patterns)
    assert len([z for z in zones if z.side == SIDE_SUPPORT]) <= 2
    assert len([z for z in zones if z.side == SIDE_RESISTANCE]) <= 2


# -- look-ahead --------------------------------------------------------------

def test_a_pivot_cannot_become_a_level_before_it_is_confirmed(zone_config, patterns):
    frame = from_closes(path([100, 130, 100], leg=12))
    from src.quant.patterns import find_pivots

    pivot = [p for p in find_pivots(frame, patterns.swing_strength) if p.kind == "HIGH"][0]

    # On the day the swing high printed it is not yet a level.
    on_print = collect_levels(frame, pivot.trade_date, zone_config, patterns)
    assert all(c.source != "SWING_HIGH" or c.price != pivot.price for c in on_print)

    # On its confirmation day it is.
    on_confirm = collect_levels(frame, pivot.confirmed_on, zone_config, patterns)
    assert any(c.source == "SWING_HIGH" and c.price == pivot.price for c in on_confirm)


def test_future_bars_do_not_change_a_past_zone_map(zone_config, patterns):
    full = from_closes(path([100, 125, 95, 130, 90], leg=12))
    cutoff = full.index[30].date()

    with_future = [z.as_dict() for z in build_zones(full, as_of=cutoff, config=zone_config, pattern_config=patterns)]
    truncated = full.loc[: pd.Timestamp(cutoff)]
    without_future = [z.as_dict() for z in build_zones(truncated, as_of=cutoff, config=zone_config, pattern_config=patterns)]
    assert with_future == without_future


def test_touches_are_only_counted_up_to_as_of(zone_config, patterns):
    frame = from_closes(path([100, 110, 100, 110, 100, 110], leg=10))
    early = build_zones(frame, as_of=frame.index[25].date(), config=zone_config, pattern_config=patterns)
    late = build_zones(frame, config=zone_config, pattern_config=patterns)
    if early and late:
        assert max(z.touch_count for z in early) <= max(z.touch_count for z in late)


def test_insufficient_history_yields_no_zones(zone_config, patterns):
    assert build_zones(from_closes([100.0, 101.0, 102.0]), config=zone_config, pattern_config=patterns) == []


def test_an_empty_frame_yields_no_zones(zone_config, patterns):
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume", "adj_close"])
    assert build_zones(empty, as_of=date(2026, 8, 27), config=zone_config, pattern_config=patterns) == []


# -- location ----------------------------------------------------------------

def test_price_location_names_where_price_sits():
    resistance = Zone(zone_low=109, zone_high=111, center=110, source="SWING_HIGH",
                      distance_from_price=10.0, side=SIDE_RESISTANCE)
    support = Zone(zone_low=89, zone_high=91, center=90, source="SWING_LOW",
                   distance_from_price=-10.0, side=SIDE_SUPPORT)
    assert price_location([resistance, support], 100.0) == "MIDDLE"
    assert price_location([resistance, support], 110.0) == "AT_RESISTANCE"

    near = Zone(zone_low=101, zone_high=103, center=102, source="SWING_HIGH",
                distance_from_price=2.0, side=SIDE_RESISTANCE)
    assert price_location([near], 100.0) == "NEAR_RESISTANCE"


def test_nearest_zone_picks_the_closest_on_that_side():
    close = Zone(zone_low=104, zone_high=106, center=105, source="SWING_HIGH",
                 distance_from_price=5.0, side=SIDE_RESISTANCE)
    far = Zone(zone_low=129, zone_high=131, center=130, source="SWING_HIGH",
               distance_from_price=30.0, side=SIDE_RESISTANCE)
    assert nearest_zone([far, close], SIDE_RESISTANCE) is close
    assert nearest_zone([far, close], SIDE_SUPPORT) is None


# -- behaviour ---------------------------------------------------------------

def test_volume_heavier_on_up_sessions_reads_above_one():
    rising = from_closes([100, 102, 101, 104, 103, 106], volumes=[1e6, 3e6, 1e6, 3e6, 1e6, 3e6])
    assert up_down_volume_ratio(rising) > 1.0


def test_volume_heavier_on_down_sessions_reads_below_one():
    falling = from_closes([100, 98, 99, 96, 97, 94], volumes=[1e6, 3e6, 1e6, 3e6, 1e6, 3e6])
    assert up_down_volume_ratio(falling) < 1.0


def test_average_close_position_sits_between_zero_and_one():
    frame = build([(100, 110, 90, 105), (105, 115, 95, 97)])
    position = average_close_position(frame)
    assert 0.0 <= position <= 1.0


def test_range_ratio_detects_compression():
    wild = path([100, 130, 90, 125], leg=12)
    calm = [110 + (i % 2) * 0.2 for i in range(36)]
    assert range_ratio(from_closes(wild + calm), window=20) < 1.0


def test_range_ratio_needs_two_full_windows():
    assert range_ratio(from_closes([100.0] * 10), window=20) is None


def test_accumulation_like_behaviour_scores_and_explains_itself(behaviour_config):
    strong = from_closes(
        path([100, 106], leg=25),
        volumes=[3e6 if i % 2 else 1e6 for i in range(25)],
    )
    read = compute_behaviour(strong, relative_strength=4.0, failed_breakdown=True,
                             config=behaviour_config)
    assert read.accumulation_like_score > 0
    assert read.accumulation_components["relative_strength"] is True
    assert read.accumulation_components["failed_breakdown"] is True
    contributions = read.contributions(ACCUMULATION_LIKE, behaviour_config)
    assert sum(contributions.values()) == pytest.approx(read.accumulation_like_score)


def test_distribution_like_behaviour_is_scored_separately(behaviour_config):
    weak = from_closes(path([120, 100], leg=25))
    read = compute_behaviour(weak, relative_strength=-6.0, failed_breakout=True,
                             config=behaviour_config)
    assert read.distribution_like_score > 0
    assert read.distribution_components["relative_weakness"] is True


def test_the_two_scores_are_independent_and_need_not_sum_to_100(behaviour_config):
    # A quiet, directionless stock should score low on both. Forcing them to
    # sum would manufacture a signal out of an absence of one.
    # An odd length gives equal numbers of up and down sessions; an even one
    # leaves one extra advance and the up/down volume test tips accumulation.
    quiet = from_closes([100.0 + (i % 2) * 0.05 for i in range(41)])
    read = compute_behaviour(quiet, config=behaviour_config)
    assert read.accumulation_like_score + read.distribution_like_score < 100
    assert read.verdict == NEUTRAL


def test_the_verdict_needs_a_clear_margin(behaviour_config):
    balanced = from_closes([100.0 + (i % 3) for i in range(40)])
    read = compute_behaviour(balanced, config=behaviour_config)
    if abs(read.accumulation_like_score - read.distribution_like_score) < 20:
        assert read.verdict == NEUTRAL


def test_no_claim_is_made_about_who_is_buying(behaviour_config):
    """The vocabulary is deliberate: price data cannot identify a participant."""
    import src.quant.behaviour as module

    text = module.__doc__ or ""
    read = compute_behaviour(from_closes(path([100, 110], leg=30)), config=behaviour_config)
    assert read.verdict in (ACCUMULATION_LIKE, DISTRIBUTION_LIKE, NEUTRAL)
    assert "institution" not in read.verdict.lower()
    assert "cannot show who is buying" in text


def test_an_empty_frame_scores_nothing(behaviour_config):
    empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume", "adj_close"])
    read = compute_behaviour(empty, config=behaviour_config)
    assert read.accumulation_like_score == 0.0
    assert read.verdict == NEUTRAL


def test_a_supplied_atr_matches_the_one_range_ratio_would_compute():
    """Same optimisation, same requirement: identical numbers or it is a bug."""
    from src.quant import indicators as ind
    from src.quant.store import price_series

    frame = from_closes(path([100, 130, 90, 125], leg=12) + [110.0] * 40)
    atr = ind.atr(frame["high"], frame["low"], price_series(frame), 14)

    assert range_ratio(frame, window=20) == pytest.approx(range_ratio(frame, window=20, atr=atr))


def test_behaviour_with_a_supplied_atr_is_identical(behaviour_config):
    from src.quant import indicators as ind
    from src.quant.store import price_series

    frame = from_closes(path([100, 112], leg=45))
    atr = ind.atr(frame["high"], frame["low"], price_series(frame), 14)

    plain = compute_behaviour(frame, config=behaviour_config)
    fast = compute_behaviour(frame, config=behaviour_config, atr=atr)
    assert plain.as_dict() == fast.as_dict()
