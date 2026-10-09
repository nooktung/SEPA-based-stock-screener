"""Breakout/breakdown lifecycles (§15, §16) and shakeouts (§17).

The lifecycle tests exist mainly to prove one thing: a setup date is never
retroactively relabelled with an outcome that happened later.
"""

from datetime import date

import pandas as pd
import pytest

from src.quant.events import (
    BREAKDOWN,
    BREAKOUT,
    STATE_ATTEMPT,
    STATE_CONFIRMED,
    STATE_EXPIRED,
    STATE_FAILED,
    BreakEvent,
    ShakeoutConfig,
    events_known_by,
    failed_breaks,
    find_shakeouts,
    track_break_events,
)
from src.quant.patterns import (
    STATUS_CANDIDATE,
    STATUS_CONFIRMED,
    STATUS_INVALIDATED,
    PatternConfig,
)


@pytest.fixture
def patterns():
    return PatternConfig.from_thresholds()


@pytest.fixture
def shakeout_config():
    return ShakeoutConfig.from_thresholds()


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


def flat(n, price=100.0):
    return [(price, price + 1, price - 1, price)] * n


# -- lifecycle ---------------------------------------------------------------

def test_a_breakout_that_holds_records_setup_and_confirmation(patterns):
    rows = flat(25) + [(101, 105, 100, 104)] + flat(3, 104)
    events = track_break_events(build(rows), window=20, kind=BREAKOUT, config=patterns)
    assert len(events) == 1

    event = events[0]
    assert event.setup_date is not None
    assert event.confirmation_date is not None
    assert event.failure_date is None
    assert event.status == STATE_CONFIRMED


def test_the_three_dates_stay_separate(patterns):
    """setup, confirmation and failure are distinct moments, never one label."""
    rows = flat(25) + [(101, 105, 100, 104)] + [(104, 105, 103, 104)] + [(104, 104, 98, 99)]
    events = track_break_events(build(rows), window=20, kind=BREAKOUT, config=patterns)
    event = events[0]

    assert event.status == STATE_FAILED
    assert event.setup_date < event.confirmation_date < event.failure_date


def test_an_event_is_not_retroactively_labelled_failed(patterns):
    """The heart of §15: on the setup date, nobody knew it would fail."""
    rows = flat(25) + [(101, 105, 100, 104)] + [(104, 105, 103, 104)] + [(104, 104, 98, 99)]
    frame = build(rows)
    event = track_break_events(frame, window=20, kind=BREAKOUT, config=patterns)[0]

    assert event.status_as_of(event.setup_date) == STATE_ATTEMPT
    assert event.status_as_of(event.confirmation_date) == STATE_CONFIRMED
    assert event.status_as_of(event.failure_date) == STATE_FAILED
    # And it did not exist at all the day before it started.
    day_before = frame.index[frame.index.get_loc(pd.Timestamp(event.setup_date)) - 1].date()
    assert event.status_as_of(day_before) is None


def test_an_attempt_that_never_confirms_expires(patterns):
    # Pokes above the level repeatedly but never closes there.
    rows = flat(25) + [(100, 104, 99, 100)] * 8
    events = track_break_events(build(rows), window=20, kind=BREAKOUT, config=patterns)
    assert events
    assert any(e.status == STATE_EXPIRED for e in events)
    assert all(e.confirmation_date is None for e in events if e.status == STATE_EXPIRED)


def test_a_breakdown_has_the_same_lifecycle(patterns):
    rows = flat(25) + [(99, 100, 95, 96)] + [(96, 97, 95, 96)] + [(96, 102, 96, 101)]
    events = track_break_events(build(rows), window=20, kind=BREAKDOWN, config=patterns)
    assert events
    event = events[0]
    assert event.kind == BREAKDOWN
    assert event.status in (STATE_ATTEMPT, STATE_CONFIRMED, STATE_FAILED, STATE_EXPIRED)
    if event.status == STATE_FAILED:
        assert event.setup_date < event.failure_date


def test_maximum_excursion_is_recorded(patterns):
    rows = flat(25) + [(101, 120, 100, 104)] + flat(3, 104)
    event = track_break_events(build(rows), window=20, kind=BREAKOUT, config=patterns)[0]
    assert event.max_excursion_pct > 0


def test_every_field_the_spec_requires_is_present(patterns):
    rows = flat(25) + [(101, 105, 100, 104)] + flat(3, 104)
    record = track_break_events(build(rows), window=20, kind=BREAKOUT, config=patterns)[0].as_dict()
    for field in ("setup_date", "confirmation_date", "failure_date", "status", "level"):
        assert field in record


# -- look-ahead --------------------------------------------------------------

def test_future_sessions_do_not_change_a_past_event_list(patterns):
    rows = flat(25) + [(101, 105, 100, 104)] + [(104, 105, 103, 104)] + [(104, 104, 98, 99)] + flat(5, 99)
    full = build(rows)
    cutoff = full.index[26].date()

    with_future = [e.as_dict() for e in track_break_events(full, 20, BREAKOUT, as_of=cutoff, config=patterns)]
    truncated = full.loc[: pd.Timestamp(cutoff)]
    without_future = [e.as_dict() for e in track_break_events(truncated, 20, BREAKOUT, as_of=cutoff, config=patterns)]
    assert with_future == without_future


def test_a_failure_is_only_visible_once_it_has_happened(patterns):
    rows = flat(25) + [(101, 105, 100, 104)] + [(104, 105, 103, 104)] + [(104, 104, 98, 99)]
    frame = build(rows)
    events = track_break_events(frame, 20, BREAKOUT, config=patterns)
    failure_day = events[0].failure_date

    before = failed_breaks(events, as_of=frame.index[26].date())
    after = failed_breaks(events, as_of=failure_day)
    assert before == []
    assert len(after) == 1


def test_events_known_by_filters_on_the_setup_date(patterns):
    rows = flat(25) + [(101, 105, 100, 104)] + flat(3, 104)
    frame = build(rows)
    events = track_break_events(frame, 20, BREAKOUT, config=patterns)
    assert events_known_by(events, frame.index[10].date()) == []
    assert len(events_known_by(events, frame.index[-1].date())) == 1


def test_too_little_history_produces_no_events(patterns):
    assert track_break_events(build(flat(5)), window=20, kind=BREAKOUT, config=patterns) == []


# -- shakeout ----------------------------------------------------------------

def _shakeout_rows():
    # Twenty quiet sessions, then a session that breaks the 20-session low,
    # leaves a long lower wick and closes back near its high.
    return flat(25, 100) + [(99.5, 100.0, 92.0, 99.6)]


def test_a_violation_that_recovers_is_a_shakeout_candidate(shakeout_config, patterns):
    found = find_shakeouts(build(_shakeout_rows()), config=shakeout_config, pattern_config=patterns)
    assert found
    shakeout = found[-1]
    assert shakeout.status == STATUS_CANDIDATE
    assert shakeout.components["broke_support"] and shakeout.components["closed_above"]
    assert shakeout.components["long_lower_wick"]
    assert shakeout.score > 0


def test_a_long_lower_wick_alone_is_not_a_shakeout(shakeout_config, patterns):
    """A stock can print a long wick on the way to a much lower low."""
    rows = flat(25, 100) + [(99.5, 100.0, 92.0, 94.0)]   # closes below support
    assert find_shakeouts(build(rows), config=shakeout_config, pattern_config=patterns) == []


def test_an_ordinary_dip_is_not_a_shakeout(shakeout_config, patterns):
    rows = flat(25, 100) + [(99.5, 100.0, 98.8, 99.2)]   # never breaks support
    assert find_shakeouts(build(rows), config=shakeout_config, pattern_config=patterns) == []


def test_a_shakeout_confirms_when_price_clears_the_candidate_high(shakeout_config, patterns):
    rows = _shakeout_rows() + [(100, 103, 99.5, 102)]
    found = find_shakeouts(build(rows), config=shakeout_config, pattern_config=patterns)
    resolved = [s for s in found if s.status == STATUS_CONFIRMED]
    assert resolved
    assert resolved[0].resolved_on > resolved[0].setup_date


def test_a_shakeout_is_invalidated_by_a_close_under_its_low(shakeout_config, patterns):
    rows = _shakeout_rows() + [(99, 99.5, 88, 90)]
    found = find_shakeouts(build(rows), config=shakeout_config, pattern_config=patterns)
    assert any(s.status == STATUS_INVALIDATED for s in found)


def test_a_shakeout_reads_as_candidate_on_its_own_day(shakeout_config, patterns):
    rows = _shakeout_rows() + [(100, 103, 99.5, 102)]
    frame = build(rows)
    shakeout = find_shakeouts(frame, config=shakeout_config, pattern_config=patterns)[-1]
    # It confirmed later, but on the day itself only a candidate was knowable.
    assert shakeout.status_as_of(shakeout.setup_date) == STATUS_CANDIDATE
    assert shakeout.status_as_of(shakeout.resolved_on) == STATUS_CONFIRMED


def test_volume_expansion_adds_to_the_score(shakeout_config, patterns):
    frame = build(_shakeout_rows())
    quiet = find_shakeouts(frame, config=shakeout_config, pattern_config=patterns)[-1]

    ratios = pd.Series(3.0, index=frame.index)
    heavy = find_shakeouts(frame, config=shakeout_config, pattern_config=patterns,
                           volume_ratios=ratios)[-1]
    assert heavy.score > quiet.score
    assert heavy.components["volume_expansion"] is True


def test_shakeouts_need_history(shakeout_config, patterns):
    assert find_shakeouts(build(flat(5)), config=shakeout_config, pattern_config=patterns) == []
