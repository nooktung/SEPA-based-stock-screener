import json
from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.database import connect, init_db
from src.quant.market_view import build_snapshot, load_snapshot, store_snapshot

END = date(2026, 10, 2)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "market.db"
    init_db(path)
    with connect(path) as conn:
        conn.execute("INSERT INTO sources (source_id, kind, source_level, enabled) VALUES ('dnse_openapi','market_data',2,1)")

        def stock(ticker, exchange, sessions, volume, start=20_000.0, end=40_000.0):
            conn.execute("INSERT INTO tickers (ticker, exchange, name, created_at, updated_at) VALUES (?,?,?, 'x','x')",
                         (ticker, exchange, f"CTCP {ticker}"))
            idx = pd.bdate_range(end=END, periods=sessions)
            closes = np.linspace(start, end, sessions)
            conn.executemany(
                "INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source_id, observed_at) "
                "VALUES (?,?,?,?,?,?,?,'dnse_openapi','x')",
                [(ticker, d.date().isoformat(), c, c, c, c, volume) for d, c in zip(idx, closes)])

        stock("AAA", "HOSE", 300, 1_000_000)          # liquid uptrend: 40 bn a session
        stock("BBB", "HNX", 300, 1_000)               # below the 3 bn floor
        stock("CCC", "UPCOM", 300, 1_000_000)         # flagged series
        stock("DDD", "HOSE", 100, 1_000_000)          # recent listing
        conn.execute("INSERT INTO data_freshness (dataset, scope_key, ttl_hours, is_stale) VALUES ('prices_daily','CCC',24,1)")
        conn.execute("INSERT INTO screen_runs (run_id, created_at, as_of_date, params_json, funnel_json, market_json) "
                     "VALUES ('r1','2026-10-02','2026-10-01','{}', ?, '{}')",
                     (json.dumps({"rs_ratings": {"AAA": 95.0, "DDD": 80.0}}),))
    return path


def snapshot(db):
    return {r["ticker"]: r for r in build_snapshot(as_of=END, path=db)}


def test_a_liquid_uptrend_is_measured_like_the_screen(db):
    a = snapshot(db)["AAA"]
    assert a["trade_date"] == "2026-10-02" and a["close"] == 40_000 and a["liquidity_tier"] == "HIGH"
    assert a["rs_rating"] == 95 and a["tt_passed"] == 8 and a["not_assessable"] == []
    assert a["above_ma"] == {"50": True, "150": True, "200": True} and a["pct_from_high"] == 0
    assert a["name"] == "CTCP AAA" and a["fund"]["grade"] == "NO_DATA" and not a["flagged"]


def test_below_the_floor_has_no_rs_and_tt8_is_not_a_fail(db):
    b = snapshot(db)["BBB"]
    assert b["liquidity_tier"] == "UNDER" and b["rs_rating"] is None
    assert b["not_assessable"] == ["TT8"] and "TT8" not in b["missing"] and b["tt_passed"] == 7


def test_a_flagged_series_gets_its_price_and_no_indicator(db):
    c = snapshot(db)["CCC"]
    assert c["flagged"] and c["close"] == 40_000
    assert "tt_passed" not in c and "above_ma" not in c and "base_kind" not in c


def test_a_recent_listing_is_measured_only_on_tt7(db):
    d = snapshot(db)["DDD"]
    assert d["sessions"] == 100 and d["missing"] == [] and "TT7" not in d["not_assessable"]
    assert len(d["not_assessable"]) == 7 and d["tt_passed"] == 1


def test_store_replaces_the_date_and_keeps_only_recent_snapshots(db):
    rows = build_snapshot(as_of=END, path=db)
    assert store_snapshot(rows, db, keep=2) == "2026-10-02"
    store_snapshot(rows, db, keep=2)                                  # same date again: replaced, not doubled
    as_of, back = load_snapshot(db)
    assert as_of == "2026-10-02" and [r["ticker"] for r in back] == ["AAA", "BBB", "CCC", "DDD"]
    for day in ("2026-09-30", "2026-10-01"):
        store_snapshot([dict(r, trade_date=day) for r in rows], db, keep=2)
    with connect(db) as conn:
        dates = [d for (d,) in conn.execute("SELECT DISTINCT as_of_date FROM market_snapshot ORDER BY as_of_date")]
    assert dates == ["2026-10-01", "2026-10-02"]


def test_a_stock_trading_on_few_days_is_not_called_a_new_listing(db):
    """BHP has years of history but few sessions in the window: "giao dịch thưa", not "mới niêm yết" (N04)."""
    with connect(db) as conn:
        conn.execute("INSERT INTO tickers (ticker, exchange, name, created_at, updated_at) VALUES ('EEE','UPCOM','CTCP E','x','x')")
        old = pd.bdate_range(end="2024-06-28", periods=300)
        recent = pd.bdate_range(end=END, periods=100)
        conn.executemany(
            "INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source_id, observed_at) "
            "VALUES ('EEE',?,?,?,?,?,1000,'dnse_openapi','x')",
            [(d.date().isoformat(), 10_000, 10_000, 10_000, 10_000) for d in list(old) + list(recent)])
    rows = snapshot(db)
    assert rows["EEE"]["history"] == "SPARSE" and rows["DDD"]["history"] == "NEW"
    assert "history" not in rows["AAA"]
