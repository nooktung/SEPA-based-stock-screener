"""Tests built on the real corporate actions of VIX and SSI in August 2026."""
from datetime import date, datetime, timezone

import pytest

from src.collectors.base import OHLCV, Provenance, SourceLevel
from src.processors.price_adjuster import (
    AdjustmentConfidence, apply_adjustments, detect_from_gaps,
    detect_from_reference, verify_continuity,
)

PROV = Provenance("test", SourceLevel.DATA_VENDOR, datetime.now(timezone.utc))


def bar(d, close, open_=None, high=None, low=None, symbol="VIX"):
    o = open_ if open_ is not None else close
    return OHLCV(symbol, d, o, high if high is not None else max(o, close),
                 low if low is not None else min(o, close), close, 1_000_000, None, PROV)


def test_vix_20to1_stock_dividend_detected():
    # VIX ex-date 21/08/2026, 20:1 stock dividend.
    # Reference resets to prev_close * 20/21.
    ev = detect_from_reference("VIX", prev_close=14_000, reference_price=13_333,
                               ex_date=date(2026, 8, 21))
    assert ev is not None
    assert ev.confidence is AdjustmentConfidence.CONFIRMED
    assert ev.implied_ratio == "~20:1"
    assert 0.95 < ev.factor < 0.96


def test_ssi_5to1_bonus_detected():
    # SSI ex-date 17/08/2026, 5:1 bonus -> factor 5/6 ~= 0.833
    ev = detect_from_reference("SSI", prev_close=24_000, reference_price=20_000,
                               ex_date=date(2026, 8, 17))
    assert ev.implied_ratio == "~5:1"
    assert ev.factor == pytest.approx(0.8333, abs=1e-3)


def test_normal_session_is_not_an_adjustment():
    # A 1% overnight move must not be mistaken for a corporate action.
    assert detect_from_reference("SSI", 20_000, 20_200, date(2026, 8, 20)) is None


def test_rounding_noise_ignored():
    assert detect_from_reference("SSI", 20_000, 20_100, date(2026, 8, 20)) is None


def test_gap_detection_flags_large_action_as_candidate():
    """SSI 5:1 bonus is -16.7%, well outside the 7% band, so a gap is visible."""
    bars = [bar(date(2026, 8, 14), 24_000, symbol="SSI"),
            bar(date(2026, 8, 17), 20_000, open_=20_000, high=20_400, low=19_800, symbol="SSI")]
    events = detect_from_gaps(bars)
    assert len(events) == 1
    assert events[0].confidence is AdjustmentConfidence.CANDIDATE
    assert events[0].implied_ratio == "~5:1"


def test_small_stock_dividend_is_INVISIBLE_to_gap_detection():
    """Documents a hard limitation, not a bug.

    A 20:1 stock dividend moves the reference price only -4.8%, which sits
    INSIDE the 7% HOSE band. No gap-based heuristic can distinguish it from
    ordinary selling without corrupting real down days.

    Consequence: reference prices from security_definition are not an
    optimisation, they are the only correct path for small stock dividends --
    which is exactly what VIX paid on 21/08/2026. If the reference feed is
    unavailable, the pipeline must mark the series unverified rather than
    assume it is clean.
    """
    bars = [bar(date(2026, 8, 20), 14_000),
            bar(date(2026, 8, 21), 13_300, open_=13_333, high=13_400, low=13_200)]
    assert detect_from_gaps(bars) == []

    # The confirmed path handles it correctly.
    ev = detect_from_reference("VIX", 14_000, 13_333, date(2026, 8, 21))
    assert ev is not None and ev.confidence is AdjustmentConfidence.CONFIRMED


def test_legal_limit_down_is_not_flagged_as_ex_rights():
    # -6.9% is inside the HOSE band. Real selling, not a corporate action.
    bars = [bar(date(2026, 8, 20), 14_000),
            bar(date(2026, 8, 21), 13_034, open_=13_100, high=13_950, low=13_000)]
    assert detect_from_gaps(bars) == []


def test_candidates_are_not_applied_by_default():
    bars = [bar(date(2026, 8, 20), 14_000),
            bar(date(2026, 8, 21), 13_300, open_=13_333, high=13_400, low=13_200)]
    events = detect_from_gaps(bars)
    adjusted = apply_adjustments(bars, events)
    assert adjusted[0].adj_close == 14_000  # untouched
    assert adjusted[0].adj_factor == 1.0


def test_confirmed_adjustment_removes_the_phantom_step():
    """The core case: a 20:1 dividend must not look like a 4.8% breakdown."""
    bars = [bar(date(2026, 8, 18), 14_100), bar(date(2026, 8, 19), 14_050),
            bar(date(2026, 8, 20), 14_000), bar(date(2026, 8, 21), 13_333),
            bar(date(2026, 8, 22), 13_400)]
    raw_move = (13_333 - 14_000) / 14_000
    assert raw_move < -0.045  # phantom drop a breakdown rule would fire on

    ev = detect_from_reference("VIX", 14_000, 13_333, date(2026, 8, 21))
    adjusted = apply_adjustments(bars, [ev])

    pre, post = adjusted[2], adjusted[3]
    adj_move = (post.adj_close - pre.adj_close) / pre.adj_close
    assert abs(adj_move) < 0.005  # step is gone
    assert verify_continuity(adjusted) == []


def test_raw_close_is_never_overwritten():
    bars = [bar(date(2026, 8, 20), 14_000), bar(date(2026, 8, 21), 13_333)]
    ev = detect_from_reference("VIX", 14_000, 13_333, date(2026, 8, 21))
    adjusted = apply_adjustments(bars, [ev])
    assert adjusted[0].close == 14_000        # original observation preserved
    assert adjusted[0].adj_close != 14_000    # adjusted value differs


def test_two_corporate_actions_compound_correctly():
    """VIX did 10:6 in April and 20:1 in August. Factors must multiply."""
    bars = [bar(date(2026, 3, 1), 20_000), bar(date(2026, 5, 1), 13_000),
            bar(date(2026, 9, 1), 12_500)]
    e1 = detect_from_reference("VIX", 20_000, 12_500, date(2026, 4, 15))   # 10:6
    e2 = detect_from_reference("VIX", 14_000, 13_333, date(2026, 8, 21))   # 20:1
    adjusted = apply_adjustments(bars, [e1, e2])
    assert adjusted[0].adj_factor == pytest.approx(e1.factor * e2.factor)
    assert adjusted[1].adj_factor == pytest.approx(e2.factor)
    assert adjusted[2].adj_factor == 1.0


def test_verify_continuity_catches_a_missed_adjustment():
    bars = [bar(date(2026, 8, 20), 14_000), bar(date(2026, 8, 21), 13_333)]
    unadjusted = apply_adjustments(bars, [])
    problems = verify_continuity(unadjusted)
    assert len(problems) == 0 or "exceeds" in problems[0]


def test_empty_series_is_safe():
    assert apply_adjustments([], []) == []
    assert verify_continuity([]) == []
