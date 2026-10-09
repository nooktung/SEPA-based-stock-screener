"""Score calibration.

The tests here are mostly about refusing to claim calibration: a score on a
0-100 scale invites being read as a probability, and only this measurement
justifies that reading.
"""

from datetime import date

import pytest

from src.backtest.calibration import (
    CALIBRATED,
    UNINFORMATIVE,
    UNMEASURABLE,
    calibrate,
    format_calibration,
)
from src.backtest.engine import BacktestConfig, Outcome, Signal


@pytest.fixture
def config():
    return BacktestConfig.from_thresholds()


def outcomes_with(pairs, pattern="bull_trap", status="CANDIDATE", metric="score"):
    """(score, return) pairs as tradeable outcomes."""
    built = []
    for index, (score, value) in enumerate(pairs):
        signal = Signal("TEST", pattern, status, date(2026, 1, 5), metadata={metric: score})
        outcome = Outcome(signal=signal, entry_date=date(2026, 1, 6), entry_price=100.0)
        outcome.returns = {1: value, 3: value, 5: value, 10: value, 20: value}
        built.append(outcome)
    return built


def test_higher_scores_followed_by_better_outcomes_calibrate(config):
    pairs = [(30.0, -2.0)] * 25 + [(50.0, 0.0)] * 25 + [(70.0, 1.5)] * 25 + [(90.0, 3.0)] * 25
    result = calibrate(outcomes_with(pairs), "bull_trap:CANDIDATE", config=config)
    assert result.verdict == CALIBRATED
    assert [b.sample_size for b in result.buckets] == [25, 25, 25, 25]


def test_outcomes_that_do_not_order_with_the_score_are_uninformative(config):
    # 80+ does worse than 40-60: the number describes the setup, not the odds.
    pairs = [(30.0, 1.0)] * 25 + [(50.0, 4.0)] * 25 + [(70.0, -1.0)] * 25 + [(90.0, 2.0)] * 25
    result = calibrate(outcomes_with(pairs), "bull_trap:CANDIDATE", config=config)
    assert result.verdict == UNINFORMATIVE
    assert "does not order" in result.note or "do not order" in result.note


def test_thin_buckets_are_unmeasurable_not_calibrated(config):
    # Six occurrences per bucket cannot separate signal from noise, however
    # neatly they happen to line up.
    pairs = [(30.0, -2.0)] * 6 + [(90.0, 3.0)] * 6
    result = calibrate(outcomes_with(pairs), "bull_trap:CANDIDATE", config=config)
    assert result.verdict == UNMEASURABLE
    assert "cannot be told apart from noise" in result.note


def test_one_populated_bucket_is_not_a_calibration(config):
    result = calibrate(outcomes_with([(90.0, 3.0)] * 50), "bull_trap:CANDIDATE", config=config)
    assert result.verdict == UNMEASURABLE


def test_a_pattern_without_the_metric_says_so(config):
    signal = Signal("TEST", "breakout_20d", "CONFIRMED", date(2026, 1, 5), metadata={})
    outcome = Outcome(signal=signal, entry_date=date(2026, 1, 6), entry_price=100.0)
    outcome.returns = {5: 1.0}
    result = calibrate([outcome], "breakout_20d:CONFIRMED", config=config)
    assert result.verdict == UNMEASURABLE
    assert "score" in result.note


def test_untradeable_occurrences_are_excluded(config):
    outcomes = outcomes_with([(90.0, 3.0)] * 25 + [(30.0, -1.0)] * 25)
    orphan = Outcome(signal=Signal("TEST", "bull_trap", "CANDIDATE", date(2026, 1, 5),
                                   metadata={"score": 90.0}))
    result = calibrate(outcomes + [orphan], "bull_trap:CANDIDATE", config=config)
    assert sum(b.sample_size for b in result.buckets) == 50


def test_buckets_report_win_rate_and_both_averages(config):
    result = calibrate(outcomes_with([(90.0, 3.0)] * 20 + [(90.0, -1.0)] * 20),
                       "bull_trap:CANDIDATE", config=config)
    top = [b for b in result.buckets if b.sample_size][0]
    assert top.win_rate == pytest.approx(50.0)
    assert top.median_return is not None
    assert top.mean_return == pytest.approx(1.0)


def test_a_different_metric_can_be_calibrated(config):
    pairs = [(20.0, -1.0)] * 25 + [(80.0, 2.0)] * 25
    outcomes = outcomes_with(pairs, metric="strength")
    result = calibrate(outcomes, "bull_trap:CANDIDATE", metric="strength", config=config)
    assert result.metric == "strength"
    assert sum(b.sample_size for b in result.buckets) == 50


def test_the_rendered_table_refuses_the_probability_reading(config):
    text = format_calibration(
        calibrate(outcomes_with([(90.0, 3.0)] * 25 + [(30.0, -1.0)] * 25),
                  "bull_trap:CANDIDATE", config=config)
    )
    assert "not a probability" in text
    assert "VERDICT" in text


def test_an_inverted_score_is_labelled_as_inverted(config):
    """A score whose high bucket does worse is informative, backwards.

    Observed live on bull_trap: the higher the trap score, the more price rose
    afterwards. Calling that "calibrated" without saying which way invites
    reading the number as its name suggests.
    """
    pairs = [(50.0, 1.0)] * 25 + [(70.0, 0.0)] * 25 + [(90.0, -2.0)] * 25
    result = calibrate(outcomes_with(pairs), "bull_trap:CANDIDATE", config=config)
    assert result.verdict == CALIBRATED
    assert result.inverted is True
    assert "opposite direction" in result.note
    assert "INVERTED" in format_calibration(result)


def test_a_normally_ordered_score_is_not_marked_inverted(config):
    pairs = [(50.0, -2.0)] * 25 + [(70.0, 0.0)] * 25 + [(90.0, 2.0)] * 25
    result = calibrate(outcomes_with(pairs), "bull_trap:CANDIDATE", config=config)
    assert result.verdict == CALIBRATED
    assert result.inverted is False
    assert "rise with the score" in result.note
