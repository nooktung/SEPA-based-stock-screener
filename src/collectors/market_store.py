"""Persistence for collected market data.

Kept apart from the providers on purpose: a provider's only job is to return
records with provenance, and the store's only job is to write them. Neither
knows how the other works, so a new vendor needs no storage change and a
storage change needs no vendor change.

Every write carries the source_id and observed_at that came with the record.
A bar in this database can always be traced to the source that produced it and
the moment it was read.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Iterable, Sequence

from src.collectors.base import FlowRecord, OHLCV
from src.database import connect, utcnow
from src.market_calendar import price_limit_pct

log = logging.getLogger(__name__)

# Datasets tracked in data_freshness, so the confidence penalty for stale data
# has something concrete to read.
DATASET_PRICES_DAILY = "prices_daily"
DATASET_INDEX_DAILY = "index_daily"
DATASET_FLOW_DAILY = "flow_daily"
DEFAULT_PRICE_TTL_HOURS = 24


class StoreError(RuntimeError):
    """A write was rejected by the database."""


def _iso(value: date | datetime | None) -> str | None:
    if value is None:
        return None
    return value.isoformat()


def _observed_from(observed: datetime) -> str:
    """Observation time as a comparable ISO string, always offset-aware.

    A naive timestamp is read as UTC rather than as machine-local: an ambiguous
    time in an audit trail is worse than one that is explicitly assumed.
    """
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    return observed.isoformat(timespec="seconds")


def _observed(bar: OHLCV) -> str:
    return _observed_from(bar.provenance.observed_at)


def upsert_daily_bars(bars: Sequence[OHLCV], path: Path | None = None) -> int:
    """Write equity bars into prices_daily. Idempotent per (ticker, date).

    Re-running a collection overwrites the same rows rather than appending, so
    a corrected bar from the vendor replaces the earlier read instead of
    silently coexisting with it. Note that this is prices only -- signals and
    their outcomes are append-only and are never rewritten this way.
    """
    if not bars:
        return 0

    rows = [
        (
            bar.symbol.upper(),
            bar.trade_date.isoformat(),
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.volume,
            bar.value_vnd,
            bar.adj_close,
            bar.adj_factor,
            bar.provenance.source_id,
            _observed(bar),
        )
        for bar in bars
    ]

    with connect(path) as conn:
        try:
            conn.executemany(
                """
                INSERT INTO prices_daily
                    (ticker, trade_date, open, high, low, close, volume, value_vnd,
                     adj_close, adj_factor, source_id, observed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(ticker, trade_date) DO UPDATE SET
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    volume=excluded.volume,
                    value_vnd=excluded.value_vnd,
                    adj_close=excluded.adj_close,
                    adj_factor=excluded.adj_factor,
                    source_id=excluded.source_id,
                    observed_at=excluded.observed_at
                """,
                rows,
            )
        except sqlite3.IntegrityError as exc:
            raise StoreError(
                f"prices_daily rejected a write ({exc}). Both the ticker and the source "
                "must exist first -- run `python -m src.main initdb`."
            ) from exc
    return len(rows)


def upsert_index_bars(bars: Sequence[OHLCV], path: Path | None = None) -> int:
    """Write benchmark bars into index_daily.

    Breadth columns (advancers/decliners/unchanged) are left untouched: they
    come from a flow source, not from an OHLC feed, and writing NULL over an
    existing breadth reading would destroy data this feed never had.
    """
    if not bars:
        return 0

    rows = [
        (
            bar.symbol.upper(),
            bar.trade_date.isoformat(),
            bar.open,
            bar.high,
            bar.low,
            bar.close,
            bar.volume,
            bar.value_vnd,
            bar.provenance.source_id,
            _observed(bar),
        )
        for bar in bars
    ]

    with connect(path) as conn:
        conn.executemany(
            """
            INSERT INTO index_daily
                (symbol, trade_date, open, high, low, close, volume, value_vnd,
                 source_id, observed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol, trade_date) DO UPDATE SET
                open=excluded.open,
                high=excluded.high,
                low=excluded.low,
                close=excluded.close,
                volume=excluded.volume,
                value_vnd=excluded.value_vnd,
                source_id=excluded.source_id,
                observed_at=excluded.observed_at
            """,
            rows,
        )
    return len(rows)


def upsert_flows(records: Sequence[FlowRecord], path: Path | None = None) -> int:
    """Write foreign-flow records into flow_daily. Idempotent per session."""
    if not records:
        return 0

    rows = [
        (
            record.scope,
            record.scope_key.upper(),
            record.trade_date.isoformat(),
            record.foreign_buy_value,
            record.foreign_sell_value,
            record.foreign_net_value,
            record.prop_net_value,
            record.etf_net_value,
            record.total_match_value,
            record.provenance.source_id,
            _observed_from(record.provenance.observed_at),
            record.foreign_buy_volume,
            record.foreign_sell_volume,
            record.foreign_buy_volume_matched,
            record.foreign_sell_volume_matched,
            record.foreign_buy_value_matched,
            record.foreign_sell_value_matched,
            1 if record.boards_split else 0,
        )
        for record in records
    ]

    with connect(path) as conn:
        conn.executemany(
            """
            INSERT INTO flow_daily
                (scope, scope_key, trade_date, foreign_buy_value, foreign_sell_value,
                 foreign_net_value, prop_net_value, etf_net_value, total_match_value,
                 source_id, observed_at, foreign_buy_volume, foreign_sell_volume,
                 foreign_buy_volume_matched, foreign_sell_volume_matched, foreign_buy_value_matched,
                 foreign_sell_value_matched, boards_split)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(scope, scope_key, trade_date) DO UPDATE SET
                foreign_buy_value=excluded.foreign_buy_value,
                foreign_sell_value=excluded.foreign_sell_value,
                foreign_net_value=excluded.foreign_net_value,
                prop_net_value=excluded.prop_net_value,
                etf_net_value=excluded.etf_net_value,
                total_match_value=excluded.total_match_value,
                source_id=excluded.source_id,
                observed_at=excluded.observed_at,
                foreign_buy_volume=excluded.foreign_buy_volume,
                foreign_sell_volume=excluded.foreign_sell_volume,
                foreign_buy_volume_matched=excluded.foreign_buy_volume_matched,
                foreign_sell_volume_matched=excluded.foreign_sell_volume_matched,
                foreign_buy_value_matched=excluded.foreign_buy_value_matched,
                foreign_sell_value_matched=excluded.foreign_sell_value_matched,
                boards_split=excluded.boards_split
            """,
            rows,
        )
    return len(rows)


def stored_flow_dates(
    scope: str, scope_key: str, start: date, end: date, path: Path | None = None
) -> set[date]:
    """Sessions already held for this scope.

    The foreign-trading endpoint allows a one-day window per request, so a
    backfill is one call per ticker per session. Knowing what is already stored
    turns the daily run from hundreds of requests into a handful.
    """
    return set(stored_flow_observations(scope, scope_key, start, end, path=path))


def stored_flow_observations(
    scope: str, scope_key: str, start: date, end: date, path: Path | None = None
) -> dict[date, datetime]:
    """Sessions already held, mapped to when each was read.

    The read time matters as much as the date. A flow record captured at noon
    is a running total, and it stays a running total after the closing bell --
    so "we already have that session" is not the same as "that session is
    final", and only the second one justifies not asking again.
    """
    with connect(path) as conn:
        rows = conn.execute(
            """
            SELECT trade_date, observed_at FROM flow_daily
             WHERE scope = ? AND scope_key = ? AND trade_date BETWEEN ? AND ? AND boards_split = 1
            """,
            (scope, scope_key.upper(), start.isoformat(), end.isoformat()),
        ).fetchall()

    out: dict[date, datetime] = {}
    for row in rows:
        try:
            observed = datetime.fromisoformat(row["observed_at"])
        except (TypeError, ValueError):
            continue
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=timezone.utc)
        out[date.fromisoformat(row["trade_date"])] = observed
    return out


def update_breadth(
    symbol: str,
    trade_date: date,
    advancers: int | None,
    decliners: int | None,
    unchanged: int | None,
    path: Path | None = None,
) -> int:
    """Attach breadth counts to an existing index session.

    Updates rather than inserts: breadth arrives from a different source than
    the index OHLC, and creating a bar here would produce an index row with no
    prices in it.
    """
    with connect(path) as conn:
        cursor = conn.execute(
            """
            UPDATE index_daily
               SET advancers = ?, decliners = ?, unchanged = ?
             WHERE symbol = ? AND trade_date = ?
            """,
            (advancers, decliners, unchanged, symbol.upper(), trade_date.isoformat()),
        )
        return cursor.rowcount


def mark_freshness(
    dataset: str,
    scope_key: str,
    ttl_hours: int = DEFAULT_PRICE_TTL_HOURS,
    stale: bool = False,
    updated_at: str | None = None,
    path: Path | None = None,
    note: str | None = None,
) -> None:
    """Record when a dataset was last refreshed, whether it is stale, and why (`note`).

    A failed collection marks stale and leaves last_updated_at pointing at the
    previous success -- so the age of the data stays visible instead of being
    reset by the attempt that failed to renew it.
    """
    with connect(path) as conn:
        if stale:
            conn.execute(
                """
                INSERT INTO data_freshness (dataset, scope_key, last_updated_at, ttl_hours, is_stale, note)
                VALUES (?, ?, NULL, ?, 1, ?)
                ON CONFLICT(dataset, scope_key) DO UPDATE SET
                    ttl_hours=excluded.ttl_hours,
                    is_stale=1,
                    note=excluded.note
                """,
                (dataset, scope_key, ttl_hours, note),
            )
        else:
            conn.execute(
                """
                INSERT INTO data_freshness (dataset, scope_key, last_updated_at, ttl_hours, is_stale)
                VALUES (?, ?, ?, ?, 0)
                ON CONFLICT(dataset, scope_key) DO UPDATE SET
                    last_updated_at=excluded.last_updated_at,
                    ttl_hours=excluded.ttl_hours,
                    is_stale=0,
                    note=NULL
                """,
                (dataset, scope_key, updated_at or utcnow(), ttl_hours),
            )


def record_source_outcome(source_id: str, ok: bool, path: Path | None = None) -> None:
    """Update the health counters the runner uses to decide what to trust."""
    now = utcnow()
    with connect(path) as conn:
        if ok:
            conn.execute(
                "UPDATE sources SET last_success_at = ?, consecutive_fails = 0 WHERE source_id = ?",
                (now, source_id),
            )
        else:
            conn.execute(
                """
                UPDATE sources
                   SET last_failure_at = ?,
                       consecutive_fails = consecutive_fails + 1
                 WHERE source_id = ?
                """,
                (now, source_id),
            )


def latest_bar_date(ticker: str, path: Path | None = None) -> date | None:
    """Most recent stored trade date for a ticker, or None if it has no rows."""
    with connect(path) as conn:
        row = conn.execute(
            "SELECT MAX(trade_date) AS d FROM prices_daily WHERE ticker = ?",
            (ticker.upper(),),
        ).fetchone()
    if not row or row["d"] is None:
        return None
    return date.fromisoformat(row["d"])


def count_bars(ticker: str, path: Path | None = None) -> int:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM prices_daily WHERE ticker = ?", (ticker.upper(),)
        ).fetchone()
    return int(row["n"]) if row else 0


def tape_adjustment_factor(bar: OHLCV, totals: OHLCV, tolerance_pct: float = 0.5) -> float | None:
    """bar / tape when open, high, low and close all differ by the same factor, else None.

    On the evening before an ex-date DNSE rescales its whole daily history,
    today's bar included, while the tape keeps the prices that traded (GMD,
    06/10/2026: bar 52,000 vs last match 78,000 = 2/3 for the 2:1 bonus issue
    going ex on 07/10). An unsettled bar does not look like that -- its open
    matches the tape while its close lags -- so one common factor away from 1
    means an adjusted, settled bar. `tolerance_pct` absorbs the vendor's
    rounding of adjusted prices to the tick.
    """
    pairs = [(b, t) for b, t in ((bar.open, totals.open), (bar.high, totals.high), (bar.low, totals.low),
                                 (bar.close, totals.close)) if b and t]
    if len(pairs) < 4:
        return None
    ratios = [b / t for b, t in pairs]
    lo, hi = min(ratios), max(ratios)
    mid = sum(ratios) / len(ratios)
    if (hi - lo) / mid * 100.0 > tolerance_pct or abs(mid - 1.0) * 100.0 <= tolerance_pct:
        return None
    return mid


def bar_disagrees_with_tape(bar: OHLCV, totals: OHLCV, tolerance_pct: float = 0.05) -> str | None:
    """Why a daily bar cannot be the settled session, or None if it can be.

    Compares the aggregated candle against the tape's own view of the same day.
    A settled bar must close where the last trade printed and must span at
    least the range that actually traded. When it does not, the candle is still
    catching up -- its close is not a close, and its volume is a fraction of
    the day's.

    Volume is deliberately not compared for equality: the two feeds have been
    seen to count in different units, and a units mismatch is not evidence
    about settlement. A bar covering less range than the tape is.
    """
    def differs(a: float | None, b: float | None) -> bool:
        if a is None or b is None:
            return False
        return abs(a - b) > abs(b) * tolerance_pct / 100.0

    if tape_adjustment_factor(bar, totals) is not None:
        return None          # the vendor already adjusted the bar for an ex-date; the tape is not
    if differs(bar.close, totals.close):
        return f"bar closes at {bar.close:,.0f} but the tape's last match is {totals.close:,.0f}"
    if totals.high is not None and bar.high is not None and bar.high < totals.high * (1 - tolerance_pct / 100):
        return f"bar high {bar.high:,.0f} is below the session high {totals.high:,.0f}"
    if totals.low is not None and bar.low is not None and bar.low > totals.low * (1 + tolerance_pct / 100):
        return f"bar low {bar.low:,.0f} is above the session low {totals.low:,.0f}"
    return None


def suspicious_gaps(
    bars: Iterable[OHLCV],
    threshold_pct: float | None = None,
    exchange: str | None = None,
) -> list[tuple[date, float]]:
    """Overnight moves too large to be a price move on that exchange.

    A session cannot travel further than the exchange's daily band, so a gap
    beyond it is either a genuine corporate action or a series that has not
    been adjusted for one. The technical engine would read the second case as
    a breakdown, which is why it is worth surfacing before the data is trusted
    rather than after a signal is published.

    The band is per exchange -- 7% on HOSE, 10% on HNX, 15% on UPCoM. Assuming
    one number for all three reports every busy HNX session as a suspected
    corporate action. Pass `exchange` to resolve it, or `threshold_pct` to set
    it directly. With neither, and no way to tell, nothing is flagged: a
    detector that does not know the rules should stay quiet rather than guess.
    """
    if threshold_pct is None:
        band = price_limit_pct(exchange) if exchange else None
        if band is None:
            return []
        # A hair above the band, so a limit-up session that rounds to the
        # boundary is not reported as a corporate action.
        threshold_pct = band + 0.5

    out: list[tuple[date, float]] = []
    previous: OHLCV | None = None
    for bar in bars:
        if previous is not None and previous.close and bar.close:
            change = (bar.close - previous.close) / previous.close * 100.0
            if abs(change) >= threshold_pct:
                out.append((bar.trade_date, round(change, 2)))
        previous = bar
    return out
