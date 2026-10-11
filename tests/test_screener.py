import json
import sqlite3
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.database import connect, init_db
from src.quant.screener import (
    GROUP_A,
    GROUP_B,
    GROUP_C,
    latest_focus_list,
    liquidity,
    locked_worst_case_pct,
    round_to_tick,
    read_base,
    rs_ratings,
    rs_raw_score,
    run_screen,
    screen_settings,
    tick_size,
    trend_template,
    zigzag,
)

SETTINGS = screen_settings()
END = date(2026, 9, 25)


def frame_from_closes(closes, volume=2_000_000, spread=0.01):
    idx = pd.bdate_range(end=pd.Timestamp(END), periods=len(closes))
    c = np.asarray(closes, dtype="float64")
    vol = np.full(len(c), float(volume)) if np.isscalar(volume) else np.asarray(volume, dtype="float64")
    return pd.DataFrame({"open": c, "high": c * (1 + spread), "low": c * (1 - spread),
                         "close": c, "volume": vol}, index=idx)


def stage2_closes(n=320):
    """A steady advance: 20,000 -> ~40,000 VND."""
    return list(np.linspace(20_000, 40_000, n))


# -- small pieces --------------------------------------------------------------

def test_tick_sizes_follow_the_exchange_tables():
    assert tick_size(9_990, "HOSE") == 10
    assert tick_size(24_350, "HOSE") == 50
    assert tick_size(50_000, "HOSE") == 100
    assert tick_size(9_990, "HNX") == 100


def test_locked_worst_case_matches_the_skill_table():
    assert locked_worst_case_pct(7) == pytest.approx(19.56, abs=0.01)
    assert locked_worst_case_pct(10) == pytest.approx(27.1, abs=0.01)
    assert locked_worst_case_pct(15) == pytest.approx(38.59, abs=0.01)
    assert locked_worst_case_pct(7, from_ceiling=True) == pytest.approx(24.83, abs=0.01)


def test_rs_needs_a_full_year():
    assert rs_raw_score(pd.Series(range(1, 200), dtype="float64"), "A", [63, 126, 189, 252]) is None


def test_rs_variants_rank_a_leader_above_a_laggard():
    up = pd.Series(np.linspace(10, 30, 300))
    flat = pd.Series(np.full(300, 10.0))
    for variant in ("A", "B"):
        ratings = rs_ratings({"UP": rs_raw_score(up, variant, [63, 126, 189, 252]),
                              "FLAT": rs_raw_score(flat, variant, [63, 126, 189, 252])})
        assert ratings["UP"] > ratings["FLAT"]
        assert all(1 <= v <= 99 for v in ratings.values())


def test_liquidity_is_close_times_volume():
    value, active = liquidity(frame_from_closes([10_000] * 60, volume=1_000_000), SETTINGS)
    assert value == pytest.approx(1.0e10)
    assert active == 1.0


# -- Trend Template -------------------------------------------------------------

def test_steady_advance_passes_all_eight():
    tt = trend_template(frame_from_closes(stage2_closes()), rs_rating=90, settings=SETTINGS)
    assert all(c.passed for c in tt.values()), {k: c.as_dict() for k, c in tt.items() if not c.passed}


def test_each_criterion_can_fail_on_its_own():
    f = frame_from_closes(stage2_closes())
    assert not trend_template(f, rs_rating=50, settings=SETTINGS)["TT8"].passed
    declining = frame_from_closes(list(np.linspace(40_000, 20_000, 320)))
    tt = trend_template(declining, rs_rating=90, settings=SETTINGS)
    assert not tt["TT1"].passed and not tt["TT3"].passed and not tt["TT7"].passed


def test_criteria_report_values_not_just_booleans():
    tt = trend_template(frame_from_closes(stage2_closes()), rs_rating=88, settings=SETTINGS)
    d = tt["TT6"].as_dict()
    assert d["value"] > 30 and "threshold" in d and d["text"]


# -- bases ------------------------------------------------------------------------

def vcp_closes():
    """Advance, then three contractions of 20%, 10%, 4%, volume drying at the end."""
    up = list(np.linspace(20_000, 40_000, 200))
    legs = []
    peak = 40_000.0
    for depth in (0.20, 0.10, 0.04):
        trough = peak * (1 - depth)
        legs += list(np.linspace(peak, trough, 12)[1:])
        new_peak = peak * 0.995
        legs += list(np.linspace(trough, new_peak, 12)[1:])
        peak = new_peak
    return up + legs


def test_zigzag_finds_alternating_swings():
    points = zigzag(frame_from_closes(vcp_closes()), 3.0)
    kinds = [k for k, _, _ in points]
    assert all(a != b for a, b in zip(kinds, kinds[1:]))


def test_vcp_is_recognised_with_shrinking_legs_and_pivot_above_price():
    closes = vcp_closes()
    vol = [2_000_000] * (len(closes) - 5) + [600_000] * 5
    base = read_base(frame_from_closes(closes, volume=vol, spread=0.002), "HOSE", SETTINGS)
    assert base.kind == "VCP", base.as_dict()
    assert base.contractions == sorted(base.contractions, reverse=True)
    assert base.volume_dryup is True
    assert base.pivot > closes[-1] * 0.99
    assert base.stop < base.pivot


def test_extended_stock_is_not_a_base():
    closes = stage2_closes() + list(np.linspace(40_000, 46_000, 10))
    assert read_base(frame_from_closes(closes, spread=0.002), "HOSE", SETTINGS).kind in {"EXTENDED", "NEAR_HIGH"}


# -- whole run ---------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    path = tmp_path / "screen.db"
    init_db(path)
    with connect(path) as conn:
        conn.execute("INSERT INTO sources (source_id, kind, source_level, enabled) VALUES ('dnse_openapi','market_data',2,1)")
    return path


def _store(path, ticker, exchange, frame):
    with connect(path) as conn:
        conn.execute("INSERT INTO tickers (ticker, exchange, created_at, updated_at) VALUES (?,?,'x','x')",
                     (ticker, exchange))
        conn.executemany(
            "INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source_id, observed_at) "
            "VALUES (?,?,?,?,?,?,?,'dnse_openapi','x')",
            [(ticker, d.date().isoformat(), r.open, r.high, r.low, r.close, int(r.volume)) for d, r in frame.iterrows()],
        )


def _populate(path):
    rng = np.random.default_rng(7)
    # 40 leaders at different strengths, 40 laggards, one illiquid leader, one short history
    for i in range(40):
        _store(path, f"L{i:02d}", "HOSE", frame_from_closes(list(np.linspace(20_000, 30_000 + 400 * i, 320))))
    for i in range(40):
        noise = rng.normal(0, 150, 320).cumsum()
        _store(path, f"W{i:02d}", "HNX", frame_from_closes(list(np.linspace(30_000, 22_000, 320) + noise)))
    _store(path, "ILQ", "UPCOM", frame_from_closes(stage2_closes(), volume=1_000))
    _store(path, "NEW", "HOSE", frame_from_closes(stage2_closes(100)))


def test_screen_caps_the_list_and_puts_group_a_first(db):
    _populate(db)
    result = run_screen(as_of=END, path=db)
    assert len(result.members) <= SETTINGS["max_members"]
    groups = [m.group for m in result.members]
    assert groups == sorted(groups)                      # all A before any B
    assert all(m.passed == 8 for m in result.members if m.group == GROUP_A)
    assert all(m.group in (GROUP_A, GROUP_B, GROUP_C) for m in result.members)


def test_illiquid_names_never_reach_the_list(db):
    _populate(db)
    result = run_screen(as_of=END, path=db)
    assert "ILQ" not in {m.ticker for m in result.members}
    assert result.exclusions.get("thanh khoản dưới ngưỡng", 0) >= 1


def test_a_liquid_recent_listing_joins_group_c_with_unassessable_criteria(db):
    _populate(db)
    result = run_screen(as_of=END, path=db)
    new = next(m for m in result.members if m.ticker == "NEW")          # 100 sessions of history
    assert new.group == GROUP_C and new.rs_rating is None
    d = {k: c.as_dict() for k, c in new.criteria.items()}
    assert d["TT1"]["pass"] is None and "cần" in d["TT1"]["threshold"]  # not a fail: not measurable yet
    assert d["TT7"]["pass"] is True
    assert result.funnel["recent_listings"] == 1


def test_stale_series_is_excluded(db):
    _populate(db)
    with connect(db) as conn:
        conn.execute("INSERT INTO data_freshness (dataset, scope_key, ttl_hours, is_stale) "
                     "VALUES ('prices_daily', 'L39', 24, 1)")
    result = run_screen(as_of=END, path=db)
    assert "L39" not in {m.ticker for m in result.members}
    assert "L39" in {t for t, _, _ in result.flagged_liquid}     # surfaced, not silently hidden


def test_a_reviewed_gap_waiver_lets_the_series_back_in(db):
    import copy
    from src.config import load_config
    _populate(db)
    with connect(db) as conn:
        conn.execute("INSERT INTO data_freshness (dataset, scope_key, ttl_hours, is_stale) "
                     "VALUES ('prices_daily', 'L39', 24, 1)")
    raw = copy.deepcopy(load_config().thresholds.raw)
    raw["sepa_screen"]["gap_waivers"] = {"L39": "chuyển sàn, đã kiểm tra"}
    result = run_screen(as_of=END, path=db, thresholds=raw, persist=False)
    assert "L39" in {m.ticker for m in result.members}


def test_runs_are_append_only_and_latest_list_is_readable(db):
    _populate(db)
    first = run_screen(as_of=END, path=db)
    second = run_screen(as_of=END, path=db)
    assert first.run_id != second.run_id
    assert latest_focus_list(db) == [m.ticker for m in second.members]
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM screen_runs").fetchone()[0] == 2
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute("UPDATE screen_members SET score = 0 WHERE run_id = ?", (first.run_id,))


def test_members_store_every_criterion_value(db):
    _populate(db)
    result = run_screen(as_of=END, path=db)
    with connect(db) as conn:
        row = conn.execute("SELECT criteria_json, locked_worst_case_pct FROM screen_members "
                           "WHERE run_id = ? AND rank = 1", (result.run_id,)).fetchone()
    criteria = json.loads(row[0])
    assert set(f"TT{i}" for i in range(1, 9)) <= set(criteria)
    assert row[1] == pytest.approx(19.56, abs=0.01)      # HOSE leader


def test_list_is_filled_to_its_size_with_a_labelled_group_c(db):
    import copy
    from src.config import load_config
    _populate(db)
    raw = copy.deepcopy(load_config().thresholds.raw)
    raw["sepa_screen"]["max_members"] = 45
    raw["sepa_screen"]["group_c"] = {"min_rs_rating": 1, "min_criteria_passed": 0, "exclude_bases": ["BROKEN"]}
    result = run_screen(as_of=END, path=db, thresholds=raw, persist=False)
    assert len(result.members) == 45
    c_members = [m for m in result.members if m.group == GROUP_C]
    assert c_members and all(m.passed < 8 for m in c_members)          # C is never SEPA-qualified
    groups = [m.group for m in result.members]
    assert groups == sorted(groups)                                    # A, then B, then C
    assert result.funnel["fill_group_c"] + result.funnel["recent_listings"] == len(c_members)


def test_group_c_floors_keep_non_candidates_out(db):
    import copy
    from src.config import load_config
    _populate(db)
    raw = copy.deepcopy(load_config().thresholds.raw)
    raw["sepa_screen"]["group_c"] = {"min_rs_rating": 99, "min_criteria_passed": 8, "exclude_bases": []}
    raw["sepa_screen"].pop("recent_listings")
    result = run_screen(as_of=END, path=db, thresholds=raw, persist=False)
    assert not [m for m in result.members if m.group == GROUP_C]     # shorter list, not padded


# -- cup rules (tightened 2026-09-27) ------------------------------------------------

def _cup(handle=True, left_sessions=25, prior_sessions=60):
    """Uptrend, a rounded 25% cup, then optionally a quiet 6% handle."""
    up = list(np.linspace(20_000, 40_000, prior_sessions))
    left = list(np.linspace(40_000, 30_000, left_sessions))[1:]
    bottom = [30_000 + 300 * np.sin(i / 3) for i in range(20)]
    right = list(np.linspace(30_000, 39_500, 25))
    closes = [20_000] * 200 + up + left + bottom + right
    vols = [1_000_000] * len(closes)
    if handle:
        h = list(np.linspace(39_300, 37_200, 5)) + list(np.linspace(37_300, 38_600, 4))
        closes += h
        vols += [500_000] * len(h)
    return frame_from_closes(closes, volume=vols, spread=0.003)


def test_textbook_cup_with_handle_is_recognised_with_handle_pivot():
    f = _cup(handle=True)
    base = read_base(f, "HOSE", SETTINGS)
    assert base.kind == "CUP_HANDLE", [c for c in base.cup_checks if not c["ok"]]
    # right lip + one HOSE tick, then up to the tick grid (an adjusted lip can sit between ticks)
    assert base.right_lip and base.pivot == round_to_tick(base.right_lip + 50, "HOSE", up=True)
    assert base.pivot % 50 == 0 and base.stop % 50 == 0
    assert base.handle_low and base.stop >= round_to_tick(base.handle_low, "HOSE", up=False) - 1e-6


def test_cup_without_a_handle_uses_the_left_side_high():
    f = _cup(handle=False)
    base = read_base(f, "HOSE", SETTINGS)
    assert base.kind == "CUP", [c for c in base.cup_checks if not c["ok"]]
    assert base.pivot == round_to_tick(base.top + 50, "HOSE", up=True)


def test_prices_are_rounded_to_the_exchange_tick():
    assert round_to_tick(23_130, "HOSE", up=True) == 23_150      # HDB after its 30% stock dividend
    assert round_to_tick(21_279.6, "HOSE", up=False) == 21_250
    assert round_to_tick(9_996, "HOSE", up=True) == 10_000 and round_to_tick(134_504, "HNX", up=False) == 134_500
    assert round_to_tick(40_250, "HOSE", up=True) == 40_250      # already on the grid: unchanged


def test_spike_then_v_crash_is_not_a_cup():
    # TLG, 2025-12: a one-week spike to the high, then a crash to the low in ~13 sessions.
    spike = [30_000] * 250 + list(np.linspace(30_000, 42_000, 6))
    crash = list(np.linspace(42_000, 32_500, 13))[1:]
    flat = [33_000 + 600 * np.sin(i / 4) for i in range(120)]
    rebound = list(np.linspace(33_500, 38_500, 20))
    from src.quant.screener import evaluate_cup
    f = frame_from_closes(spike + crash + flat + rebound, spread=0.003)
    top_index = int(np.argmax(f["high"].to_numpy()))
    cup = evaluate_cup(f, top_index, f.iloc[top_index:], SETTINGS)
    failed = {c["rule"] for c in cup.checks if not c["ok"]}
    assert not cup.ok and {"left_side", "prior_uptrend"} <= failed
    assert read_base(f, "HOSE", SETTINGS).kind not in {"CUP", "CUP_HANDLE"}


def test_group_c_holds_only_names_above_their_moving_averages(db):
    _populate(db)
    result = run_screen(as_of=END, path=db)
    c = [m for m in result.members if m.group == GROUP_C and m.rs_rating is not None]
    assert all(m.criteria["TT1"].passed and m.criteria["TT5"].passed for m in c)
    assert not any(m.ticker.startswith("W") for m in result.members)     # the downtrends stay out
    assert len(result.members) <= SETTINGS["max_members"]                 # not padded with them either


def test_a_freshly_broken_chart_leaves_the_main_list(db):
    _populate(db)
    # A slow advance, a steep run-up to a top 9 sessions ago, then a 12% drop:
    # the Trend Template still passes but the chart has just broken.
    closes = list(np.linspace(15_000, 25_000, 270)) + list(np.linspace(25_000, 40_000, 41))
    closes += list(np.linspace(40_000, 35_200, 9))
    _store(db, "BRK", "HOSE", frame_from_closes(closes))
    result = run_screen(as_of=END, path=db, persist=False)
    brk = next((m for m in result.members if m.ticker == "BRK"), None)
    assert brk is None or brk.group not in (GROUP_A, GROUP_B)
    assert any("gãy chart" in k and "BRK" in k for k in result.exclusions)


# -- base rules 2 (tester report 2, 2026-10-11) --------------------------------------

from src.quant.screener import BASE_RULES, explain_base, major_contractions  # noqa: E402


def ohlc(rows, volume=2_000_000):
    """Bars from explicit (open, high, low, close) rows, ending on END."""
    idx = pd.bdate_range(end=pd.Timestamp(END), periods=len(rows))
    o, h, l, c = (np.asarray(x, dtype="float64") for x in zip(*rows))
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c,
                         "volume": np.full(len(rows), float(volume))}, index=idx)


def bars_of(closes, spread=0.004):
    return [(c, c * (1 + spread), c * (1 - spread), c) for c in closes]


def test_major_contractions_are_the_big_pullbacks_not_every_wiggle():
    """KSV had 37 zigzag "contractions" in one base (M11): count the major ones only."""
    f = frame_from_closes(vcp_closes(), spread=0.002)
    base = f.iloc[199:]
    legs = major_contractions(base["high"].to_numpy(), base["low"].to_numpy(), 3.0)
    assert [round(d) for d, _, _ in legs] == [20, 10, 4]
    noisy = frame_from_closes([40_000 * (1 - 0.15 * min(i, 30) / 30 + 0.04 * np.sin(i / 2)) for i in range(250)])
    assert len(zigzag(noisy, 3.0)) > 12                       # the old count
    legs = major_contractions(noisy["high"].to_numpy(), noisy["low"].to_numpy(), 3.0)
    assert 1 <= len(legs) <= 6
    assert all(b <= a for (a, _, _), (b, _, _) in zip(legs, legs[1:]))


def test_a_two_session_shakeout_counts_as_a_contraction():
    highs = np.array([100.0, 99.0, 90.0, 95.0, 94.0, 93.0])
    lows = np.array([98.0, 91.0, 88.0, 93.0, 92.5, 92.8])
    assert [(h, lo) for _, h, lo in major_contractions(highs, lows, 3.0)] == [(0, 2)]
    assert major_contractions(highs, lows, 3.0, min_leg_sessions=4) == []


def _vcp_then(tail):
    """Advance, a 20% then a ~8% contraction, then `tail` (closes)."""
    closes = list(np.linspace(20_000, 40_000, 200)) + list(np.linspace(40_000, 32_000, 15)) \
        + list(np.linspace(32_000, 39_000, 15)) + list(np.linspace(39_000, 36_000, 8)) + tail
    return frame_from_closes(closes, spread=0.002)


TIGHT_TAIL = list(np.linspace(36_000, 38_600, 6)) + [38_400, 38_300, 38_500, 38_350, 38_450]


def test_a_tight_area_in_the_upper_half_is_the_pivot_and_its_low_the_stop():
    f = _vcp_then(TIGHT_TAIL)
    base = read_base(f, "HOSE", SETTINGS)
    assert base.legs[-1]["tight"] and base.kind == "VCP", base.as_dict()
    top = base.legs[-1]["high"]
    assert base.pivot == round_to_tick(top + tick_size(top, "HOSE"), "HOSE", up=True)
    assert base.stop == max(round_to_tick(base.legs[-1]["low"], "HOSE", up=False),
                            round_to_tick(base.pivot * 0.92, "HOSE", up=True))


def test_a_pause_in_the_lower_half_is_not_a_pivot():
    """HCM's 23,750 sat 13% under its left high: a rebound inside the base, not its right side (M04)."""
    closes = list(np.linspace(20_000, 40_000, 200)) + list(np.linspace(40_000, 30_000, 20)) \
        + list(np.linspace(30_000, 32_500, 6)) + [32_300, 32_200, 32_400, 32_250, 32_350]
    base = read_base(frame_from_closes(closes, spread=0.002), "HOSE", SETTINGS)
    assert not any(leg["tight"] for leg in base.legs)
    assert base.pivot > 40_000                                 # the last contraction's high: the top


def _abb_like():
    """ABB 2026: an opening spike at the ceiling (09/07), a left high, a flat base, a right high."""
    rows = bars_of(np.linspace(12_000, 16_500, 220), spread=0.01)
    rows += [(18_900, 18_900, 16_500, 17_200),                 # opened at the ceiling, closed 9% off it
             (17_200, 18_000, 17_100, 17_600)]                 # the left-side high
    rows += bars_of([17_000, 16_900, 16_700, 17_000, 17_300] * 8, spread=0.006)
    rows += [(17_400, 18_200, 17_300, 17_900)]                 # the right-side high
    rows += bars_of([17_700, 17_500, 17_300, 17_600, 18_000, 17_800, 17_600, 17_500, 17_600, 17_500], spread=0.004)
    return ohlc(rows)


def test_an_opening_spike_is_not_the_base_top_and_the_base_starts_at_the_left_high():
    f = _abb_like()
    base = read_base(f, "UPCOM", SETTINGS)
    spike_day, left_day = f.index[220].date().isoformat(), f.index[221].date().isoformat()
    assert base.spike_dates == [spike_day] and base.start_date == left_day
    assert base.top == 18_200 and base.kind == "FLAT_BASE", base.as_dict()
    assert base.pivot == 18_300                                # IBD flat base: highest high + one tick
    assert base.rules == BASE_RULES


def test_a_left_high_before_a_deep_fall_does_not_start_the_base():
    """An 18,000 high a year ago, a 40% fall and a climb to 18,200 are two bases, not one."""
    closes = list(np.linspace(14_000, 18_000, 60)) + list(np.linspace(18_000, 10_800, 80)) \
        + list(np.linspace(10_800, 18_200, 100)) + [17_600, 17_300, 17_500, 17_800, 17_400] * 4
    base = read_base(frame_from_closes(closes, spread=0.004), "HOSE", SETTINGS)
    assert base.start_date == base.top_date


def test_a_climax_top_with_neighbouring_highs_is_kept():
    rows = bars_of(np.linspace(20_000, 29_000, 250), spread=0.01)
    rows += [(29_730, 29_730, 26_000, 26_100)]                 # opens at its high, closes at its low ...
    rows += bars_of([28_800, 28_000, 27_000, 26_500] + [26_000] * 25, spread=0.01)   # ... 29,088 is 2% away
    base = read_base(ohlc(rows), "HOSE", SETTINGS)
    assert base.spike_dates == [] and base.top == 29_730


@pytest.mark.parametrize("exchange", ["HOSE", "HNX", "UPCOM"])
@pytest.mark.parametrize("scale", [0.31, 0.5, 0.77, 1.0, 1.24, 1.3555, 2.17, 5.3])
def test_the_risk_from_the_pivot_never_exceeds_8_percent(exchange, scale):
    """Stop = pivot x 0.92 rounded DOWN made ABB 8.4% (M03); it is rounded up now."""
    closes = [c * scale for c in stage2_closes(200)]
    closes += [closes[-1] * (1 - 0.14 * min(i, 10) / 10) for i in range(30)]     # a 14%-deep flat base
    base = read_base(frame_from_closes(closes, spread=0.002), exchange, SETTINGS)
    assert base.forming and base.stop, base.as_dict()
    assert (base.pivot - base.stop) / base.pivot <= 0.08 + 1e-12
    assert base.stop == round_to_tick(base.stop, exchange, up=False)            # on the tick grid


def test_the_8_percent_floor_at_the_hose_tick_boundary():
    """Pivot 54,300 x 0.92 = 49,956: the 50-dong tick below 50,000 rounds it up to 50,000."""
    assert round_to_tick(54_300 * 0.92, "HOSE", up=True) == 50_000
    assert (54_300 - 50_000) / 54_300 <= 0.08


def test_no_forming_base_no_stop():
    extended = frame_from_closes(stage2_closes() + list(np.linspace(40_000, 46_000, 10)), spread=0.002)
    assert read_base(extended, "HOSE", SETTINGS).stop is None
    deep = list(np.linspace(20_000, 40_000, 200)) + list(np.linspace(40_000, 22_000, 60)) + [23_000] * 20
    read = read_base(frame_from_closes(deep, spread=0.002), "HOSE", SETTINGS)
    assert read.kind == "TOO_DEEP" and read.stop is None


def test_explain_base_dates_the_stop_breach_and_checks_each_label_by_its_own_rules():
    f = _abb_like()
    out = explain_base(f, "UPCOM", SETTINGS)
    assert out["kind"] == "FLAT_BASE"
    assert not any("Tay cầm" in c["text"] or "cốc" in c["text"] for c in out["checks"])   # M09
    assert out["swings"] and out["rules"] == BASE_RULES
    broke = ohlc(list(zip(f["open"], f["high"], f["low"], f["close"])) + [(16_900, 16_900, 16_500, 16_600)])
    out = explain_base(broke, "UPCOM", SETTINGS)
    assert out["stop_breached"] and out["stop_breached_on"] == broke.index[-1].date().isoformat()


def test_supply_overhead_is_information_then_a_warning():
    """GMD's left high sat 9% above its pivot (M04): normal up to 5%, information to 10%, then a warning."""
    assert "overhead" not in explain_base(_vcp_then(TIGHT_TAIL), "HOSE", SETTINGS)          # 3.6%
    info = explain_base(_vcp_then(list(np.linspace(36_000, 37_400, 6)) + [37_200, 37_100, 37_300, 37_150]),
                        "HOSE", SETTINGS)["overhead"]
    assert info["level"] == "info" and 5 <= info["pct"] < 10
    closes = list(np.linspace(20_000, 40_000, 200)) + list(np.linspace(40_000, 32_000, 15)) \
        + list(np.linspace(32_000, 37_500, 15)) + list(np.linspace(37_500, 34_500, 8)) \
        + list(np.linspace(34_500, 36_200, 5)) + [36_100, 36_050, 36_150, 36_100]
    warn = explain_base(frame_from_closes(closes, spread=0.002), "HOSE", SETTINGS)["overhead"]
    assert warn["level"] == "warn" and warn["pct"] >= 10


def test_swings_between_the_same_two_ticks_are_one_contraction():
    """VHG (1,600 dong, 100-dong tick) read 18 contractions of 12% and 6%, all the same range."""
    highs = np.array([2_000.0, 1_700, 1_400, 1_600, 1_400, 1_600, 1_400, 1_600, 1_500, 1_600, 1_500, 1_550])
    lows = np.array([1_900.0, 1_500, 1_300, 1_400, 1_400, 1_400, 1_400, 1_400, 1_400, 1_500, 1_500, 1_500])
    legs = major_contractions(highs, lows, 3.0)
    assert [round(d) for d, _, _ in legs] == [35, 6]          # 2,000 -> 1,300, then 1,600 (its last time) -> 1,500
    assert all(highs[b] < highs[a] for (_, a, _), (_, b, _) in zip(legs, legs[1:]))
