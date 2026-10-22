"""The user's own picks ("Danh mục của tôi").

Stocks the user wants followed whether or not the screen selects them (PHP
before the liquidity floor was lowered, say). They are monitored and given news
with the focus list until removed. Only listed 3-character shares are
accepted -- the same universe the screen covers -- so a typo never creates a
phantom ticker.
"""

from __future__ import annotations

import re
import json
from datetime import datetime, timezone
from pathlib import Path

from src.database import connect

_TICKER = re.compile(r"^[A-Z0-9]{3}$")
MAX_NOTE = 120


class WatchlistError(ValueError):
    pass


def add(ticker: str, note: str | None = None, path: Path | None = None, max_items: int | None = None) -> dict:
    t = (ticker or "").strip().upper()
    if not _TICKER.match(t):
        raise WatchlistError("mã phải gồm đúng 3 ký tự (VD: PHP)")
    with connect(path) as conn:
        if max_items is not None:
            have = {r[0] for r in conn.execute("SELECT ticker FROM user_watchlist")}
            if t not in have and len(have) >= max_items:
                raise WatchlistError(f"danh sách tối đa {max_items} mã")
        row = conn.execute("SELECT ticker, exchange, name FROM tickers WHERE ticker = ?", (t,)).fetchone()
        if row is None:
            raise WatchlistError(f"không có mã {t} trong danh sách cổ phiếu HOSE/HNX/UPCoM")
        # Listed but never traded here (ZZZ: in DNSE's list, no prices): nothing to show or monitor.
        if conn.execute("SELECT 1 FROM prices_daily WHERE ticker = ? LIMIT 1", (t,)).fetchone() is None:
            raise WatchlistError(f"mã {t} chưa có dữ liệu giá nên không theo dõi được")
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


# ---------------------------------------------------------------------------
# The master list on Cloudflare KV (src/collectors/cloudflare_kv.py), when the web copy is set up
# ---------------------------------------------------------------------------

def as_items(path: Path | None = None) -> list[dict]:
    """The local list in the KV value's shape."""
    return [{"ticker": e["ticker"], "added_at": e["added_at"], "note": e["note"]} for e in entries(path)]


def mirror(items: list[dict], path: Path | None = None) -> dict[str, list]:
    """Make the local list equal to `items` (the master). A ticker that is not a listed share
    with prices is skipped and reported, never stored. Returns added / removed / skipped."""
    wanted: dict[str, dict] = {}
    skipped: list[tuple[str, str]] = []
    with connect(path) as conn:
        for item in items:
            t = str((item or {}).get("ticker") or "").strip().upper()
            if not _TICKER.match(t):
                skipped.append((t, "mã không hợp lệ"))
                continue
            if conn.execute("SELECT 1 FROM prices_daily WHERE ticker = ? LIMIT 1", (t,)).fetchone() is None:
                skipped.append((t, "chưa có dữ liệu giá"))
                continue
            wanted[t] = item
        have = {r[0] for r in conn.execute("SELECT ticker FROM user_watchlist")}
        added = sorted(set(wanted) - have)
        removed = sorted(have - set(wanted))
        for t in added:
            item = wanted[t]
            note = (str(item.get("note") or "").strip()[:MAX_NOTE]) or None
            conn.execute("INSERT INTO user_watchlist (ticker, added_at, note) VALUES (?, ?, ?)",
                         (t, item.get("added_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"), note))
        for t in removed:
            conn.execute("DELETE FROM user_watchlist WHERE ticker = ?", (t,))
        for t in set(wanted) & have:                   # a note edited on the web
            note = (str(wanted[t].get("note") or "").strip()[:MAX_NOTE]) or None
            conn.execute("UPDATE user_watchlist SET note = ? WHERE ticker = ?", (note, t))
    return {"added": added, "removed": removed, "skipped": skipped}

# ---------------------------------------------------------------------------
# The other accounts' lists of the web copy (one list per account, 2026-10-14)
# ---------------------------------------------------------------------------

def mirror_web(lists: dict[str, list[dict]], max_items: int, path: Path | None = None) -> dict[str, list]:
    """Make web_watchlist hold exactly `lists` ({account: items}, the local account excluded).
    The same checks as the local list (a listed share with prices); an account's tickers past
    `max_items` (oldest first) are skipped. Returns the tickers that entered / left the union of
    these lists and the skipped ones as (account, ticker, why)."""
    skipped: list[tuple[str, str, str]] = []
    wanted: dict[tuple[str, str], dict] = {}
    with connect(path) as conn:
        before = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM web_watchlist")}
        for account, items in lists.items():
            kept = 0
            for item in sorted(items, key=lambda i: str((i or {}).get("added_at") or "")):
                t = str((item or {}).get("ticker") or "").strip().upper()
                if not _TICKER.match(t):
                    skipped.append((account, t, "mã không hợp lệ"))
                elif conn.execute("SELECT 1 FROM prices_daily WHERE ticker = ? LIMIT 1", (t,)).fetchone() is None:
                    skipped.append((account, t, "chưa có dữ liệu giá"))
                elif kept >= max_items:
                    skipped.append((account, t, f"quá {max_items} mã"))
                elif (account, t) not in wanted:
                    wanted[(account, t)] = item
                    kept += 1
        conn.execute("DELETE FROM web_watchlist")
        conn.executemany("INSERT INTO web_watchlist (account, ticker, added_at, note) VALUES (?, ?, ?, ?)", [
            (a, t, item.get("added_at") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
             (str(item.get("note") or "").strip()[:MAX_NOTE]) or None) for (a, t), item in wanted.items()])
    after = {t for _, t in wanted}
    return {"added": sorted(after - before), "removed": sorted(before - after), "skipped": skipped}


def web_tickers(path: Path | None = None) -> list[str]:
    """Every ticker in the other accounts' lists, the earliest added first."""
    with connect(path) as conn:
        return [r[0] for r in conn.execute(
            "SELECT ticker FROM web_watchlist GROUP BY ticker ORDER BY MIN(added_at), ticker")]


def refresh_requirements(tickers: list[str], session: str, path: Path | None = None) -> tuple[list[str], list[str]]:
    """Reuse shared prices/states when another account adds an already updated ticker."""
    prices, states = [], []
    with connect(path) as conn:
        for ticker in dict.fromkeys(tickers):
            latest = conn.execute("SELECT MAX(trade_date) FROM prices_daily WHERE ticker = ?", (ticker,)).fetchone()[0]
            need_price = not latest or latest < session
            if need_price:
                prices.append(ticker)
            state = conn.execute("SELECT trade_date, state_json FROM monitor_state WHERE ticker = ? "
                                 "ORDER BY trade_date DESC LIMIT 1", (ticker,)).fetchone()
            try:
                data = json.loads(state[1]) if state else {}
            except (ValueError, TypeError):
                data = {}
            if need_price or not state or state[0] < max(session, latest or "") or not (data.get("tech") or data.get("flagged")):
                states.append(ticker)
    return prices, states
