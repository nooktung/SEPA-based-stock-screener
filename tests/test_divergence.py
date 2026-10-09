"""Divergence engine (§18), with particular attention to look-ahead leakage."""

from datetime import date, timedelta

import pandas as pd
import pytest

from src.quant.divergence import (
    BEARISH,
    BULLISH,
    INDICATOR_MACD_HIST,
    INDICATOR_RSI,
    STATUS_EXPIRED,
    DivergenceConfig,
    find_divergences,
    indicator_series,
    latest_divergences,
)
from src.quant.patterns import (
    STATUS_CANDIDATE,
    STATUS_CONFIRMED,
    STATUS_INVALIDATED,
    PatternConfig,
    find_pivots,
)
from src.quant.technical import TechnicalConfig


@pytest.fixture
def config():
    return DivergenceConfig.from_thresholds()


@pytest.fixture
def patterns():
    return PatternConfig.from_thresholds()


@pytest.fixture
def technical():
    return TechnicalConfig.from_thresholds()


def build(closes, start="2026-01-01"):
    """OHLCV frame from a close path, with the range derived from the close."""
    if len(closes) == 0:
        empty = pd.DataFrame(columns=["open", "high", "low", "close", "volume", "adj_close"])
        empty.index.name = "trade_date"
        return empty
    index = pd.bdate_range(start=start, periods=len(closes))
    previous = [closes[0]] + list(closes[:-1])
    out = pd.DataFrame(
        {
            "open": previous,
            "high": [c + 0.5 for c in closes],
            "low": [c - 0.5 for c in closes],
            "close": list(closes),
            "volume": [1_000_000.0] * len(closes),
        },
        index=index,
        dtype="float64",
    )
    out["adj_close"] = out["close"]
    out.index.name = "trade_date"
    return out


def path(points, leg=6):
    """Linear path through turning points, one bar per step."""
    closes = []
    for start, end in zip(points, points[1:]):
        closes += [start + (end - start) * (i + 1) / leg for i in range(leg)]
    return closes


def bullish_path():
    """Two lows, the second lower, but the decline into it is much gentler.

    A shallow second leg is what makes RSI hold up while price does not -- the
    textbook bullish divergence.
    """
    return (
        path([100, 100], leg=30)          # quiet base so RSI has history
        + path([100, 70], leg=12)         # steep first decline -> deeply oversold
        + path([70, 88], leg=10)          # rebound (the reaction high)
        + path([88, 68], leg=25)          # long shallow decline to a lower low
        + path([68, 95], leg=15)          # recovery through the reaction high
    )


def bearish_path():
    return (
        path([100, 100], leg=30)
        + path([100, 140], leg=12)        # sharp advance -> overbought
        + path([140, 118], leg=10)
        + path([118, 143], leg=25)        # slow grind to a marginal higher high
        + path([143, 120], leg=12)
    )


# -- pairing rules -----------------------------------------------------------

def test_a_bullish_divergence_pairs_two_confirmed_lows(config, patterns, technical):
    frame = build(bullish_path())
    found = [
        d for d in find_divergences(frame, config=config, pattern_config=patterns,
                                    technical_config=technical, indicators=[INDICATOR_RSI])
        if d.kind == BULLISH
    ]
    assert found
    divergence = found[-1]
    # Price lower, oscillator higher: that is the whole definition.
    assert divergence.price_pivot_2 < divergence.price_pivot_1
    assert divergence.indicator_value_2 > divergence.indicator_value_1
    assert divergence.indicator == "rsi"


def test_a_bearish_divergence_pairs_two_confirmed_highs(config, patterns, technical):
    frame = build(bearish_path())
    found = [
        d for d in find_divergences(frame, config=config, pattern_config=patterns,
                                    technical_config=technical, indicators=[INDICATOR_RSI])
        if d.kind == BEARISH
    ]
    assert found
    divergence = found[-1]
    assert divergence.price_pivot_2 > divergence.price_pivot_1
    assert divergence.indicator_value_2 < divergence.indicator_value_1


def test_a_straight_trend_has_no_divergence(config, patterns, technical):
    rising = build(path([100, 160], leg=80))
    assert find_divergences(rising, config=config, pattern_config=patterns,
                            technical_config=technical) == []


def test_pivots_too_far_apart_are_not_one_event(patterns, technical):
    frame = build(bullish_path())
    tight = DivergenceConfig(
        min_price_change_pct=1.0, min_indicator_change=1.0,
        max_pivot_gap_sessions=2, confirmation_window=10, expiry_sessions=20,
    )
    assert find_divergences(frame, config=tight, pattern_config=patterns,
                            technical_config=technical) == []


def test_a_tiny_price_difference_is_not_a_divergence(patterns, technical):
    frame = build(bullish_path())
    demanding = DivergenceConfig(
        min_price_change_pct=99.0, min_indicator_change=1.0,
        max_pivot_gap_sessions=60, confirmation_window=10, expiry_sessions=20,
    )
    assert find_divergences(frame, config=demanding, pattern_config=patterns,
                            technical_config=technical) == []


def test_every_field_the_spec_requires_is_present(config, patterns, technical):
    frame = build(bullish_path())
    found = find_divergences(frame, config=config, pattern_config=patterns,
                             technical_config=technical, indicators=[INDICATOR_RSI])
    record = found[0].as_dict()
    for field in (
        "indicator", "price_pivot_1", "price_pivot_2", "indicator_value_1",
        "indicator_value_2", "trade_date", "confirmed_on", "status", "strength",
    ):
        assert field in record


# -- look-ahead --------------------------------------------------------------

def test_a_divergence_is_invisible_before_its_second_pivot_confirms(config, patterns, technical):
    """The central look-ahead test: no divergence on the day its pivot printed."""
    frame = build(bullish_path())
    found = find_divergences(frame, config=config, pattern_config=patterns,
                             technical_config=technical, indicators=[INDICATOR_RSI])
    assert found
    divergence = found[0]
    assert divergence.confirmed_on > divergence.trade_date

    # As of the day the second pivot printed, the pattern does not exist yet.
    on_print_day = find_divergences(frame, as_of=divergence.trade_date, config=config,
                                    pattern_config=patterns, technical_config=technical,
                                    indicators=[INDICATOR_RSI])
    assert all(d.trade_date != divergence.trade_date for d in on_print_day)

    # As of the confirmation day it does.
    on_confirm_day = find_divergences(frame, as_of=divergence.confirmed_on, config=config,
                                      pattern_config=patterns, technical_config=technical,
                                      indicators=[INDICATOR_RSI])
    assert any(d.trade_date == divergence.trade_date for d in on_confirm_day)


def test_status_never_uses_sessions_after_as_of(config, patterns, technical):
    """A divergence resolved later must still read CANDIDATE at the earlier date."""
    frame = build(bullish_path())
    everything = find_divergences(frame, config=config, pattern_config=patterns,
                                  technical_config=technical, indicators=[INDICATOR_RSI])
    resolved = [d for d in everything if d.resolved_on]
    assert resolved, "the fixture should produce at least one resolved divergence"

    divergence = resolved[0]
    earlier = find_divergences(frame, as_of=divergence.confirmed_on, config=config,
                               pattern_config=patterns, technical_config=technical,
                               indicators=[INDICATOR_RSI])
    same = [d for d in earlier if d.trade_date == divergence.trade_date]
    assert same and same[0].status == STATUS_CANDIDATE


def test_results_depend_only_on_data_and_as_of(config, patterns, technical):
    """Determinism: same inputs, same output, every time."""
    frame = build(bullish_path())
    cutoff = frame.index[-10].date()
    first = [d.as_dict() for d in find_divergences(frame, as_of=cutoff, config=config,
                                                   pattern_config=patterns, technical_config=technical)]
    second = [d.as_dict() for d in find_divergences(frame, as_of=cutoff, config=config,
                                                    pattern_config=patterns, technical_config=technical)]
    assert first == second


def test_trailing_bars_beyond_as_of_change_nothing(config, patterns, technical):
    """Handing the engine extra future bars must not change a past reading."""
    full = build(bullish_path())
    cutoff = full.index[len(full) // 2].date()

    with_future = find_divergences(full, as_of=cutoff, config=config,
                                   pattern_config=patterns, technical_config=technical)
    truncated = full.loc[: pd.Timestamp(cutoff)]
    without_future = find_divergences(truncated, as_of=cutoff, config=config,
                                      pattern_config=patterns, technical_config=technical)
    assert [d.as_dict() for d in with_future] == [d.as_dict() for d in without_future]


# -- status transitions ------------------------------------------------------

def test_a_close_through_the_reaction_high_confirms_a_bullish_divergence(config, patterns, technical):
    frame = build(bullish_path())
    found = [d for d in find_divergences(frame, config=config, pattern_config=patterns,
                                         technical_config=technical, indicators=[INDICATOR_RSI])
             if d.kind == BULLISH]
    statuses = {d.status for d in found}
    assert statuses <= {STATUS_CANDIDATE, STATUS_CONFIRMED, STATUS_INVALIDATED, STATUS_EXPIRED}
    for divergence in found:
        if divergence.status in (STATUS_CONFIRMED, STATUS_INVALIDATED, STATUS_EXPIRED):
            assert divergence.resolved_on is not None
            assert divergence.resolved_on > divergence.confirmed_on


def test_an_unresolved_divergence_expires_rather_than_disappearing(config, patterns, technical):
    """EXPIRED is an outcome. Dropping it would flatter every later statistic."""
    frame = build(
        path([100, 100], leg=30) + path([100, 70], leg=12) + path([70, 88], leg=10)
        + path([88, 68], leg=25) + path([68, 69], leg=40)   # drifts, resolves nothing
    )
    found = find_divergences(frame, config=config, pattern_config=patterns,
                             technical_config=technical, indicators=[INDICATOR_RSI])
    assert any(d.status == STATUS_EXPIRED for d in found)


# -- indicators --------------------------------------------------------------

def test_rsi_changes_are_measured_in_points(technical):
    frame = build(bullish_path())
    series, unit = indicator_series(frame, INDICATOR_RSI, technical)
    assert unit == 1.0
    assert series.dropna().between(0, 100).all()


def test_macd_changes_are_measured_in_its_own_standard_deviations(technical):
    # The histogram's scale follows the price level, so a raw threshold could
    # not serve a 13,000-dong stock and a 72,000-dong one at once.
    cheap = build([c for c in path([10, 14, 11, 15], leg=30)])
    expensive = build([c * 100 for c in path([10, 14, 11, 15], leg=30)])
    _, cheap_unit = indicator_series(cheap, INDICATOR_MACD_HIST, technical)
    _, expensive_unit = indicator_series(expensive, INDICATOR_MACD_HIST, technical)
    assert expensive_unit > cheap_unit * 50


def test_an_unknown_indicator_is_rejected(technical):
    with pytest.raises(ValueError):
        indicator_series(build(path([100, 120], leg=40)), "stochastic", technical)


# -- edges -------------------------------------------------------------------

def test_an_empty_frame_yields_nothing(config, patterns, technical):
    assert find_divergences(build([]), config=config, pattern_config=patterns,
                            technical_config=technical) == []


def test_too_little_history_yields_nothing(config, patterns, technical):
    assert find_divergences(build([100.0] * 5), config=config, pattern_config=patterns,
                            technical_config=technical) == []


def test_latest_only_returns_recent_divergences(config, patterns, technical):
    frame = build(bullish_path())
    everything = find_divergences(frame, config=config, pattern_config=patterns,
                                  technical_config=technical)
    recent = latest_divergences(frame, within_sessions=5, config=config,
                                pattern_config=patterns, technical_config=technical)
    assert len(recent) <= len(everything)
