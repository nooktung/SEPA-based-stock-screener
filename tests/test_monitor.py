from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.database import connect, init_db
from src.quant.monitor import (
    BREAKOUT,
    BREAKOUT_FAILED,
    BREAKOUT_WEAK,
    LARGEST_DOWN_DAY,
    TREND_TEMPLATE_BROKEN,
    compute_state,
    detect_events,
    monitor_settings,
    render_brief,
    run_monitor,
)
from src.quant.screener import screen_settings

SCREEN = screen_settings()
MON = monitor_settings()
END = date(2026, 9, 25)


def frame(closes, volumes=None, spread=0.004):
    idx = pd.bdate_range(end=pd.Timestamp(END), periods=len(closes))
    c = np.asarray(closes, dtype="float64")
    v = np.full(len(c), 1_000_000.0) if volumes is None else np.asarray(volumes, dtype="float64")
    return pd.DataFrame({"open": c, "high": c * (1 + spread), "low": c * (1 - spread),
                         "close": c, "volume": v}, index=idx)


def flat_base_closes():
    """Stage 2 advance, then 35 sessions sideways in a ~6% range under 40,000."""
    up = list(np.linspace(20_000, 40_000, 260))
    side = [38_000 + 1_000 * np.sin(i / 3) for i in range(35)]
    return up + side


def events_for(closes, volumes=None, exchange="HOSE"):
    f = frame(closes, volumes)
    today = compute_state("AAA", exchange, f, None, 90.0, SCREEN, MON)
    before = compute_state("AAA", exchange, f.iloc[:-1], None, 90.0, SCREEN, MON)
    return {e.event_type: e for e in detect_events("AAA", exchange, f, today, before, SCREEN, MON)}, today, before


def test_flat_base_pivot_is_the_highest_price_in_the_base():
    from src.quant.screener import read_base
    b = read_base(frame(flat_base_closes()), "HOSE", SCREEN)
    assert b.kind == "FLAT_BASE"
    assert b.pivot == 40_250                       # top high 40,160 + one HOSE tick, on the 50-dong grid


def test_breakout_through_yesterdays_pivot_with_volume():
    closes = flat_base_closes() + [41_000]
    volumes = [1_000_000] * (len(closes) - 1) + [2_000_000]
    events, today, before = events_for(closes, volumes)
    assert before.pivot and before.pivot < 41_000
    assert BREAKOUT in events and BREAKOUT_WEAK not in events


def test_a_new_high_weeks_after_the_last_is_not_called_a_base_breakout():
    from src.quant.monitor import NEW_HIGH_NO_BASE
    closes = list(np.linspace(20_000, 40_000, 290)) + [39_000] * 5 + [40_500]
    volumes = [1_000_000] * (len(closes) - 1) + [2_000_000]
    events, _, before = events_for(closes, volumes)
    assert before.base_kind == "NEAR_HIGH"
    assert NEW_HIGH_NO_BASE in events and BREAKOUT not in events


def test_breakout_without_volume_is_labelled_as_such():
    # UPCoM: +10% is inside its 15% band, so this is not a limit-up session.
    events, _, _ = events_for(flat_base_closes() + [41_000], exchange="UPCOM")
    assert BREAKOUT_WEAK in events and BREAKOUT not in events


def test_a_limit_up_breakout_notes_that_volume_may_be_understated():
    base = flat_base_closes() + [38_800]          # right side near the top of the base
    ceiling_close = base[-1] * 1.07               # limit-up through the ~40,210 pivot
    events, today, _ = events_for(base + [ceiling_close], exchange="HOSE")
    assert today.at_ceiling
    e = events.get(BREAKOUT) or events.get(BREAKOUT_WEAK)
    assert e is not None and "giá trần" in e.why_it_matters


def test_breakout_that_closes_back_under_its_pivot_is_flagged():
    closes = flat_base_closes()
    volumes = [1_000_000] * len(closes) + [2_000_000, 1_000_000]
    events, _, _ = events_for(closes + [41_000, 38_500], volumes)
    assert BREAKOUT_FAILED in events


def test_losing_a_trend_template_criterion_is_reported():
    closes = list(np.linspace(20_000, 40_000, 300))
    events, today, before = events_for(closes + [36_000])      # -10%: below MA50
    assert "TT5" in before.price_criteria_passed and "TT5" not in today.price_criteria_passed
    assert TREND_TEMPLATE_BROKEN in events
    assert "TT5" in events[TREND_TEMPLATE_BROKEN].what_changed


def test_largest_down_day_in_the_lookback():
    closes = list(np.linspace(20_000, 40_000, 300))
    events, _, _ = events_for(closes + [37_600])
    assert LARGEST_DOWN_DAY in events


def test_quiet_day_produces_no_events():
    closes = list(np.linspace(20_000, 40_000, 300))
    events, _, _ = events_for(closes + [closes[-1] * 1.001])
    assert not events


def test_state_uses_only_price_criteria_for_tt():
    _, today, _ = events_for(list(np.linspace(20_000, 40_000, 300)))
    assert set(today.price_criteria_passed) <= {"TT1", "TT2", "TT3", "TT4", "TT5", "TT6", "TT7"}


# -- run and persistence -------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    path = tmp_path / "mon.db"
    init_db(path)
    with connect(path) as conn:
        conn.execute("INSERT INTO sources (source_id, kind, source_level, enabled) VALUES ('dnse_openapi','market_data',2,1)")
        conn.execute("INSERT INTO tickers (ticker, exchange, created_at, updated_at) VALUES ('AAA','HOSE','x','x')")
        f = frame(list(np.linspace(20_000, 40_000, 300)) + [36_000])
        conn.executemany(
            "INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source_id, observed_at) "
            "VALUES ('AAA',?,?,?,?,?,?,'dnse_openapi','x')",
            [(d.date().isoformat(), r.open, r.high, r.low, r.close, int(r.volume)) for d, r in f.iterrows()])
        conn.execute("INSERT INTO screen_runs (run_id, created_at, as_of_date, params_json, funnel_json) "
                     "VALUES ('r1', '2026-09-25', '2026-09-25', '{}', '{}')")
        conn.execute("INSERT INTO screen_members (run_id, ticker, grp, rank, score, rs_rating, criteria_json) "
                     "VALUES ('r1', 'AAA', 'A', 1, 80, 90, '{}')")
    return path


def test_run_monitors_the_latest_screen_and_is_idempotent(db):
    first = run_monitor(path=db)
    assert "AAA" in first.states
    assert any(e.ticker == "AAA" for e in first.events)
    run_monitor(path=db)
    with connect(db) as conn:
        alerts = conn.execute("SELECT COUNT(*) FROM alerts WHERE ticker='AAA'").fetchone()[0]
        states = conn.execute("SELECT COUNT(*) FROM monitor_state WHERE ticker='AAA'").fetchone()[0]
    assert alerts == len([e for e in first.events if e.ticker == "AAA"])     # no duplicates on re-run
    assert states == 1


def test_watchlist_names_without_data_are_listed_not_dropped(db):
    result = run_monitor(path=db, persist=False)
    assert "SSI" in result.missing                    # in watchlist.yaml, no bars in this test DB
    assert "SSI" in render_brief(result)


def test_brief_contains_no_buy_language(db):
    brief = render_brief(run_monitor(path=db, persist=False)).lower()
    assert "không phải khuyến nghị" in brief
    for word in ("nên mua", "mua ngay", "chắc chắn tăng"):
        assert word not in brief


def test_a_recent_listing_is_monitored_without_trend_template_events():
    closes = list(np.linspace(30_000, 36_000, 40)) + [32_000]          # a sharp drop on day 41
    events, today, before = events_for(closes)
    assert TREND_TEMPLATE_BROKEN not in events                        # no 52-week template yet
    assert LARGEST_DOWN_DAY in events                                  # price/volume events still apply


def test_recent_listing_state_marks_template_as_not_assessable():
    _, today, _ = events_for(list(np.linspace(30_000, 36_000, 40)))
    assert today.criteria["TT1"]["pass"] is None and today.criteria["TT7"]["pass"] is not None


def test_rerun_drops_an_alert_the_current_rules_no_longer_raise(db):
    run_monitor(path=db)
    with connect(db) as conn:
        conn.execute("INSERT INTO alerts (alert_id, ticker, created_at, alert_type, what_changed) "
                     "VALUES ('old', 'AAA', 'x', 'SETUP_READY', '[' || (SELECT MAX(trade_date) FROM prices_daily) || '] cũ')")
    run_monitor(path=db)
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM alerts WHERE alert_id = 'old'").fetchone()[0] == 0


# -- breakout quality and status (2026-09-27) ----------------------------------------

def test_breakout_closing_low_in_its_range_is_weak_even_on_volume():
    closes = flat_base_closes() + [41_000]
    volumes = [1_000_000] * (len(closes) - 1) + [2_000_000]
    f = frame(closes, volumes)
    f.iloc[-1, f.columns.get_loc("high")] = 43_000        # closed in the bottom of a wide range
    today = compute_state("AAA", "HOSE", f, None, 90.0, SCREEN, MON)
    before = compute_state("AAA", "HOSE", f.iloc[:-1], None, 90.0, SCREEN, MON)
    events = {e.event_type: e for e in detect_events("AAA", "HOSE", f, today, before, SCREEN, MON)}
    assert BREAKOUT_WEAK in events and "biên độ phiên" in events[BREAKOUT_WEAK].what_changed


def test_breakout_that_is_already_extended_is_weak():
    closes = flat_base_closes() + [44_500]                   # ~10% through the ~40,210 pivot at once
    volumes = [1_000_000] * (len(closes) - 1) + [2_000_000]
    events, _, _ = events_for(closes, volumes, exchange="UPCOM")
    assert BREAKOUT_WEAK in events and "vượt pivot hơn" in events[BREAKOUT_WEAK].what_changed


def test_breakout_in_a_correction_says_so():
    closes = flat_base_closes() + [41_000]
    volumes = [1_000_000] * (len(closes) - 1) + [2_000_000]
    f = frame(closes, volumes)
    today = compute_state("AAA", "HOSE", f, None, 90.0, SCREEN, MON)
    before = compute_state("AAA", "HOSE", f.iloc[:-1], None, 90.0, SCREEN, MON)
    market = {"status": "CORRECTION", "ftd": {}}
    events = {e.event_type: e for e in detect_events("AAA", "HOSE", f, today, before, SCREEN, MON, market)}
    assert BREAKOUT in events and "không mở vị thế mới" in events[BREAKOUT].why_it_matters


def test_status_is_attached_to_each_monitored_state(db):
    result = run_monitor(path=db, persist=False)
    setup = result.states["AAA"].setup
    assert setup["status"] in {"AT_PIVOT", "WATCH", "BREAKOUT", "INVALIDATED"}
    assert setup["market_note"]


# -- pivot / stop held after an event; volume around an ex-rights date (tester report 2026-10-11) --

def test_after_a_breakout_the_pivot_it_cleared_is_kept():
    """GMD 07/10/2026 broke out of 52,770 and the list then said "pivot 55,100" (B01)."""
    closes = flat_base_closes() + [41_000, 41_600, 42_300]
    volumes = [1_000_000] * (len(closes) - 3) + [2_000_000] * 3
    f = frame(closes, volumes)
    s = compute_state("AAA", "HOSE", f, None, 90.0, SCREEN, MON)
    assert s.pivot == 40_250 and s.held_by == "BREAKOUT" and s.held_since == f.index[-3].date().isoformat()
    assert s.distance_to_pivot_pct > 0 and not s.stop_breached


def test_the_hold_ends_after_the_status_window():
    closes = flat_base_closes() + [41_000] + [41_000 + 150 * k for k in range(1, 7)]
    s = compute_state("AAA", "HOSE", frame(closes), None, 90.0, SCREEN, MON)
    assert s.held_since is None                     # 6 sessions after the breakout: today's base again


def test_after_a_close_under_the_stop_the_stop_is_kept_and_marked_breached():
    """VIC 09/10/2026: "thủng stop 226.900", then the list showed stop 225.500 = the close (B05)."""
    base = flat_base_closes()
    stop_before = compute_state("AAA", "HOSE", frame(base), None, 90.0, SCREEN, MON).stop
    s = compute_state("AAA", "HOSE", frame(base + [stop_before * 0.97]), None, 90.0, SCREEN, MON)
    assert s.stop == stop_before and s.stop_breached and s.held_by == "STOP"


def test_volume_is_averaged_only_from_the_ex_rights_date():
    """DNSE rescaled HDB's prices for a 30% stock dividend but not its volumes (B03)."""
    closes = flat_base_closes() + [38_000] * 12
    volumes = [1_000_000] * (len(closes) - 12) + [1_300_000] * 11 + [2_600_000]
    f = frame(closes, volumes)
    through = f.index[-13].date().isoformat()          # last old-scale session: ex-date 12 sessions ago
    s = compute_state("AAA", "HOSE", f, None, 90.0, SCREEN, MON, actions=[(through, 0.769)])
    assert s.ex_date == f.index[-12].date().isoformat() and s.volume_ratio == pytest.approx(2.0)
    fresh = compute_state("AAA", "HOSE", f, None, 90.0, SCREEN, MON, actions=[(f.index[-4].date().isoformat(), 0.769)])
    assert fresh.volume_ratio is None and "GDKHQ" in fresh.volume_note   # 2 sessions after: not comparable


def test_a_breakout_on_the_ex_rights_date_is_never_rated_valid():
    closes = flat_base_closes() + [41_000]
    volumes = [1_000_000] * (len(closes) - 1) + [3_000_000]
    f = frame(closes, volumes)
    acts = [(f.index[-2].date().isoformat(), 0.769)]  # today is the ex-date
    today = compute_state("AAA", "HOSE", f, None, 90.0, SCREEN, MON, actions=acts)
    before = compute_state("AAA", "HOSE", f.iloc[:-1], None, 90.0, SCREEN, MON, actions=acts)
    events = {e.event_type: e for e in detect_events("AAA", "HOSE", f, today, before, SCREEN, MON, actions=acts)}
    assert BREAKOUT not in events and "GDKHQ" in events[BREAKOUT_WEAK].what_changed


# -- moving to base rules 2 (tester report 2, 2026-10-11) ---------------------------

from src.quant.monitor import apply_legacy_hold, legacy_hold  # noqa: E402


def _old_state(held_since, held_by="BREAKOUT", **kw):
    """A state stored by the earlier rules: no base_rules."""
    return {"base_kind": "BASE", "pivot": 52_800.0, "stop": 50_800.0, "held_since": held_since,
            "held_by": held_by, "contractions": [21.0, 5.0], **kw}


def test_a_hold_from_the_earlier_rules_is_kept_while_it_lasts():
    """GMD held 52,800 from its 07/10 breakout; the new rules read 55,600 (plan point 4)."""
    f = frame([50_000] * 40 + [53_000, 54_000, 55_000])
    since = f.index[-3].date().isoformat()
    held = legacy_hold(_old_state(since), f, 5)
    assert held and held["pivot"] == 52_800 and held["held_since"] == since
    state = compute_state("AAA", "HOSE", f, None, 90.0, SCREEN, MON)
    apply_legacy_hold(state, f, held)
    assert state.pivot == 52_800 and state.legacy_hold and state.distance_to_pivot_pct > 0
    # carried forward: a state that carries it keeps carrying it
    assert legacy_hold({**_old_state(since), "base_rules": 2, "legacy_hold": True}, f, 5)
    # the new rules' own holds are not "legacy"
    assert legacy_hold({**_old_state(since), "base_rules": 2}, f, 5) is None


def test_a_legacy_hold_ends_with_its_window_its_stop_or_a_rescale():
    f = frame([50_000] * 40 + [53_000] * 6)
    since = f.index[-6].date().isoformat()
    assert legacy_hold(_old_state(since), f, 5) is None                        # 6 sessions: over
    g = frame([50_000] * 40 + [53_000, 50_000])
    assert legacy_hold(_old_state(g.index[-2].date().isoformat()), g, 5) is None   # closed under the stop
    h = frame([50_000] * 40 + [53_000, 54_000])
    day = h.index[-2].date().isoformat()
    assert legacy_hold(_old_state(day), h, 5, actions=[(day, 0.5)]) is None    # a rescale since: other scale
    assert legacy_hold(_old_state(h.index[-1].date().isoformat()), h.iloc[:-1], 5) is None   # not yet held then


def test_keep_events_recomputes_states_and_leaves_the_sessions_alerts(db):
    run_monitor(path=db)
    with connect(db) as conn:
        before = conn.execute("SELECT alert_id, what_changed FROM alerts ORDER BY alert_id").fetchall()
        conn.execute("UPDATE monitor_state SET state_json = '{}'")
    result = run_monitor(path=db, keep_events=True)
    assert result.events == [] and "AAA" in result.states
    with connect(db) as conn:
        assert conn.execute("SELECT alert_id, what_changed FROM alerts ORDER BY alert_id").fetchall() == before
        state = conn.execute("SELECT state_json FROM monitor_state WHERE ticker = 'AAA'").fetchone()[0]
    assert '"base_rules": 2' in state
    decided = result.states["AAA"].setup.get("decided_by")
    assert decided is None or decided[0] in {r[1][1:11] for r in before}       # from the stored alerts
