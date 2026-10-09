"""`start`: which steps run, which are skipped, and how failures are reported."""

from datetime import date, datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from src import pipeline as pl
from src.database import connect, init_db

VN = timezone(timedelta(hours=7))
CFG = {"session_closed_after": "15:05", "coverage_ratio": 0.9, "news_min_interval_hours": 4, "flow_days": 10,
       "lock_stale_hours": 3, "refprices_min_interval_hours": 3}
SESSIONS = [date(2026, 9, 30), date(2026, 10, 1), date(2026, 10, 2)]


def test_today_counts_only_after_the_close():
    dates = SESSIONS + [date(2026, 10, 5)]
    assert pl.latest_closed_session(datetime(2026, 10, 5, 11, 0, tzinfo=VN), dates, CFG) == date(2026, 10, 2)
    assert pl.latest_closed_session(datetime(2026, 10, 5, 15, 10, tzinfo=VN), dates, CFG) == date(2026, 10, 5)
    assert pl.latest_closed_session(datetime(2026, 10, 4, 9, 0, tzinfo=VN), SESSIONS, CFG) == date(2026, 10, 2)


@pytest.fixture
def db(tmp_path, monkeypatch):
    monkeypatch.setattr(pl, "_monitored_count", lambda path: 10)
    path = tmp_path / "p.db"
    init_db(path)
    with connect(path) as conn:
        conn.execute("INSERT INTO sources (source_id, kind, source_level, enabled) VALUES ('dnse_openapi','market_data',2,1)")
        conn.executemany("INSERT INTO index_daily (symbol, trade_date, close, volume, source_id, observed_at) "
                         "VALUES ('VNINDEX', ?, 1700, 1, 'dnse_openapi', 'x')", [(d.isoformat(),) for d in SESSIONS])
        for i in range(100):           # 100 tickers traded on 01/10, only the 10 monitored ones so far on 02/10
            conn.execute("INSERT INTO tickers (ticker, exchange, created_at, updated_at) VALUES (?, 'HOSE', 'x', 'x')", (f"T{i:02d}",))
            days = SESSIONS[:2] + ([SESSIONS[2]] if i < 10 else [])
            conn.executemany("INSERT INTO prices_daily (ticker, trade_date, close, volume, source_id, observed_at) "
                             "VALUES (?, ?, 10, 1, 'dnse_openapi', 'x')", [(f"T{i:02d}", d.isoformat()) for d in days])
    return path


def keys(plan, run=True):
    return [s.key for s in plan.steps if s.run is run]


def test_a_new_session_runs_everything(db):
    plan = pl.plan_steps(datetime(2026, 10, 5, 9, 0, tzinfo=VN), db, index_dates=SESSIONS, cfg=CFG)
    assert plan.session == date(2026, 10, 2) and plan.session_source == "DNSE"
    assert keys(plan) == ["backfill", "index-session", "index-check", "industry", "fundamentals", "screen", "refprices",
                          "monitor", "flow", "news", "logos", "market"]
    assert "10/100" in plan.steps[0].why


def test_a_second_start_the_same_day_skips_what_is_done(db):
    with connect(db) as conn:
        conn.executemany("INSERT INTO prices_daily (ticker, trade_date, close, volume, source_id, observed_at) "
                         "VALUES (?, '2026-10-02', 10, 1, 'dnse_openapi', 'x')", [(f"T{i:02d}",) for i in range(10, 100)])
        # screened after 02/10 closed (15:05 VN = 08:05 UTC)
        conn.execute("INSERT INTO screen_runs (run_id, created_at, as_of_date, params_json, funnel_json) "
                     "VALUES ('r', '2026-10-02T09:00:00+00:00', '2026-10-02', '{}', '{}')")
        conn.executemany("INSERT INTO monitor_state (ticker, trade_date, state_json, computed_at) VALUES (?, '2026-10-02', '{}', 'x')",
                         [(f"T{i:02d}",) for i in range(10)])
        conn.execute("INSERT INTO news_articles (article_id, title, url, source_id, source_level, collected_at) "
                     "VALUES ('a', 't', 'u', 'dnse_openapi', 3, '2026-10-05T01:00:00+00:00')")
        conn.execute("INSERT INTO market_snapshot (as_of_date, ticker, row_json, created_at) VALUES ('2026-10-02', 'T00', '{}', 'x')")
        conn.execute("INSERT INTO reference_prices (ticker, session_date, reference, ceiling, floor, published_at, source_id, observed_at) VALUES ('T00', '2026-10-05', 10, 11, 9, '2026-10-02 20:46:00', "
                     "'dnse_openapi', '2026-10-05T00:30:00+00:00')")       # read at 07:30 VN, 1.5 hours ago
        conn.execute("UPDATE index_daily SET matched_volume = 1, session_observed_at = 'x' WHERE trade_date = '2026-10-02'")
    # the trading calendar was read today
    import src.market_calendar as mc

    mc.CALENDAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    mc.CALENDAR_FILE.write_text('{"fetched_on": "2026-10-05", "known_from": "2026-10-05", "known_to": "2026-10-09", '
                                '"dates": ["2026-10-05"]}', encoding="utf-8")
    # ... and the VN-Index check against HOSE was written for 02/10
    from src.collectors.index_check import report_path

    report_path("2026-10-02").parent.mkdir(parents=True, exist_ok=True)
    report_path("2026-10-02").write_text("{}", encoding="utf-8")
    plan = pl.plan_steps(datetime(2026, 10, 5, 9, 0, tzinfo=VN), db, index_dates=SESSIONS, cfg=CFG)
    assert keys(plan) == ["flow", "logos"]                 # both only ask for what is missing
    assert keys(plan, run=False) == ["backfill", "index-session", "index-check", "industry", "fundamentals", "screen",
                                     "refprices", "monitor", "news", "market"]
    # Three hours on, reference prices are read again (DNSE may have published the next session's).
    later = pl.plan_steps(datetime(2026, 10, 5, 12, 0, tzinfo=VN), db, index_dates=SESSIONS, cfg=CFG)
    assert "refprices" in keys(later)


def test_a_screen_run_before_the_close_does_not_count(db):
    with connect(db) as conn:
        conn.execute("INSERT INTO screen_runs (run_id, created_at, as_of_date, params_json, funnel_json) "
                     "VALUES ('r', '2026-10-02T03:00:00+00:00', '2026-10-02', '{}', '{}')")   # 10:00 VN, mid-session
    plan = pl.plan_steps(datetime(2026, 10, 5, 9, 0, tzinfo=VN), db, index_dates=SESSIONS, cfg=CFG)
    assert "screen" in keys(plan)


def test_without_dnse_the_stored_sessions_decide(db):
    plan = pl.plan_steps(datetime(2026, 10, 5, 9, 0, tzinfo=VN), db, index_dates=None, cfg=CFG)
    assert plan.session == date(2026, 10, 2) and plan.session_source == "dữ liệu đã lưu"


def test_steps_run_in_order_and_a_failure_does_not_stop_the_rest(db):
    plan = pl.plan_steps(datetime(2026, 10, 5, 9, 0, tzinfo=VN), db, index_dates=SESSIONS, cfg=CFG)
    codes = {"backfill": 3, "screen": 1}
    calls, lines = [], []

    def runner(cmd, cwd=None):
        calls.append(cmd[3])
        return SimpleNamespace(returncode=codes.get(cmd[3], 0))
    pl.run_steps(plan, runner=runner, out=lines.append, clock=iter(range(100)).__next__)
    assert calls == ["backfill", "index-session", "index-check", "industry", "fundamentals", "screen", "refprices",
                     "monitor", "flow", "news", "logos", "market"]
    result = {s.key: s.result for s in plan.steps}
    assert result["backfill"].startswith("xong (một số mã bị gắn cờ") and result["screen"].startswith("LỖI (mã thoát 1)")
    assert result["market"] == "xong"
    assert any(line.startswith("\n[1/12] Tải giá toàn thị trường") for line in lines)


def test_a_running_start_blocks_another_until_it_is_stale(tmp_path):
    lock = tmp_path / "pipeline.lock"
    now = datetime(2026, 10, 5, 9, 0, tzinfo=VN)
    assert pl.acquire_lock(now, CFG, lock) is None
    assert "PID" in pl.acquire_lock(now + timedelta(minutes=30), CFG, lock)
    assert pl.acquire_lock(now + timedelta(hours=4), CFG, lock) is None      # left over from a crash
    pl.release_lock(lock)
    assert not lock.exists()
