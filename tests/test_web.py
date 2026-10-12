import json
import threading
import urllib.request
from http.server import ThreadingHTTPServer

import numpy as np
import pandas as pd
import pytest

from src.database import connect, init_db
from src.web import server


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "web.db"
    init_db(path)
    with connect(path) as conn:
        conn.execute("INSERT INTO sources (source_id, kind, source_level, enabled) VALUES ('dnse_openapi','market_data',2,1)")
        conn.execute("INSERT INTO tickers (ticker, exchange, name, created_at, updated_at) VALUES ('AAA','HOSE','CTCP A','x','x')")
        idx = pd.bdate_range(end="2026-09-25", periods=260)
        closes = np.linspace(20_000, 40_000, 260)
        conn.executemany(
            "INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source_id, observed_at) "
            "VALUES ('AAA',?,?,?,?,?,1000,'dnse_openapi','x')",
            [(d.date().isoformat(), c, c, c, c) for d, c in zip(idx, closes)])
        conn.execute("INSERT INTO screen_runs (run_id, created_at, as_of_date, params_json, funnel_json, market_json) "
                     "VALUES ('r1','2026-09-26','2026-09-25','{}', ?, ?)",
                     (json.dumps({"funnel": {"universe": 10, "members": 1}, "exclusions": {}}),
                      json.dumps({"status": "UPTREND"})))
        conn.execute("INSERT INTO screen_members (run_id, ticker, grp, rank, score, exchange, close, rs_rating, "
                     "criteria_json, base_json) VALUES ('r1','AAA','A',1,80,'HOSE',40000,90,?,?)",
                     (json.dumps({"TT1": {"pass": True}, "TT5": {"pass": False}}), json.dumps({"kind": "VCP"})))
    return path


def test_screen_api_returns_latest_run_with_missing_criteria(db):
    data = server.api_screen({}, db)
    assert data["run"]["run_id"] == "r1" and "params_json" not in data["run"]
    assert data["members"][0]["missing"] == ["TT5"]


def test_ticker_api_serves_bars_and_moving_averages(db):
    data = server.api_ticker({"ticker": "aaa"}, db)
    assert len(data["bars"]) == 260
    assert len(data["ma"]["MA200"]) == 61 and len(data["ma"]["MA20"]) == 241
    assert data["screen"]["grp"] == "A"


def test_unknown_run_is_a_404_not_a_crash(db):
    with pytest.raises(KeyError):
        server.api_screen({"run_id": "nope"}, db)


def test_http_server_serves_page_and_json_and_refuses_writes(db):
    server.Handler.db_path = db
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    port = httpd.server_address[1]
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        page = urllib.request.urlopen(f"http://127.0.0.1:{port}/").read().decode("utf-8")
        assert "SEPA Việt Nam" in page
        runs = json.loads(urllib.request.urlopen(f"http://127.0.0.1:{port}/api/runs").read())
        assert runs[0]["run_id"] == "r1"
        with pytest.raises(urllib.error.HTTPError) as exc:
            urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/runs", data=b"x", method="POST"))
        assert exc.value.code == 404                      # only /api/mylist accepts a write
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(f"http://127.0.0.1:{port}/../../config/sources.yaml")
    finally:
        httpd.shutdown()
        server.Handler.db_path = None


def test_nan_in_stored_json_never_reaches_the_browser(db):
    with connect(db) as conn:
        conn.execute("INSERT INTO monitor_state (ticker, trade_date, state_json, computed_at) "
                     "VALUES ('AAA', '2026-09-25', '{\"above_ma200_pct\": NaN, \"close\": 1}', 'x')")
    payload = server._clean(server.api_screen({}, db))
    text = json.dumps(payload, allow_nan=False)        # raises if any NaN survived
    assert "NaN" not in text


def test_ticker_api_reports_missing_fundamentals_and_industry_honestly(db):
    data = server.api_ticker({"ticker": "AAA"}, db)
    assert data["fundamentals"]["grade"] == "NO_DATA" and data["fundamentals"]["quarters"] == []
    assert data["industry"] is None


def test_industries_api_reads_the_runs_stored_table(db):
    assert server.api_industries({}, db)["industries"] == []
    with connect(db) as conn:
        conn.execute("INSERT INTO screen_runs (run_id, created_at, as_of_date, params_json, funnel_json, market_json) "
                     "VALUES ('r2','2026-09-27','2026-09-26','{}', ?, '{}')", (json.dumps(
                         {"funnel": {}, "exclusions": {},
                          "industries": [{"code": "2010", "name": "X", "rs_percentile": 90}]}),))
    assert server.api_industries({}, db)["industries"][0]["code"] == "2010"
    assert server.api_industries({"run_id": "r1"}, db)["industries"] == []


def test_ticker_api_serves_levels_even_without_swings(db):
    lv = server.api_ticker({"ticker": "AAA"}, db)["levels"]
    assert set(lv) >= {"zones", "trendlines", "channel", "note"}   # a straight line has no swings to join


def test_leaders_api_reads_the_runs_leaders(db):
    assert server.api_leaders({}, db) == []
    with connect(db) as conn:
        conn.execute("INSERT INTO screen_industry_leaders (run_id, industry_code, industry, rank, ticker, grp, "
                     "liquidity_vnd, detail_json) VALUES ('r1','4020','Dịch vụ tài chính',1,'AAA',NULL,1e9,?)",
                     (json.dumps({"ticker": "AAA", "industry": "Dịch vụ tài chính", "rank": 1}),))
    rows = server.api_leaders({}, db)
    assert rows[0]["ticker"] == "AAA" and rows[0]["name"] == "CTCP A"


def test_leaders_are_monitored_after_members(db):
    from src.quant.screener import latest_focus_list

    with connect(db) as conn:
        conn.execute("INSERT INTO screen_industry_leaders (run_id, industry_code, industry, rank, ticker, grp, "
                     "liquidity_vnd, detail_json) VALUES ('r1','4020','X',1,'SSI',NULL,1e9,'{}'), "
                     "('r1','4020','X',2,'AAA','A',1e9,'{}')")
    assert latest_focus_list(db) == ["AAA", "SSI"]


def _serve(db):
    server.Handler.db_path = db
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, httpd.server_address[1]


def _post(port, body, headers=None):
    h = {"Content-Type": "application/json", **(headers or {})}
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api/mylist", data=json.dumps(body).encode(),
                                 headers=h, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_mylist_add_list_remove_through_the_dashboard(db):
    httpd, port = _serve(db)
    try:
        assert _post(port, {"action": "add", "ticker": "aaa", "note": "theo dõi thử"})[0] == 200
        rows = server.api_mylist({}, db)
        assert rows[0]["ticker"] == "AAA" and rows[0]["note"] == "theo dõi thử"
        assert rows[0]["tt_passed"] >= 0 and rows[0]["close"] == pytest.approx(40_000)
        assert _post(port, {"action": "remove", "ticker": "AAA"}) == (200, {"ok": True, "removed": True})
        assert server.api_mylist({}, db) == []
    finally:
        httpd.shutdown()
        server.Handler.db_path = None


def test_mylist_refuses_unknown_tickers_and_foreign_pages(db):
    httpd, port = _serve(db)
    try:
        code, body = _post(port, {"action": "add", "ticker": "ZZZ"})
        assert code == 400 and "ZZZ" in body["error"]
        assert _post(port, {"action": "add", "ticker": "AAA"}, {"Origin": "https://evil.example"})[0] == 403
        assert _post(port, {"action": "add", "ticker": "AAA"}, {"Content-Type": "text/plain"})[0] == 403
        assert server.api_mylist({}, db) == []           # nothing was written
    finally:
        httpd.shutdown()
        server.Handler.db_path = None


def test_position_api_checks_a_cost_against_the_rules(db):
    r = server.api_position({"ticker": "aaa", "cost": "35000", "qty": "100"}, db)
    assert r["pnl_pct"] > 0 and r["situation"] in ("gain_small", "gain_big")
    assert any(lv["name"] == "Giá vốn của bạn" for lv in r["levels"])
    assert {s["title"] for s in r["sections"]} >= {"Xu hướng", "Điều kiện gia tăng theo SEPA"}
    with pytest.raises(KeyError):
        server.api_position({"ticker": "AAA", "cost": "0"}, db)


def test_position_api_asks_before_checking_a_cost_far_from_the_price(db):
    # "40.000" read as 40 (a unit slip): ask, offer 40,000 VND, never check silently.
    r = server.api_position({"ticker": "AAA", "cost": "40"}, db)
    assert r["check"] == "unit" and r["suggest_cost"] == 40_000 and r["close"] == pytest.approx(40_000)
    assert "levels" not in r
    # far off with no plausible x1000 reading: still asked, nothing suggested
    assert server.api_position({"ticker": "AAA", "cost": "900000"}, db)["suggest_cost"] is None
    # the user confirms the figure: the check runs
    r = server.api_position({"ticker": "AAA", "cost": "40", "confirm": "1"}, db)
    assert r["situation"] == "gain_big" and r["levels"]


def _flow(conn, ticker, day, buy, sell):
    conn.execute("INSERT INTO flow_daily (scope, scope_key, trade_date, foreign_buy_value, foreign_sell_value, "
                 "foreign_net_value, source_id, observed_at, foreign_buy_volume, foreign_sell_volume) "
                 "VALUES ('TICKER', ?, ?, ?, ?, ?, 'dnse_openapi', 'x', ?, ?)",
                 (ticker, day, buy * 40_000, sell * 40_000, (buy - sell) * 40_000, buy, sell))


def test_stocks_api_lists_every_monitored_name_with_its_source_and_flow(db):
    from src.quant.user_watchlist import add

    with connect(db) as conn:
        conn.execute("INSERT INTO tickers (ticker, exchange, name, created_at, updated_at) VALUES ('BBB','HNX','CTCP B','x','x')")
        conn.execute("INSERT INTO prices_daily (ticker, trade_date, close, volume, source_id, observed_at) "
                     "VALUES ('BBB', '2026-09-25', 10000, 500, 'dnse_openapi', 'x')")    # "Của tôi" needs a priced share
        _flow(conn, "AAA", "2026-09-25", 300, 100)
        _flow(conn, "AAA", "2026-09-24", 50, 150)
    add("BBB", "theo dõi", db)
    rows = {r["ticker"]: r for r in server.api_stocks({}, db)}
    a, b = rows["AAA"], rows["BBB"]
    assert a["grp"] == "A" and not a["mine"] and a["tt_passed"] == 1 and a["missing"] == ["TT5"]
    assert a["flow"]["date"] == "2026-09-25" and a["flow"]["net_volume"] == 200 and a["flow"]["net5_volume"] == 100
    assert a["flow"]["volume"] == 1000                     # the session's matched volume, for the domestic side
    assert b["mine"] and b["note"] == "theo dõi" and b["grp"] is None and b["flow"]["net_volume"] is None
    assert list(rows)[:2] == ["AAA", "BBB"]                 # members first, then the user's picks, then the watchlist


def test_flow_api_splits_foreign_and_domestic_shares(db):
    with connect(db) as conn:
        _flow(conn, "AAA", "2026-09-25", 300, 100)
    r = server.api_flow({"ticker": "aaa"}, db)
    row = r["rows"][0]
    assert row["foreign_net"] == 200 and row["foreign_net_value"] == 200 * 40_000
    assert row["domestic_buy"] == 700 and row["domestic_sell"] == 900   # matched volume 1000 minus the foreign side
    assert server.api_flow({"ticker": "ZZZ"}, db)["rows"] == []


def test_learning_page_script_is_served(db):
    httpd, port = _serve(db)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/learn.js") as resp:
            body = resp.read().decode("utf-8")
        assert resp.headers["Content-Type"].endswith("charset=utf-8")
        assert "Cốc tay cầm" in body and "Cây thông" in body and "renderLearn" in body
    finally:
        httpd.shutdown()
        server.Handler.db_path = None


def test_logo_route_serves_stored_logos_read_only(db, tmp_path, monkeypatch):
    from src.collectors import logos

    monkeypatch.setattr(logos, "LOGO_DIR", tmp_path)
    (tmp_path / "AAA.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)
    assert server.api_logos({}, db) == ["AAA"]
    httpd, port = _serve(db)
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/logo/aaa") as resp:
            assert resp.headers["Content-Type"] == "image/png" and resp.read().startswith(b"\x89PNG")
            assert "max-age" in resp.headers["Cache-Control"]
        for bad in ("/logo/BBB", "/logo/..%2F..%2Fdata%2Fagent.db", "/logo/AAAA"):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}{bad}")
                raise AssertionError(bad)
            except urllib.error.HTTPError as exc:
                assert exc.code == 404
    finally:
        httpd.shutdown()
        server.Handler.db_path = None


def test_market_api_serves_the_latest_snapshot_with_current_membership(db):
    from src.quant.market_view import store_snapshot
    from src.quant.user_watchlist import add

    assert server.api_market({}, db)["rows"] == []                        # nothing built yet: say how
    assert "python -m src.main market" in server.api_market({}, db)["note"]
    store_snapshot([{"ticker": "AAA", "trade_date": "2026-09-25", "close": 40_000, "rs_from": "2026-09-24"}], db)
    add("AAA", None, db)
    r = server.api_market({}, db)
    # the run the snapshot's RS came from, not the latest run's date (tester report B04)
    assert r["as_of"] == "2026-09-25" and r["rs_from"] == "2026-09-24" and "2026-09-24" in r["note"]
    row = r["rows"][0]
    assert row["grp"] == "A" and row["mine"] and row["leader_of"] is None


def test_change_is_against_the_reference_price_when_one_was_read(db):
    """PHP 05/10/2026: +6.00% close to close, +4.65% on the boards (UPCoM reference = prior average price)."""
    from src.quant.market_view import store_snapshot

    with connect(db) as conn:
        conn.execute("INSERT INTO reference_prices (ticker, session_date, reference, ceiling, floor, published_at, source_id, observed_at) VALUES ('AAA', '2026-09-25', 39800, 42500, 37000, "
                     "'2026-09-24 20:46:00', 'dnse_openapi', 'x')")
    store_snapshot([{"ticker": "AAA", "exchange": "HOSE", "trade_date": "2026-09-25", "close": 40_000, "change_pct": 0.3},
                    {"ticker": "PHP", "exchange": "UPCOM", "trade_date": "2026-09-25", "close": 49_500, "change_pct": 6.0}], db)
    rows = {r["ticker"]: r for r in server.api_market({}, db)["rows"]}
    assert rows["AAA"]["change_pct"] == 0.5 and rows["AAA"]["change_basis"] == "ref" and rows["AAA"]["reference"] == 39800
    assert rows["AAA"]["change_close_pct"] == 0.3
    assert rows["PHP"]["change_pct"] == 6.0 and rows["PHP"]["change_basis"] == "close"     # no reference read: said so
    t = server.api_ticker({"ticker": "AAA"}, db)
    assert t["change"]["pct"] == 0.5 and t["change"]["basis"] == "ref" and t["references"] == {"2026-09-25": 39800}


def test_a_second_dashboard_on_the_same_port_is_refused():
    first = server.DashboardServer(("127.0.0.1", 0), server.Handler)
    try:
        port = first.server_address[1]
        with pytest.raises(SystemExit, match=str(port)):
            server.serve(port=port, open_browser=False)
    finally:
        first.server_close()


def test_index_api_serves_the_chart_with_distribution_days(db):
    idx = pd.bdate_range(end="2026-09-25", periods=260)
    closes = list(np.linspace(1500, 1800, 260))
    closes[-1] = closes[-2] * 0.99                       # last session: -1% ...
    vols = [1000] * 259 + [2000]                         # ... on higher volume: a distribution day
    with connect(db) as conn:
        conn.executemany(
            "INSERT INTO index_daily (symbol, trade_date, open, high, low, close, volume, source_id, observed_at) "
            "VALUES ('VNINDEX',?,?,?,?,?,?,'dnse_openapi','x')",
            [(d.date().isoformat(), c, c + 5, c - 5, c, v) for d, c, v in zip(idx, closes, vols)])
    r = server.api_index({}, db)
    assert r["info"] == {"ticker": "VNINDEX", "name": "VN-Index", "kind": "index"} and r["decimals"] == 2
    assert len(r["bars"]) == 260 and len(r["ma"]["MA200"]) == 61 and r["ma"]["MA200"][0]["time"] == idx[199].date().isoformat()
    assert r["distribution_dates"] == ["2026-09-25"] and r["market"]["distribution_days"] == 1
    with pytest.raises(KeyError):
        server.api_index({"symbol": "SPX"}, db)
    weeks = server.api_bars({"index": "vnindex", "tf": "w"}, db)
    assert weeks["tf"] == "W" and len(weeks["bars"]) == 52 and weeks["bars"][-1]["period_end"] == "2026-09-25"


def test_bars_api_serves_weekly_and_monthly_charts(db):
    d = server.api_ticker({"ticker": "AAA"}, db)
    assert d["tf"] == "D" and d["ma_periods"]["MA50"] == 50
    w = server.api_bars({"ticker": "aaa", "tf": "W"}, db)
    assert len(w["bars"]) == 52 and sum(b["sessions"] for b in w["bars"]) == 260
    assert w["ma_periods"] == {"MA20": 4, "MA50": 10, "MA150": 30, "MA200": 40}
    assert set(w["levels"]) == {"trendlines", "channel", "note"} and "nến tuần" in w["levels"]["note"]
    assert len(w["ma"]["MA200"]) == 52 - 39 and w["ma"]["MA200"][0]["time"] == w["bars"][39]["trade_date"]
    m = server.api_bars({"ticker": "AAA", "tf": "M"}, db)
    assert m["bars"][-1]["period_end"] == "2026-09-25" and m["bars"][-1]["close"] == 40_000
    for bad in ({"ticker": "AAA", "tf": "Q"}, {"ticker": "ZZZ", "tf": "W"}, {"tf": "W"}, {"index": "SPX", "tf": "W"}):
        with pytest.raises(KeyError):
            server.api_bars(bad, db)


def test_index_api_shows_matched_volume_and_breadth(db):
    idx = pd.bdate_range(end="2026-09-25", periods=260)
    closes = list(np.linspace(1500, 1800, 260))
    closes[-1] = closes[-2] * 0.99                       # last session -1% ...
    with connect(db) as conn:
        conn.executemany(
            "INSERT INTO index_daily (symbol, trade_date, open, high, low, close, volume, source_id, observed_at) "
            "VALUES ('VNINDEX',?,?,?,?,?,?,'dnse_openapi','x')",
            [(d.date().isoformat(), c, c + 5, c - 5, c, 1000) for d, c in zip(idx, closes)])
        conn.execute("UPDATE index_daily SET volume = 2000 WHERE trade_date = '2026-09-25'")   # ... total volume up,
        conn.executemany("UPDATE index_daily SET matched_volume = ?, put_through_volume = ?, advancers = 110, "
                         "unchanged = 56, decliners = 183, ceilings = 12, floors = 7, session_observed_at = 'x' "
                         "WHERE trade_date = ?", [(800, 200, "2026-09-24"), (700, 1300, "2026-09-25")])   # matched down
    r = server.api_index({}, db)
    assert r["distribution_dates"] == [] and r["market"]["distribution_days"] == 0
    assert r["market"]["volume_basis"]["basis"] == "matched"
    assert r["bars"][-1]["volume"] == 700 and r["bars"][-3]["volume"] == 1000     # the bars show what is compared
    assert r["session"]["advancers"] == 110 and r["session"]["put_through_volume"] == 1300
    weeks = server.api_bars({"index": "VNINDEX", "tf": "W"}, db)
    assert weeks["bars"][-1]["volume"] == 1000 * 3 + 800 + 700
    live = server.api_screen({}, db)["run"]["live_market"]                          # the header VN-Index card
    assert live["session"]["decliners"] == 183 and live["session"]["ceilings"] == 12


def test_domestic_side_is_blank_when_foreign_volume_exceeds_matched(db):
    """VHM 09/10/2026 showed 0 / 0: foreign volume includes put-through deals (tester report B16)."""
    with connect(db) as conn:
        _flow(conn, "AAA", "2026-09-25", 1_500, 400)          # matched volume of that session: 1000
    row = server.api_flow({"ticker": "AAA"}, db)["rows"][0]
    assert row["domestic_buy"] is None and row["domestic_sell"] == 600


def test_an_older_run_shows_the_change_since_its_screening_day(db):
    """Viewing a past run showed today's price only (tester report B12)."""
    with connect(db) as conn:
        conn.execute("INSERT INTO screen_runs (run_id, created_at, as_of_date, params_json, funnel_json) "
                     "VALUES ('r2','2026-09-27','2026-09-26','{}','{}')")       # a newer run
        conn.execute("INSERT INTO monitor_state (ticker, trade_date, state_json, computed_at) "
                     "VALUES ('AAA','2026-09-26', ?, 'x')", (json.dumps({"close": 44_000}),))
    old = {r["ticker"]: r for r in server.api_stocks({"run_id": "r1"}, db)}["AAA"]
    assert old["since_screen"] == {"date": "2026-09-25", "close": 40_000, "pct": 10.0}
    assert all(r.get("since_screen") is None for r in server.api_stocks({}, db))   # the latest run: nothing to add


# -- tester report 2 (2026-10-11) ----------------------------------------------------

def test_since_the_screen_uses_the_adjusted_close_of_that_day(db):
    """GMD read -29.9% since 01/10: screen_members.close was on the price scale before its bonus shares (N01)."""
    with connect(db) as conn:
        conn.execute("UPDATE prices_daily SET close = close / 2, open = open / 2, high = high / 2, low = low / 2")
        conn.execute("INSERT INTO screen_runs (run_id, created_at, as_of_date, params_json, funnel_json) "
                     "VALUES ('r2','2026-09-27','2026-09-26','{}','{}')")
        conn.execute("INSERT INTO monitor_state (ticker, trade_date, state_json, computed_at) "
                     "VALUES ('AAA','2026-09-26', ?, 'x')", (json.dumps({"close": 22_000}),))
    old = {r["ticker"]: r for r in server.api_stocks({"run_id": "r1"}, db)}["AAA"]
    assert old["since_screen"] == {"date": "2026-09-25", "close": 20_000, "pct": 10.0}


def test_a_price_newer_than_the_state_is_shown_with_the_states_date(db):
    """PET showed its 05/10 price after leaving the list while others showed 09/10 (N02)."""
    with connect(db) as conn:
        conn.execute("INSERT INTO monitor_state (ticker, trade_date, state_json, computed_at) "
                     "VALUES ('AAA','2026-09-24', ?, 'x')", (json.dumps({"close": 39_000, "pivot": 41_000}),))
    row = server.api_stocks({}, db)[0]
    assert row["trade_date"] == "2026-09-25" and row["close"] == 40_000 and row["state_date"] == "2026-09-24"
    assert row["distance"] == pytest.approx((40_000 / 41_000 - 1) * 100)


def test_float_noise_never_reaches_the_browser():
    assert server._clean({"p": [16149.999999999998, float("nan")]}) == {"p": [16150.0, None]}


def test_the_invariant_check_flags_rule_breaks_and_lists_older_numbers_apart(db):
    from src.web import checks
    with connect(db) as conn:
        bad = {"close": 40_000, "pivot": 41_000, "stop": 37_000, "base_kind": "BASE", "contractions": [12.0],
               "setup": {"status": "BREAKOUT"}, "stop_breached": False, "base_rules": 2}
        conn.execute("INSERT INTO monitor_state (ticker, trade_date, state_json, computed_at) "
                     "VALUES ('AAA','2026-09-25', ?, 'x')", (json.dumps(bad),))
    report = checks.run_checks(db, zones=False)
    rules = {v["rule"] for v in report["violations"]}
    assert {"risk_8pct", "breakout_above_pivot"} <= rules and report["transition"] == []
    with connect(db) as conn:
        conn.execute("UPDATE monitor_state SET state_json = ?", (json.dumps({**bad, "base_rules": None}),))
    report = checks.run_checks(db, zones=False)
    assert report["violations"] == [] and {r["rule"] for r in report["transition"]} >= {"risk_8pct"}


def test_state_checks_cover_each_rule():
    from src.quant.screener import screen_settings
    from src.web.checks import state_violations
    s = screen_settings()
    ok = {"close": 40_000, "pivot": 41_000, "stop": 38_000, "base_kind": "VCP", "contractions": [20.0, 8.0],
          "setup": {"status": "AT_PIVOT"}, "stop_breached": False}
    assert state_violations(ok, "HOSE", s) == []
    found = dict(state_violations({**ok, "close": 37_900, "stop": 38_010, "base_kind": "TOO_DEEP",
                                   "contractions": [1.0] * 9}, "HOSE", s))
    assert set(found) == {"breached_flag", "stop_needs_base", "on_tick", "legs"}
    assert state_violations({**ok, "base_kind": "FLAT_BASE", "contractions": []}, "HOSE", s) == []   # 0 legs is a base
    assert dict(state_violations({**ok, "contractions": [5.0] * 7}, "HOSE", s)).keys() == {"legs"}


def test_a_stop_breach_of_an_earlier_base_is_marked(db):
    """Old "thủng stop" events stay visible, labelled when their stop is not today's (plan, round 2)."""
    with connect(db) as conn:
        conn.execute("INSERT INTO alerts (alert_id, ticker, created_at, alert_type, what_changed) VALUES "
                     "('s1','AAA','x','STOP_BREACHED','[2026-09-24] Đóng cửa 43.100 dưới mức stop tham chiếu 44.500 (đáy nhịp)')")
        conn.execute("INSERT INTO monitor_state (ticker, trade_date, state_json, computed_at) "
                     "VALUES ('AAA','2026-09-25', ?, 'x')", (json.dumps({"close": 40_000, "stop": None}),))
    row = next(r for r in server.api_alerts({}, db) if r["alert_type"] == "STOP_BREACHED")
    assert row["stale_stop"] is True and row["current_stop"] is None
    with connect(db) as conn:
        conn.execute("UPDATE monitor_state SET state_json = ?", (json.dumps({"close": 40_000, "stop": 44_500}),))
    assert next(r for r in server.api_alerts({}, db) if r["alert_type"] == "STOP_BREACHED")["stale_stop"] is False


def test_the_checks_route_serves_the_latest_report(db):
    from src.web import checks
    assert server.api_checks({}, db) is None                      # nothing written yet
    checks.write_report({"session": "2026-09-25", "checked": 3, "violations": [{"ticker": "AAA", "rule": "on_tick",
                         "text": "x", "detail": "stop 1", "session": "2026-09-25"}], "transition": [], "info": []})
    out = server.api_checks({}, db)
    assert out["violations"] == 1 and out["session"] == "2026-09-25" and out["rows"][0]["ticker"] == "AAA"
    assert "/api/checks" in server.ROUTES and "/api/checks" not in server.WRITE_ROUTES


# -- tester report 3 (2026-10-12) -----------------------------------------------------

def test_the_flow_lists_every_session_and_five_sessions_mean_five_sessions(db):
    """ABB's "5 phiên" added five stored rows spread over 14/09-30/09 (R3-03)."""
    with connect(db) as conn:
        for day, buy, sell in (("2026-09-25", 100, 0), ("2026-09-22", 50, 0), ("2026-09-01", 900, 0)):
            _flow(conn, "AAA", day, buy, sell)
    rows = server.api_flow({"ticker": "AAA", "sessions": "5"}, db)["rows"]
    assert [r["date"] for r in rows] == ["2026-09-25", "2026-09-24", "2026-09-23", "2026-09-22", "2026-09-21"]
    assert [r["has_flow"] for r in rows] == [True, False, False, True, False]
    flow = server.api_stocks({}, db)[0]["flow"]
    assert flow["net5_volume"] == 150 and flow["net5_sessions"] == 2 and flow["net5_of"] == 5   # 01/09 is left out


def test_old_event_texts_show_the_tt6_threshold(db):
    with connect(db) as conn:
        conn.execute("INSERT INTO alerts (alert_id, ticker, created_at, alert_type, what_changed) VALUES "
                     "('t6','AAA','x','TREND_TEMPLATE_BROKEN','[2026-09-25] Mất tiêu chí TT6 (Giá cao hơn đáy 52 tuần ≥ X%).')")
    text = next(r for r in server.api_alerts({}, db) if r["alert_type"] == "TREND_TEMPLATE_BROKEN")["what_changed"]
    assert "≥ 30%" in text and "X%" not in text
