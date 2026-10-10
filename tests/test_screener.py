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
