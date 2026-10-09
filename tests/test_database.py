import sqlite3
import tempfile
from pathlib import Path

import pytest

from src.database import connect, init_db, sync_reference_data


@pytest.fixture
def tmpdb(tmp_path):
    return tmp_path / "test.db"


def test_schema_creates_all_expected_tables(tmpdb):
    init_db(tmpdb)
    with connect(tmpdb) as conn:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
    names = {r[0] for r in rows}
    for expected in ("tickers", "prices_daily", "news_events", "news_articles",
                     "financial_metrics", "macro_series", "flow_daily",
                     "signals", "signal_outcomes", "theses", "market_regime_history"):
        assert expected in names, f"missing table {expected}"


def test_init_db_is_idempotent(tmpdb):
    init_db(tmpdb)
    init_db(tmpdb)


def test_reference_sync_writes_watchlist_and_sources(tmpdb):
    init_db(tmpdb)
    with connect(tmpdb) as conn:
        pass
    import src.database as db
    orig = db.db_path
    db.db_path = lambda: tmpdb
    try:
        tickers, sources = sync_reference_data(tmpdb)
    finally:
        db.db_path = orig
    assert tickers == 5
    assert sources >= 10


def test_assertion_type_check_rejects_invalid_value(tmpdb):
    init_db(tmpdb)
    with pytest.raises(sqlite3.IntegrityError):
        with connect(tmpdb) as conn:
            conn.execute(
                "INSERT INTO news_events (event_id, event_type, headline, first_seen_at, "
                "best_source_level, assertion_type) VALUES (?,?,?,?,?,?)",
                ("e1", "EARNINGS", "h", "2026-08-27", 1, "PROBABLY_TRUE"),
            )


def test_thesis_status_check_enforced(tmpdb):
    init_db(tmpdb)
    with connect(tmpdb) as conn:
        conn.execute("INSERT INTO tickers VALUES ('SSI','HOSE','n','securities',NULL,1,'t','t')")
    with pytest.raises(sqlite3.IntegrityError):
        with connect(tmpdb) as conn:
            conn.execute(
                "INSERT INTO theses (thesis_id,ticker,created_at,last_verified_at,status,"
                "summary,invalidation_conditions) VALUES (?,?,?,?,?,?,?)",
                ("t1", "SSI", "a", "b", "MAYBE", "s", "i"),
            )


def test_signals_table_accepts_insufficient_data_recommendation(tmpdb):
    init_db(tmpdb)
    with connect(tmpdb) as conn:
        conn.execute("INSERT INTO tickers VALUES ('SSI','HOSE','n','securities',NULL,1,'t','t')")
        conn.execute(
            "INSERT INTO signals (signal_id,ticker,created_at,report_type,recommendation) "
            "VALUES (?,?,?,?,?)",
            ("s1", "SSI", "2026-08-27", "PREMARKET", "INSUFFICIENT_DATA"),
        )
        row = conn.execute("SELECT recommendation FROM signals WHERE signal_id='s1'").fetchone()
    assert row[0] == "INSUFFICIENT_DATA"
