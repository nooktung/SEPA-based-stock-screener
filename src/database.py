"""Database bootstrap and connection helpers.

SQLite by default so the project runs with zero setup. DATABASE_URL can point
at PostgreSQL later; the schema is restricted to the common subset of both.
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

PROJECT_ROOT = Path(__file__).resolve().parent.parent
SCHEMA_PATH = PROJECT_ROOT / "database" / "schema.sql"
DEFAULT_DB_PATH = PROJECT_ROOT / "data" / "agent.db"


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def db_path() -> Path:
    url = os.environ.get("DATABASE_URL", "")
    if url.startswith("postgres"):
        raise NotImplementedError(
            "PostgreSQL backend is not wired yet. Unset DATABASE_URL to use SQLite."
        )
    if url.startswith("sqlite:///"):
        return Path(url.replace("sqlite:///", "", 1))
    return DEFAULT_DB_PATH


@contextmanager
def connect(path: Path | None = None) -> Iterator[sqlite3.Connection]:
    target = path or db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    # WAL lets the dashboard and the whole-market snapshot read while a 45-minute
    # backfill writes; without it a long read made the backfill's commit fail with
    # "database is locked" (2026-10-04). The timeout covers two writers meeting.
    conn = sqlite3.connect(target, timeout=60)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def init_db(path: Path | None = None) -> Path:
    """Create all tables. Idempotent."""
    target = path or db_path()
    sql = SCHEMA_PATH.read_text(encoding="utf-8")
    with connect(target) as conn:
        _migrate(conn)
        conn.executescript(sql)
    return target


def _migrate(conn: sqlite3.Connection) -> None:
    """In-place upgrades for tables whose constraints changed after creation.

    SQLite cannot alter a CHECK constraint, so the table is rebuilt with every
    row copied. Its append-only triggers go with the old table and are
    recreated by the schema script that runs right after.
    """
    row = conn.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='screen_members'").fetchone()
    if row and "'C'" not in row[0]:
        conn.executescript(
            """
            DROP TRIGGER IF EXISTS screen_members_append_only;
            ALTER TABLE screen_members RENAME TO screen_members_old;
            CREATE TABLE screen_members (
                run_id TEXT NOT NULL, ticker TEXT NOT NULL, grp TEXT NOT NULL, rank INTEGER NOT NULL,
                score REAL NOT NULL, exchange TEXT, close REAL, rs_rating REAL, criteria_json TEXT NOT NULL,
                base_json TEXT, pivot REAL, stop REAL, risk_to_stop_pct REAL, locked_worst_case_pct REAL,
                liquidity_vnd REAL,
                PRIMARY KEY (run_id, ticker),
                FOREIGN KEY (run_id) REFERENCES screen_runs(run_id),
                CHECK (grp IN ('A','B','C'))
            );
            INSERT INTO screen_members SELECT * FROM screen_members_old;
            DROP TABLE screen_members_old;
            """
        )


    # 2026-10-03: share volumes of foreign buying / selling (DNSE totalBuyVolume / totalSellVolume).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(flow_daily)")}
    if cols and "foreign_buy_volume" not in cols:
        conn.execute("ALTER TABLE flow_daily ADD COLUMN foreign_buy_volume REAL")
        conn.execute("ALTER TABLE flow_daily ADD COLUMN foreign_sell_volume REAL")

    # 2026-10-06: how a reference price was obtained (published by DNSE, or UPCoM's prior average).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(reference_prices)")}
    if cols and "method" not in cols:
        conn.execute("ALTER TABLE reference_prices ADD COLUMN method TEXT NOT NULL DEFAULT 'secdef'")

    # 2026-10-08: matched / put-through volume and limit counts per index session (market-index).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(index_daily)")}
    if cols and "matched_volume" not in cols:
        for column, kind in (("matched_volume", "INTEGER"), ("matched_value_vnd", "REAL"),
                             ("put_through_volume", "INTEGER"), ("put_through_value_vnd", "REAL"),
                             ("ceilings", "INTEGER"), ("floors", "INTEGER"),
                             ("session_source_id", "TEXT"), ("session_observed_at", "TEXT")):
            conn.execute(f"ALTER TABLE index_daily ADD COLUMN {column} {kind}")
    # 2026-10-11: why a series is flagged, shown beside the flag.
    cols = {r[1] for r in conn.execute("PRAGMA table_info(data_freshness)")}
    if cols and "note" not in cols:
        conn.execute("ALTER TABLE data_freshness ADD COLUMN note TEXT")
    # 2026-10-09: the snapshot's highest / lowest value (index-check).
    cols = {r[1] for r in conn.execute("PRAGMA table_info(index_daily)")}
    if cols and "session_high" not in cols:
        conn.execute("ALTER TABLE index_daily ADD COLUMN session_high REAL")
        conn.execute("ALTER TABLE index_daily ADD COLUMN session_low REAL")


def sync_reference_data(path: Path | None = None) -> tuple[int, int]:
    """Push watchlist tickers and configured sources into the DB.

    Returns (tickers_written, sources_written).
    """
    from src.config import load_config

    cfg = load_config()
    now = utcnow()
    with connect(path) as conn:
        for entry in cfg.watchlist.entries:
            conn.execute(
                """
                INSERT INTO tickers (ticker, exchange, name, sector_profile, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    exchange=excluded.exchange,
                    name=excluded.name,
                    sector_profile=excluded.sector_profile,
                    active=excluded.active,
                    updated_at=excluded.updated_at
                """,
                (entry.ticker, entry.exchange, entry.name, entry.sector_profile,
                 int(entry.active), now, now),
            )
        for spec in cfg.sources.specs:
            conn.execute(
                """
                INSERT INTO sources (source_id, kind, source_level, event_authority, enabled)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(source_id) DO UPDATE SET
                    kind=excluded.kind,
                    source_level=excluded.source_level,
                    event_authority=excluded.event_authority,
                    enabled=excluded.enabled
                """,
                (spec.id, spec.kind, spec.source_level,
                 int(bool(spec.config.get("event_authority"))), int(spec.enabled)),
            )
        t = conn.execute("SELECT COUNT(*) FROM tickers").fetchone()[0]
        s = conn.execute("SELECT COUNT(*) FROM sources").fetchone()[0]
    return t, s
