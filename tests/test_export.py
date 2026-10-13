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
    from src.web import server
    stocks = json.loads((out / "data/stocks/run_id-r1.json").read_text(encoding="utf-8"))
    assert stocks == json.loads(json.dumps(server._clean(server.api_stocks({"run_id": "r1"}, db)), default=str))
    for name in ("index.html", "app.js", "data/runs.json", "data/ticker/ticker-AAA.json",
                 "data/bars/tf-W_ticker-AAA.json", "data/flow/sessions-30_ticker-AAA.json", "data/news/days-30.json"):
        assert (out / name).is_file(), name
    assert not (out / "data/position").exists() and not (out / "data/mylist").exists()
    assert not (out.parent / "site.new").exists()


def test_a_value_from_env_anywhere_in_the_output_stops_the_export(db, tmp_path):
    with connect(db) as conn:
        conn.execute("UPDATE tickers SET name = 'CTCP sk-live-0123456789' WHERE ticker = 'AAA'")
    env = tmp_path / ".env"
    env.write_text("DNSE_API_SECRET=sk-live-0123456789\nTZ=Asia/Ho_Chi_Minh\n", encoding="utf-8")
    with pytest.raises(export.ExportRefused, match=".env"):
        export.export_site(out_dir=tmp_path / "site", path=db, now=datetime(2026, 10, 9, 16, 0, tzinfo=VN),
                           env_file=env, logo_dir=tmp_path / "logos")
    assert not (tmp_path / "site").exists() and not (tmp_path / "site.new").exists()
