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
                         [("PHP", "UPCOM"), ("TRC", "HOSE")])
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
