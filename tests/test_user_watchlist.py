import pytest

from src.database import connect, init_db
from src.quant.monitor import monitored_tickers
from src.quant.user_watchlist import WatchlistError, add, entries, remove, tickers


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "w.db"
    init_db(path)
    with connect(path) as conn:
        conn.executemany("INSERT INTO tickers (ticker, exchange, created_at, updated_at) VALUES (?, ?, 'x', 'x')",
                         [("PHP", "UPCOM"), ("TRC", "HOSE"), ("ZZZ", "UPCOM")])
        conn.execute("INSERT INTO sources (source_id, kind, source_level, enabled) VALUES ('dnse_openapi','market_data',2,1)")
        conn.executemany("INSERT INTO prices_daily (ticker, trade_date, close, volume, source_id, observed_at) "
                         "VALUES (?, '2026-10-09', 10000, 100, 'dnse_openapi', 'x')", [("PHP",), ("TRC",)])
    return path


def test_add_is_idempotent_and_keeps_the_note(db):
    add("php", "cảng Hải Phòng", db)
    add("PHP", None, db)                                  # a second star keeps the note
    assert entries(db)[0]["note"] == "cảng Hải Phòng" and tickers(db) == ["PHP"]


def test_only_listed_three_character_shares_are_accepted(db):
    with pytest.raises(WatchlistError):
        add("VN30F1M", None, db)
    with pytest.raises(WatchlistError):
        add("XYZ", None, db)                              # not a listed share
    assert tickers(db) == []


def test_remove(db):
    add("TRC", None, db)
    assert remove("trc", db) and not remove("TRC", db) and tickers(db) == []


def test_the_users_picks_are_monitored(db):
    add("TRC", None, db)
    assert "TRC" in monitored_tickers(db)[0]


def test_a_listed_code_without_prices_is_refused(db):
    with pytest.raises(WatchlistError, match="chưa có dữ liệu giá"):
        add("ZZZ", None, db)                              # in DNSE's list, never traded (tester report B09)
    assert tickers(db) == []
