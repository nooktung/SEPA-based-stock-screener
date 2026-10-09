"""Price structure and pattern detection.

The tests that matter most here are the look-ahead ones. A pattern detector
that can see one bar into the future looks excellent in backtest and is
worthless live, so several tests below exist purely to prove that a pattern is
not knowable before the information that defines it exists.
"""

from datetime import date

import pandas as pd
import pytest

from src.quant.patterns import (
    BEARISH_STRUCTURE,
    BREAKOUT_ATTEMPT,
    BREAKOUT_CONFIRMED,
    BREAKOUT_FAILED,
    BREAKOUT_NONE,
    BULLISH_STRUCTURE,
    NEUTRAL_STRUCTURE,
    STATUS_CANDIDATE,
    STATUS_CONFIRMED,
    STATUS_INVALIDATED,
    STATUS_NONE,
    PatternConfig,
    bear_trap,
    bull_trap,
    close_position_in_range,
    find_pivots,
    lower_wick_ratio,
    pivots_known_by,
    read_breakout,
    read_structure,
    resolve_trap,
    upper_wick_ratio,
)


@pytest.fixture
def config():
    return PatternConfig.from_thresholds()


def frame(rows, start="2026-01-01"):
    """Build an OHLCV frame from (open, high, low, close) or (o,h,l,c,volume)."""
    index = pd.bdate_range(start=start, periods=len(rows))
    data = {
        "open": [r[0] for r in rows],
        "high": [r[1] for r in rows],
        "low": [r[2] for r in rows],
        "close": [r[3] for r in rows],
        "volume": [r[4] if len(r) > 4 else 1_000_000 for r in rows],
    }
    out = pd.DataFrame(data, index=index, dtype="float64")
    out["adj_close"] = out["close"]
    out.index.name = "trade_date"
    return out


def flat(n, price=100.0):
    return [(price, price + 1, price - 1, price)] * n


# -- candle geometry ---------------------------------------------------------

def test_close_position_is_zero_at_the_low_and_one_at_the_high():
    assert close_position_in_range(110, 100, 100) == pytest.approx(0.0)
    assert close_position_in_range(110, 100, 110) == pytest.approx(1.0)
    assert close_position_in_range(110, 100, 105) == pytest.approx(0.5)


def test_a_session_with_no_range_has_no_position():
    assert close_position_in_range(100, 100, 100) is None


def test_wick_ratios_measure_rejection():
    # Opened 100, ran to 120, closed back at 102: most of the range was wick.
    assert upper_wick_ratio(100, 120, 99, 102) == pytest.approx(18 / 21, abs=0.01)
    assert lower_wick_ratio(100, 120, 99, 102) == pytest.approx(1 / 21, abs=0.01)


def test_missing_prices_produce_no_geometry():
    assert upper_wick_ratio(None, 120, 99, 102) is None
    assert close_position_in_range(None, 100, 100) is None


# -- pivots and look-ahead ---------------------------------------------------

def test_a_pivot_is_only_knowable_after_its_right_hand_bars(config):
    rows = flat(4) + [(100, 130, 99, 128)] + flat(6)
    pivots = find_pivots(frame(rows), strength=3)
    assert len(pivots) == 1

    pivot = pivots[0]
    assert pivot.kind == "HIGH"
    # It printed on one date and could first be known three sessions later.
    assert pivot.confirmed_on > pivot.trade_date
    assert pivot.confirmed_on == frame(rows).index[4 + 3].date()


def test_a_pivot_is_invisible_on_the_day_it_prints(config):
    rows = flat(4) + [(100, 130, 99, 128)] + flat(6)
    all_pivots = find_pivots(frame(rows), strength=3)
    printed_on = all_pivots[0].trade_date

    # This is the whole look-ahead question: on the day the high printed,
    # nobody could know it was a swing high.
    assert pivots_known_by(all_pivots, printed_on) == []
    assert pivots_known_by(all_pivots, all_pivots[0].confirmed_on) == all_pivots


def test_too_little_history_yields_no_pivots():
    assert find_pivots(frame(flat(4)), strength=3) == []


def test_a_flat_series_has_no_pivots():
    assert find_pivots(frame(flat(30)), strength=3) == []


# -- structure ---------------------------------------------------------------

def _zigzag(turning_points, leg=6):
    """A price path through the given turning points, one bar per step.

    Built from closes and derived OHLC so the swing highs and lows fall
    naturally at the turns, the way they do in a real series.
    """
    closes = []
    for start, end in zip(turning_points, turning_points[1:]):
        closes += [start + (end - start) * (i + 1) / leg for i in range(leg)]
    rows, previous = [], turning_points[0]
    for close in closes:
        # Range derived from the close alone, so the extreme bar of a turn is a
        # strict extreme. Deriving it from open and close instead makes the
        # turning bar tie with its neighbour and no pivot is found.
        rows.append((previous, close + 0.5, close - 0.5, close))
        previous = close
    return rows


def test_rising_peaks_and_troughs_read_as_bullish_structure(config):
    # Two peaks and two troughs, each rising, plus a final leg so the last
    # peak has the bars it needs to be confirmed.
    rising = frame(_zigzag([100, 96, 112, 106, 124, 120]))
    read = read_structure(rising, config)
    assert read.structure == BULLISH_STRUCTURE
    assert read.swing_sequence == ["HH", "HL"]


def test_falling_peaks_and_troughs_read_as_bearish_structure(config):
    # Peaks 128 then 118, troughs 108 then 100: lower highs and lower lows.
    falling = frame(_zigzag([110, 128, 108, 118, 100, 106]))
    read = read_structure(falling, config)
    assert read.structure == BEARISH_STRUCTURE
    assert read.swing_sequence == ["LH", "LL"]


def test_without_pivots_structure_is_neutral_not_guessed(config):
    assert read_structure(frame(flat(30)), config).structure == NEUTRAL_STRUCTURE


def test_an_empty_frame_is_neutral(config):
    assert read_structure(frame([]), config).structure == NEUTRAL_STRUCTURE


# -- breakouts ---------------------------------------------------------------

def test_a_close_above_the_prior_range_is_a_confirmed_breakout(config):
    rows = flat(30, 100) + [(101, 112, 100, 111)]
    read = read_breakout(frame(rows), window=20, config=config)
    assert read.state == BREAKOUT_CONFIRMED
    assert read.level == pytest.approx(101.0)
    assert read.distance_pct > 0


def test_trading_above_the_level_without_closing_there_is_only_an_attempt(config):
    rows = flat(30, 100) + [(100, 115, 99, 100.5)]
    read = read_breakout(frame(rows), window=20, config=config)
    assert read.state == BREAKOUT_ATTEMPT


def test_a_quiet_session_inside_the_range_is_no_breakout(config):
    # Every bar identical: today never trades above the prior highs.
    read = read_breakout(frame(flat(30, 100)), window=20, config=config)
    assert read.state == BREAKOUT_NONE


def test_the_level_excludes_today_so_a_new_high_is_not_circular(config):
    # If today's own high set the level, every new high would trivially be a
    # breakout and the signal would mean nothing. Resistance here is the prior
    # sessions' highest traded price -- 101, not today's 130.
    rows = flat(30, 100) + [(101, 130, 100, 129)]
    read = read_breakout(frame(rows), window=20, config=config)
    assert read.level == pytest.approx(101.0)


def test_giving_back_a_confirmed_breakout_reads_as_failed(config):
    rows = flat(30, 100) + [(101, 112, 100, 111)] + [(110, 111, 95, 96)]
    read = read_breakout(frame(rows), window=20, config=config)
    assert read.state == BREAKOUT_FAILED
    assert read.sessions_since_confirmed == 1


def test_short_history_produces_no_breakout_verdict(config):
    assert read_breakout(frame(flat(5)), window=20, config=config).state == BREAKOUT_NONE


# -- bull trap ---------------------------------------------------------------

def _trap_rows():
    # Thirty flat sessions at 100, then: opens 101, spikes to 115, closes 99
    # at the bottom of its own range on heavy volume.
    return flat(30, 100) + [(101, 115, 98, 99, 5_000_000)]


def test_a_failed_upside_break_scores_as_a_bull_trap_candidate(config):
    read = bull_trap(frame(_trap_rows()), config, window=20, volume_ratio=2.5)
    assert read.status == STATUS_CANDIDATE
    assert read.components["broke_intraday"] and read.components["closed_below"]
    assert read.components["weak_close"] and read.components["upper_wick"]
    assert read.components["volume_expansion"]
    assert read.score == pytest.approx(100.0)
    assert read.reasons


def test_a_clean_breakout_is_not_a_trap(config):
    rows = flat(30, 100) + [(101, 112, 100, 111, 5_000_000)]
    read = bull_trap(frame(rows), config, window=20, volume_ratio=2.5)
    assert read.status == STATUS_NONE
    assert read.score == 0.0


def test_a_session_that_never_tested_the_level_is_not_a_trap(config):
    rows = flat(30, 100) + [(99, 99.5, 97, 97.5, 5_000_000)]
    assert bull_trap(frame(rows), config, window=20).status == STATUS_NONE


def test_each_missing_component_lowers_the_score(config):
    full = bull_trap(frame(_trap_rows()), config, window=20, volume_ratio=2.5)
    # Same candle, ordinary volume: one component fewer.
    quiet = bull_trap(frame(_trap_rows()), config, window=20, volume_ratio=0.9)
    assert quiet.score < full.score
    assert quiet.status == STATUS_CANDIDATE


def test_a_strong_close_after_the_break_scores_lower(config):
    # Poked above and closed near the high: much weaker evidence of rejection.
    rows = flat(30, 100) + [(101, 115, 98, 113, 5_000_000)]
    read = bull_trap(frame(rows), config, window=20, volume_ratio=2.5)
    assert read.status == STATUS_NONE  # closed above the level, nothing failed


# -- bear trap ---------------------------------------------------------------

def test_a_failed_downside_break_scores_as_a_bear_trap_candidate(config):
    rows = flat(30, 100) + [(99, 101, 85, 100.5, 5_000_000)]
    read = bear_trap(frame(rows), config, window=20, volume_ratio=2.5)
    assert read.status == STATUS_CANDIDATE
    assert read.components["broke_intraday"] and read.components["closed_above"]
    assert read.score > 0


def test_a_genuine_breakdown_is_not_a_bear_trap(config):
    rows = flat(30, 100) + [(99, 100, 85, 86, 5_000_000)]
    assert bear_trap(frame(rows), config, window=20).status == STATUS_NONE


# -- resolution --------------------------------------------------------------

def test_a_trap_is_only_a_candidate_until_something_follows(config):
    data = frame(_trap_rows())
    trap = bull_trap(data, config, window=20, volume_ratio=2.5)
    # Nothing after it yet: on the day itself, that is all that can be said.
    assert resolve_trap(data, len(data) - 1, trap, config) == STATUS_CANDIDATE


def test_a_close_below_the_candidate_low_confirms_the_trap(config):
    rows = _trap_rows() + [(98, 99, 94, 95)]
    data = frame(rows)
    trap = bull_trap(frame(_trap_rows()), config, window=20, volume_ratio=2.5)
    assert resolve_trap(data, len(data) - 2, trap, config) == STATUS_CONFIRMED


def test_reclaiming_the_level_invalidates_the_trap(config):
    rows = _trap_rows() + [(100, 106, 99, 105)]
    data = frame(rows)
    trap = bull_trap(frame(_trap_rows()), config, window=20, volume_ratio=2.5)
    assert resolve_trap(data, len(data) - 2, trap, config) == STATUS_INVALIDATED


def test_resolution_only_reads_sessions_after_the_candidate(config):
    """The confirmation must come from the future of the candidate, not its past."""
    rows = _trap_rows() + [(100, 106, 99, 105)]
    data = frame(rows)
    trap = bull_trap(frame(_trap_rows()), config, window=20, volume_ratio=2.5)

    # Evaluated as of the candidate day itself, the later session does not exist.
    as_of_candidate = data.iloc[: len(data) - 1]
    assert resolve_trap(as_of_candidate, len(as_of_candidate) - 1, trap, config) == STATUS_CANDIDATE


# -- configuration -----------------------------------------------------------

def test_thresholds_drive_the_detector(config):
    from src.config import load_config

    configured = load_config().thresholds["patterns"]
    assert config.swing_strength == configured["swing_strength"]
    assert config.weak_close_position == configured["weak_close_position"]
    assert sum(config.bull_trap_weights.values()) == pytest.approx(100.0)
    assert sum(config.bear_trap_weights.values()) == pytest.approx(100.0)


def test_the_breakout_margin_is_shared_with_the_technical_engine(config):
    from src.config import load_config

    assert config.breakout_confirm_pct == load_config().thresholds["technical"]["breakout_confirm_close_pct"]


# -- precomputed levels ------------------------------------------------------

def test_a_supplied_level_matches_the_one_the_detector_would_compute(config):
    """The backtest hands in a level computed once for the whole history.

    That optimisation is only safe if it is the same number, so this pins the
    two paths together rather than trusting that they stayed in step.
    """
    from src.quant import indicators as ind

    data = frame(_trap_rows())
    computed = ind.rolling_high(data["high"], 20, exclude_current=True).iloc[-1]

    internal = bull_trap(data, config, window=20, volume_ratio=2.5)
    supplied = bull_trap(data, config, window=20, volume_ratio=2.5, level=float(computed))

    assert internal.level == pytest.approx(supplied.level)
    assert internal.score == supplied.score
    assert internal.status == supplied.status
    assert internal.components == supplied.components


def test_a_supplied_level_matches_for_the_bear_side(config):
    from src.quant import indicators as ind

    rows = flat(30, 100) + [(99, 101, 85, 100.5, 5_000_000)]
    data = frame(rows)
    computed = ind.rolling_low(data["low"], 20, exclude_current=True).iloc[-1]

    internal = bear_trap(data, config, window=20, volume_ratio=2.5)
    supplied = bear_trap(data, config, window=20, volume_ratio=2.5, level=float(computed))
    assert internal.score == supplied.score
    assert internal.components == supplied.components
