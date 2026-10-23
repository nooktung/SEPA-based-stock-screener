"""Static copy of the dashboard (export-site): same answers as the local API, read-only, no secrets."""

import json
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from src.database import connect, init_db
from src.web import export

VN = timezone(timedelta(hours=7))


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "site.db"
    init_db(path)
    days = pd.bdate_range(end="2026-10-09", periods=260)
    with connect(path) as conn:
        conn.execute("INSERT INTO sources (source_id, kind, source_level, enabled) VALUES ('dnse_openapi','market_data',2,1)")
        conn.execute("INSERT INTO tickers (ticker, exchange, name, created_at, updated_at) VALUES ('AAA','HOSE','CTCP A','x','x')")
        closes = np.linspace(20_000, 40_000, 260)
        conn.executemany("INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source_id, "
                         "observed_at) VALUES ('AAA',?,?,?,?,?,1000,'dnse_openapi','x')",
                         [(d.date().isoformat(), c, c, c, c) for d, c in zip(days, closes)])
        conn.executemany("INSERT INTO index_daily (symbol, trade_date, open, high, low, close, volume, source_id, "
                         "observed_at) VALUES ('VNINDEX',?,1700,1700,1700,1700,1000,'dnse_openapi','x')",
                         [(d.date().isoformat(),) for d in days])
        conn.execute("INSERT INTO screen_runs (run_id, created_at, as_of_date, params_json, funnel_json, market_json) "
                     "VALUES ('r1','2026-10-09T10:00:00+00:00','2026-10-09','{}','{}','{\"status\": \"UPTREND\"}')")
        conn.execute("INSERT INTO screen_members (run_id, ticker, grp, rank, score, exchange, close, rs_rating, "
                     "criteria_json, base_json) VALUES ('r1','AAA','A',1,80,'HOSE',40000,90,'{}','{\"kind\": \"VCP\"}')")
        conn.execute("INSERT INTO monitor_state (ticker, trade_date, state_json, computed_at) VALUES "
                     "('AAA','2026-10-09','{\"trade_date\": \"2026-10-09\"}','x')")
    return path


def test_the_path_rule_matches_the_page():
    """app.js staticPath must give the same names (keys sorted, unsafe characters replaced)."""
    assert export.static_path("runs") == "data/runs.json"
    assert export.static_path("ticker", {"ticker": "ABB"}) == "data/ticker/ticker-ABB.json"
    assert export.static_path("bars", {"ticker": "ABB", "tf": "W"}) == "data/bars/tf-W_ticker-ABB.json"
    assert export.static_path("stocks", {"run_id": "a.b/c"}) == "data/stocks/run_id-a_b_c.json"


def test_the_expected_session_waits_for_the_close():
    assert export.expected_session(datetime(2026, 10, 12, 10, 0, tzinfo=VN)).isoformat() == "2026-10-09"   # Monday morning
    assert export.expected_session(datetime(2026, 10, 12, 16, 0, tzinfo=VN)).isoformat() == "2026-10-12"
    assert export.expected_session(datetime(2026, 10, 11, 16, 0, tzinfo=VN)).isoformat() == "2026-10-09"   # Sunday


def test_stale_data_is_refused_and_the_published_copy_stays(db, tmp_path):
    out = tmp_path / "site"
    out.mkdir()
    (out / "site.json").write_text('{"old": true}', encoding="utf-8")
    with pytest.raises(export.ExportRefused, match="lượt lọc mới nhất"):
        export.export_site(out_dir=out, path=db, now=datetime(2026, 10, 13, 16, 0, tzinfo=VN),
                           env_file=tmp_path / "none.env", logo_dir=tmp_path / "logos")
    assert json.loads((out / "site.json").read_text(encoding="utf-8")) == {"old": True}


def test_the_copy_holds_the_api_answers_and_nothing_to_write(db, tmp_path):
    out = tmp_path / "site"
    report = export.export_site(out_dir=out, path=db, now=datetime(2026, 10, 9, 16, 0, tzinfo=VN),
                                env_file=tmp_path / "none.env", logo_dir=tmp_path / "logos")
    meta = json.loads((out / "site.json").read_text(encoding="utf-8"))
    assert meta["static"] and meta["session"] == "2026-10-09" and meta["detail"] == ["AAA"] == report.detail
    assert meta["local_account"] == "admin" and meta["mylist_max"] == 40      # read by functions/api/mylist.js
    from src.web import server
    stocks = json.loads((out / "data/stocks/run_id-r1.json").read_text(encoding="utf-8"))
    assert stocks == json.loads(json.dumps(server._clean(server.api_stocks({"run_id": "r1"}, db)), default=str))
    for name in ("index.html", "404.html", "app.js", "data/runs.json", "data/ticker/ticker-AAA.json",
                 "data/bars/tf-W_ticker-AAA.json", "data/flow/sessions-30_ticker-AAA.json", "data/news.json",
                 "data/universe.json"):
        assert (out / name).is_file(), name
    from src.web.page_routes import PAGE_PATHS
    for route in PAGE_PATHS:
        assert (out / (route[1:] + ".html")).read_bytes() == (out / "index.html").read_bytes()
    assert not (out / "data/position").exists() and not (out / "data/mylist").exists()
    assert not (out.parent / "site.new").exists()
    # Every path behind the login on Cloudflare, the public logos excepted.
    assert json.loads((out / "_routes.json").read_text(encoding="utf-8")) == \
        {"version": 1, "include": ["/*"], "exclude": ["/logo/*"]}


def test_a_value_from_env_anywhere_in_the_output_stops_the_export(db, tmp_path):
    with connect(db) as conn:
        conn.execute("UPDATE tickers SET name = 'CTCP sk-live-0123456789' WHERE ticker = 'AAA'")
    env = tmp_path / ".env"
    env.write_text("DNSE_API_SECRET=sk-live-0123456789\nTZ=Asia/Ho_Chi_Minh\n", encoding="utf-8")
    with pytest.raises(export.ExportRefused, match=".env"):
        export.export_site(out_dir=tmp_path / "site", path=db, now=datetime(2026, 10, 9, 16, 0, tzinfo=VN),
                           env_file=env, logo_dir=tmp_path / "logos")
    assert not (tmp_path / "site").exists() and not (tmp_path / "site.new").exists()


def _pick(db, ticker, name):
    """A second listed ticker with a year of prices, just added to the local list."""
    days = pd.bdate_range(end="2026-10-09", periods=260)
    with connect(db) as conn:
        conn.execute("INSERT INTO tickers (ticker, exchange, name, created_at, updated_at) VALUES (?,'HOSE',?,'x','x')",
                     (ticker, name))
        conn.executemany("INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source_id, "
                         "observed_at) VALUES (?,?,10000,10000,10000,10000,1000,'dnse_openapi','x')",
                         [(ticker, d.date().isoformat()) for d in days])
        conn.execute("INSERT INTO user_watchlist (ticker, added_at) VALUES (?, '2026-10-09T09:00:00+00:00')", (ticker,))


def _kw(db, tmp_path, env=None):
    return dict(path=db, now=datetime(2026, 10, 9, 16, 0, tzinfo=VN), env_file=env or tmp_path / "none.env",
                logo_dir=tmp_path / "logos")


def test_an_update_writes_only_what_a_new_ticker_changes(db, tmp_path):
    out = tmp_path / "site"
    export.export_site(out_dir=out, **_kw(db, tmp_path))
    assert not (out / "data" / "alerts").exists()
    assert not (out / "data" / "intraday.json").exists()
    before = json.loads((out / "site.json").read_text(encoding="utf-8"))
    index = out / export.static_path("index")
    index_time = index.stat().st_mtime_ns
    _pick(db, "BBB", "CTCP B")
    rep = export.update_site(out_dir=out, **_kw(db, tmp_path))
    assert rep.mode == "update" and rep.detail == ["BBB"]
    from src.web.page_routes import PAGE_PATHS
    for route in PAGE_PATHS:
        assert (out / (route[1:] + ".html")).read_bytes() == (out / "index.html").read_bytes()
    meta = json.loads((out / "site.json").read_text(encoding="utf-8"))
    assert meta["detail"] == ["AAA", "BBB"] and meta["run_id"] == "r1" and meta["version"] != before["version"]
    for name in ("data/ticker/ticker-BBB.json", "data/bars/tf-W_ticker-BBB.json", "data/bars/tf-M_ticker-BBB.json",
                 "data/flow/sessions-30_ticker-BBB.json"):
        assert (out / name).is_file(), name
    for name in ("data/stocks.json", "data/stocks/run_id-r1.json"):
        assert "BBB" in [r["ticker"] for r in json.loads((out / name).read_text(encoding="utf-8"))], name
    assert index.stat().st_mtime_ns == index_time                       # untouched: not part of an update
    assert not (tmp_path / "site.upd").exists()
    assert export.update_site(out_dir=out, **_kw(db, tmp_path)).detail == []   # nothing new the second time


def test_an_update_without_a_copy_of_this_run_is_the_full_export(db, tmp_path):
    out = tmp_path / "site"
    rep = export.update_site(out_dir=out, **_kw(db, tmp_path))
    assert rep.mode == "full" and (out / "index.html").is_file() and rep.detail == ["AAA"]
    meta = json.loads((out / "site.json").read_text(encoding="utf-8"))
    (out / "site.json").write_text(json.dumps({**meta, "run_id": "older"}), encoding="utf-8")
    assert export.update_site(out_dir=out, **_kw(db, tmp_path)).mode == "full"
    assert json.loads((out / "site.json").read_text(encoding="utf-8"))["run_id"] == "r1"


@pytest.mark.parametrize("incremental", [False, True])
def test_market_ticker_gets_all_detail_routes_without_a_watchlist(db, tmp_path, monkeypatch, incremental):
    from src.web import server

    out = tmp_path / "site"
    if incremental:
        export.export_site(out_dir=out, **_kw(db, tmp_path))
    _pick(db, "A32", "CTCP 32")
    with connect(db) as conn:
        conn.execute("DELETE FROM user_watchlist WHERE ticker = 'A32'")
    monkeypatch.setattr(server, "api_market", lambda q, path: {"rows": [{"ticker": "A32"}]})
    fn = export.update_site if incremental else export.export_site
    fn(out_dir=out, **_kw(db, tmp_path))
    meta = json.loads((out / "site.json").read_text(encoding="utf-8"))
    assert meta["detail"] == ["A32", "AAA"]
    for route, q in [("ticker", {"ticker": "A32"}), ("bars", {"ticker": "A32", "tf": "W"}),
                     ("bars", {"ticker": "A32", "tf": "M"}), ("flow", {"ticker": "A32", "sessions": "30"})]:
        payload = json.loads((out / export.static_path(route, q)).read_text(encoding="utf-8"))
        assert payload is not None
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM user_watchlist WHERE ticker='A32'").fetchone()[0] == 0


def test_an_update_carrying_a_value_from_env_changes_nothing(db, tmp_path):
    out = tmp_path / "site"
    env = tmp_path / ".env"
    env.write_text("SOME_KEY=Cong ty BBB bi mat\n", encoding="utf-8")
    export.export_site(out_dir=out, **_kw(db, tmp_path, env))
    before = (out / "site.json").read_text(encoding="utf-8")
    _pick(db, "BBB", "Cong ty BBB bi mat")
    with pytest.raises(export.ExportRefused, match=".env"):
        export.update_site(out_dir=out, **_kw(db, tmp_path, env))
    assert (out / "site.json").read_text(encoding="utf-8") == before
    assert not (out / "data/ticker/ticker-BBB.json").exists() and not (tmp_path / "site.upd").exists()


def test_the_monitor_can_be_limited_to_the_tickers_just_added(db):
    from src.quant.monitor import run_monitor

    _pick(db, "BBB", "CTCP B")
    res = run_monitor(persist=False, path=db, keep_events=True, only=["bbb"])
    assert res.tickers == ["BBB"] and set(res.states) <= {"BBB"}
    assert "AAA" in run_monitor(persist=False, path=db, keep_events=True).tickers


def test_parallel_chart_worker_matches_serial_api_answers(db, tmp_path):
    from concurrent.futures import ProcessPoolExecutor
    from src.web import server
    folder = tmp_path / "workers"
    folder.mkdir()
    with ProcessPoolExecutor(max_workers=2) as pool:
        detail, skipped = pool.submit(export._detail_job, ("AAA", folder, db)).result(timeout=60)
    assert detail == ["AAA"] and not skipped
    for route, query, fn in [
        ("ticker", {"ticker": "AAA"}, server.api_ticker),
        ("bars", {"ticker": "AAA", "tf": "W"}, server.api_bars),
        ("bars", {"ticker": "AAA", "tf": "M"}, server.api_bars),
        ("flow", {"ticker": "AAA", "sessions": export.FLOW_SESSIONS}, server.api_flow),
    ]:
        actual = json.loads((folder / export.static_path(route, query)).read_text(encoding="utf-8"))
        expected = json.loads(json.dumps(server._clean(fn(query, db)), default=str))
        assert actual == expected


def test_incremental_export_refreshes_existing_ticker_news_without_rebuilding_charts(db, tmp_path, monkeypatch):
    from src.web import server
    out = tmp_path / "site"
    export.export_site(out_dir=out, **_kw(db, tmp_path))
    detail = out / export.static_path("ticker", {"ticker": "AAA"})
    old = json.loads(detail.read_text(encoding="utf-8"))
    weekly = out / export.static_path("bars", {"ticker": "AAA", "tf": "W"})
    timestamp = weekly.stat().st_mtime_ns
    news = [{"event_id": "fresh", "headline": "AAA: Tin moi", "articles": []}]
    monkeypatch.setattr(server, "ticker_news", lambda ticker, path: news)
    original_api = server.api_ticker
    def check_new_only(q, path):
        assert q["ticker"] != "AAA", "must not recalculate existing chart"
        return original_api(q, path)
    monkeypatch.setattr(server, "api_ticker", check_new_only)
    export.update_site(out_dir=out, **_kw(db, tmp_path))
    actual = json.loads(detail.read_text(encoding="utf-8"))
    assert actual == {**old, "news": news}
    assert weekly.stat().st_mtime_ns == timestamp
