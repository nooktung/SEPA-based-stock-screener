"""Screening universe: every ordinary share on HOSE, HNX and UPCoM.

DNSE's /market/instruments lists ~3,300 rows mixing shares with covered
warrants, bonds, ETFs, funds, futures and indices. The rules for what counts
as a share live in config/universe.yaml; this module only applies them.

A partial listing is never treated as the universe. If pagination stops short
of the advertised total, the fetch raises instead of returning a truncated
list -- otherwise every ticker on the missing pages would be marked inactive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from src.collectors.base import ProviderDataMissing
from src.config import UniverseConfig, load_universe
from src.database import connect, utcnow

INSTRUMENTS_PATH = "/market/instruments"
SOURCE_ID = "dnse"
# The endpoint ignores larger values and serves 100 rows per page.
PAGE_SIZE = 100
# Guard against an endpoint that keeps returning pages forever.
MAX_PAGES = 200


@dataclass(frozen=True)
class UniverseMember:
    ticker: str
    exchange: str
    name: str
    listed_date: str | None
    index_membership: tuple[str, ...]
    source_id: str
    observed_at: str


def _symbol_pattern(cfg: UniverseConfig) -> re.Pattern[str]:
    tail = "[A-Z0-9]" if cfg.allow_digits else "[A-Z]"
    return re.compile(rf"^[A-Z]{tail}{{{cfg.symbol_length - 1}}}$")


def filter_instruments(
    rows: Iterable[dict[str, Any]],
    cfg: UniverseConfig,
    observed_at: str | None = None,
) -> list[UniverseMember]:
    """Keep ordinary shares on the configured exchanges. Pure; no I/O."""
    observed_at = observed_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    pattern = _symbol_pattern(cfg)
    members: dict[str, UniverseMember] = {}
    for row in rows:
        symbol = str(row.get("symbol") or "").strip().upper()
        exchange = cfg.markets.get(str(row.get("marketId") or ""))
        if exchange is None:
            continue
        if str(row.get("securityGroupId") or "") not in cfg.security_groups:
            continue
        if not pattern.match(symbol):
            continue
        indices = row.get("indexName") or ()
        members[symbol] = UniverseMember(
            ticker=symbol,
            exchange=exchange,
            name=str(row.get("name") or row.get("shortName") or symbol),
            listed_date=row.get("listedDate") or None,
            index_membership=tuple(indices) if isinstance(indices, list) else (),
            source_id=SOURCE_ID,
            observed_at=observed_at,
        )
    return sorted(members.values(), key=lambda m: m.ticker)


def fetch_instruments(client: Any) -> list[dict[str, Any]]:
    """Read every page of /market/instruments via a signed GET client.

    Raises ProviderDataMissing if the pages do not add up to `total`.
    """
    rows: list[dict[str, Any]] = []
    total: int | None = None
    for page in range(1, MAX_PAGES + 1):
        body = client.get(INSTRUMENTS_PATH, {"page": page, "pageSize": PAGE_SIZE})
        if not isinstance(body, dict) or not isinstance(body.get("data"), list):
            raise ProviderDataMissing(f"unrecognised {INSTRUMENTS_PATH} payload on page {page}")
        total = int(body.get("total") or 0)
        rows.extend(body["data"])
        if not body["data"] or len(rows) >= total:
            break
    if total is None or len(rows) != total:
        raise ProviderDataMissing(
            f"{INSTRUMENTS_PATH} returned {len(rows)} rows but advertised {total}; "
            "refusing to treat a partial listing as the universe"
        )
    return rows


def sync_universe(members: list[UniverseMember], path: Path | None = None) -> dict[str, int]:
    """Upsert members into `tickers` and deactivate codes no longer listed.

    sector_profile is only set on insert, so watchlist curation is never
    overwritten by the generic default.
    """
    if not members:
        raise ProviderDataMissing("empty universe; refusing to deactivate every ticker")
    now = utcnow()
    listed = {m.ticker for m in members}
    with connect(path) as conn:
        before = {r[0] for r in conn.execute("SELECT ticker FROM tickers")}
        for m in members:
            conn.execute(
                """
                INSERT INTO tickers (ticker, exchange, name, listed_date, active, created_at, updated_at)
                VALUES (?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    exchange=excluded.exchange,
                    name=excluded.name,
                    listed_date=excluded.listed_date,
                    active=1,
                    updated_at=excluded.updated_at
                """,
                (m.ticker, m.exchange, m.name, m.listed_date, now, now),
            )
        gone = sorted(before - listed)
        conn.executemany(
            "UPDATE tickers SET active=0, updated_at=? WHERE ticker=? AND active=1",
            [(now, t) for t in gone],
        )
    return {"listed": len(listed), "added": len(listed - before), "deactivated": len(gone)}


def build_universe(client: Any, cfg: UniverseConfig | None = None) -> list[UniverseMember]:
    return filter_instruments(fetch_instruments(client), cfg or load_universe())
