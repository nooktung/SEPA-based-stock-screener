"""The user's own picks ("Danh mục của tôi").

Stocks the user wants followed whether or not the screen selects them (PHP
before the liquidity floor was lowered, say). They are monitored and given news
with the focus list until removed. Only listed 3-character shares are
accepted -- the same universe the screen covers -- so a typo never creates a
phantom ticker.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path

from src.database import connect

_TICKER = re.compile(r"^[A-Z0-9]{3}$")
MAX_NOTE = 120


class WatchlistError(ValueError):
    pass


def add(ticker: str, note: str | None = None, path: Path | None = None) -> dict:
    t = (ticker or "").strip().upper()
    if not _TICKER.match(t):
        raise WatchlistError("mã phải gồm đúng 3 ký tự (VD: PHP)")
    with connect(path) as conn:
        row = conn.execute("SELECT ticker, exchange, name FROM tickers WHERE ticker = ?", (t,)).fetchone()
        if row is None:
            raise WatchlistError(f"không có mã {t} trong danh sách cổ phiếu HOSE/HNX/UPCoM")
        clean = (note or "").strip()[:MAX_NOTE] or None
        conn.execute(
            "INSERT INTO user_watchlist (ticker, added_at, note) VALUES (?, ?, ?) "
            "ON CONFLICT(ticker) DO UPDATE SET note = COALESCE(excluded.note, user_watchlist.note)",
            (t, datetime.now(timezone.utc).isoformat(timespec="seconds"), clean))
    return {"ticker": t, "exchange": row[1], "name": row[2], "note": clean}


def remove(ticker: str, path: Path | None = None) -> bool:
    with connect(path) as conn:
        return conn.execute("DELETE FROM user_watchlist WHERE ticker = ?",
                            ((ticker or "").strip().upper(),)).rowcount > 0


def tickers(path: Path | None = None) -> list[str]:
    with connect(path) as conn:
        return [r[0] for r in conn.execute("SELECT ticker FROM user_watchlist ORDER BY added_at")]


def entries(path: Path | None = None) -> list[dict]:
    with connect(path) as conn:
        return [dict(r) for r in conn.execute("SELECT ticker, added_at, note FROM user_watchlist ORDER BY added_at")]
