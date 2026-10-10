"""Incremental price backfill for the screening universe and benchmarks.

`collect_market_data` in runner.py asks the registry for a provider per
ticker, which is right for a five-name morning run and wrong for 1,663 names:
every call builds a fresh HTTP client, so the client-side rate limiter never
sees more than one request and the vendor's limit is left to find out the hard
way. This module holds one provider for the whole run.

Two rules it adds for a long-lived store:

* **Resume, don't restart.** Each ticker fetches from a little before its last
  stored bar. An interrupted run picks up where it stopped.
* **An adjusted series can change its past.** DNSE's history is adjusted for
  share-based corporate actions (docs/DNSE.md), so after an ex-date every
  earlier bar is rescaled. Appending new bars to old unscaled ones would splice
  two price scales into one series. The fetch therefore overlaps a few stored
  sessions; if any stored close no longer matches, the ticker's full history is
  refetched and replaced.

Fixture data is never written, a failure marks the series stale and moves on,
and nothing is interpolated.

`backfill --deepen` (2026-10-06) reads the full window once for series stored
with a shorter one -- see `deepen_history`.
"""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Sequence

from src.collectors.base import MarketDataProvider, OHLCV, ProviderError
from src.collectors.market_store import (
    DATASET_INDEX_DAILY,
    DATASET_PRICES_DAILY,
    mark_freshness,
    record_source_outcome,
    upsert_daily_bars,
    upsert_index_bars,
)
from src.collectors.registry import ProviderLoadError, instantiate
from src.collectors.runner import CollectionOutcome, CollectionReport, _record_run, _verified_daily_bars
from src.config import load_config
from src.database import connect

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class BackfillSettings:
    history_days: int
    overlap_days: int
    readjust_tolerance_pct: float
    gap_check_sessions: int = 300

    @classmethod
    def from_config(cls) -> "BackfillSettings":
        raw = load_config().thresholds.get("collection", {}) or {}
        return cls(
            history_days=int(raw.get("backfill_history_days", 1830)),
            overlap_days=int(raw.get("backfill_overlap_days", 15)),
            readjust_tolerance_pct=float(raw.get("readjust_tolerance_pct", 0.5)),
            gap_check_sessions=int(raw.get("gap_check_sessions", 300)),
        )


def live_market_provider() -> MarketDataProvider:
    """The highest-priority enabled, credentialed, non-fixture market source."""
    errors: list[str] = []
    for spec in load_config().sources.for_kind("market_data"):
        if spec.id.startswith("fixture"):
            continue
        if not spec.has_credentials:
            errors.append(f"{spec.id}: missing ${spec.auth_env}")
            continue
        try:
            return instantiate(spec)  # type: ignore[return-value]
        except ProviderLoadError as exc:
            errors.append(f"{spec.id}: {exc}")
    raise ProviderError("no live market-data source is available -- " + ("; ".join(errors) or "none enabled"))


def _stored_closes(table: str, symbol: str, since: date, path: Path | None) -> dict[date, float]:
    column = "ticker" if table == "prices_daily" else "symbol"
    with connect(path) as conn:
        rows = conn.execute(
            f"SELECT trade_date, close FROM {table} WHERE {column} = ? AND trade_date >= ?",  # noqa: S608
            (symbol.upper(), since.isoformat()),
        ).fetchall()
    return {date.fromisoformat(r[0]): float(r[1]) for r in rows if r[1] is not None}


def _latest(table: str, symbol: str, path: Path | None) -> date | None:
    column = "ticker" if table == "prices_daily" else "symbol"
    with connect(path) as conn:
        row = conn.execute(
            f"SELECT MAX(trade_date) FROM {table} WHERE {column} = ?",  # noqa: S608
            (symbol.upper(),),
        ).fetchone()
    return date.fromisoformat(row[0]) if row and row[0] else None


def _history_rewritten(
    fetched: Sequence[OHLCV], stored: dict[date, float], tolerance_pct: float
) -> str | None:
    """Why the vendor's past no longer matches ours, or None if it still does."""
    for bar in fetched:
        old = stored.get(bar.trade_date)
        if old is None or bar.close is None or old == 0:
            continue
        drift = abs(bar.close - old) / old * 100.0
        if drift > tolerance_pct:
            return (f"stored close {old:,.0f} on {bar.trade_date} is now {bar.close:,.0f} "
                    f"({drift:.1f}%) -- history was re-adjusted")
    return None


def _readjust_factor(fetched: Sequence[OHLCV], stored: dict[date, float]) -> float | None:
    """Median new / old close over the sessions both hold: the vendor's adjustment factor."""
    ratios = sorted(b.close / stored[b.trade_date] for b in fetched
                    if b.close and stored.get(b.trade_date))
    return ratios[len(ratios) // 2] if ratios else None


def _adjustment_boundary(fetched: Sequence[OHLCV], stored: dict[date, float], factor: float,
                         notes: Sequence[str] | None) -> tuple[float, date] | None:
    """(factor, last old-scale session) of a rescaled history, or None when it is not a rescale.

    DNSE rescales the evening before the ex-date. Read that evening, today's bar
    is already rescaled while the tape is not ("already adjusted" note from
    `_verified_daily_bars`): today is the last old-scale session. Read later,
    the ex-date's bar was stored on the new scale already, so the last old-scale
    session is the newest stored one that moved by the factor.
    """
    if abs(factor - 1.0) < 0.002:
        return None
    if notes and any("already adjusted" in n for n in notes) and fetched and fetched[-1].trade_date not in stored:
        return factor, fetched[-1].trade_date
    moved = [b.trade_date for b in fetched if stored.get(b.trade_date) and b.close
             and abs(b.close / stored[b.trade_date] - factor) <= abs(factor) * 0.005]
    return (factor, max(moved)) if moved else None


def _backfill_one(
    symbol: str,
    *,
    table: str,
    dataset: str,
    fetch: Callable[[date, date], list[OHLCV]],
    writer: Callable[..., int],
    exchange: str | None,
    end: date,
    settings: BackfillSettings,
    path: Path | None,
    notes: list[str] | None = None,
) -> CollectionOutcome:
    outcome = CollectionOutcome(scope=symbol, dataset=dataset, source_id=None)
    adjustment: tuple[float, date] | None = None
    last = _latest(table, symbol, path)
    full_start = end - timedelta(days=settings.history_days)
    start = full_start if last is None else max(full_start, last - timedelta(days=settings.overlap_days))

    try:
        bars = fetch(start, end)
        # Only equity history is rescaled by corporate actions. index_daily also
        # holds breadth that this feed never had, so it is never deleted.
        if last is not None and table == "prices_daily":
            reason = _history_rewritten(
                bars, _stored_closes(table, symbol, start, path), settings.readjust_tolerance_pct
            )
            if reason:
                log.info("%s: %s; refetching full history", symbol, reason)
                outcome.note = reason
                old = _stored_closes(table, symbol, start, path)
                factor = _readjust_factor(bars, old)
                adjustment = _adjustment_boundary(bars, old, factor, notes) if factor else None
                bars = fetch(full_start, end)
                with connect(path) as conn:
                    conn.execute("DELETE FROM prices_daily WHERE ticker = ?", (symbol.upper(),))
    except ProviderError as exc:
        outcome.stale = True
        outcome.note = f"{type(exc).__name__}: {exc}"
        mark_freshness(dataset, symbol, stale=True, path=path, note=f"không tải được dữ liệu: {exc}"[:300])
        return outcome

    outcome.source_id = bars[0].provenance.source_id if bars else None
    outcome.bars = len(bars)
    if not bars:
        outcome.stale = True
        outcome.note = "vendor returned nothing for the requested range"
        mark_freshness(dataset, symbol, stale=True, path=path, note="nguồn không trả dữ liệu cho khoảng thời gian yêu cầu")
        return outcome

    writer(bars, path=path)
    outcome.persisted = True
    if adjustment:
        # Exchange prices stored for the old-scale sessions (reference, ceiling, floor) are read
        # through this factor from now on, so a session's % against its reference stays the board's.
        from src.collectors.refprices import record_adjustment  # noqa: PLC0415

        record_adjustment(symbol, adjustment[0], adjustment[1], outcome.source_id or "dnse_openapi", path)

    # Gap check over the recent stored series, not just the new slice: a join
    # between old and new bars is exactly where a scale mismatch would show.
    # Only the window the screener reads is checked -- older sessions may
    # predate a move between exchanges (VIX traded on HNX's 10% band before
    # HOSE's 7%), and today's band says nothing about them.
    if exchange:
        outcome.gaps = recent_gaps(symbol, exchange, settings.gap_check_sessions, path)

    if outcome.gaps:
        # Real bars, kept -- but flagged so the screener does not trust them.
        outcome.stale = True
        outcome.note = (f"{len(outcome.gaps)} overnight move(s) beyond the {exchange} band, latest "
                        f"{outcome.gaps[-1][0]} ({outcome.gaps[-1][1]:+.1f}%) -- unadjusted corporate "
                        "action or re-listing; excluded from screening until checked")
        mark_freshness(dataset, symbol, stale=True, path=path, note=gap_note(outcome.gaps, exchange))
    else:
        mark_freshness(dataset, symbol, stale=False, path=path)
    if outcome.source_id:
        record_source_outcome(outcome.source_id, ok=True, path=path)
    return outcome


def close_to_close_bounds(exchange: str) -> tuple[float, float] | None:
    """Largest possible close-to-close move (down %, up %) for one session.

    HOSE and HNX set the reference price to the previous close, so one session
    moves at most the band (plus half a point for tick rounding). UPCoM's
    reference is the previous session's VWAP, not its close. Today's close can
    sit a full band from that VWAP, and the previous close could itself have
    been a full band from the day before's reference, so close-to-close spans
    (1-b)^2/(1+b)-1 .. (1+b)^2/(1-b)-1: -37.2% .. +55.6% for b = 15%. Loose, but
    anything outside it cannot be one session of trading.
    """
    from src.market_calendar import price_limit_pct

    band = price_limit_pct(exchange)
    if band is None:
        return None
    b = band / 100.0
    if exchange.upper() == "UPCOM":
        return ((1 - (1 - b) ** 2 / (1 + b)) * 100.0, ((1 + b) ** 2 / (1 - b) - 1) * 100.0)
    return (band + 0.5, band + 0.5)


def recent_gaps(symbol: str, exchange: str, sessions: int, path: Path | None = None) -> list[tuple[date, float]]:
    """Overnight moves in the last `sessions` bars that one session cannot produce.

    Up to and including the day the stock listed on its current exchange
    (`tickers.listed_date`), it traded elsewhere -- usually UPCoM, whose band is
    the widest -- so those sessions are judged by UPCoM's bounds. Judged by
    today's band, MCH (UPCoM -> HOSE 25/12/2025), BVB and VVS were flagged for
    ordinary UPCoM sessions (tester report 2026-10-11, B07).
    """
    bounds = close_to_close_bounds(exchange)
    if bounds is None:
        return []
    down, up = bounds
    wide_down, wide_up = close_to_close_bounds("UPCOM") or bounds
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT trade_date, close FROM ("
            "  SELECT trade_date, close FROM prices_daily WHERE ticker = ?"
            "  ORDER BY trade_date DESC LIMIT ?) ORDER BY trade_date",
            (symbol.upper(), sessions),
        ).fetchall()
        listed = conn.execute("SELECT listed_date FROM tickers WHERE ticker = ?", (symbol.upper(),)).fetchone()
    listed_on = str(listed[0])[:10] if listed and listed[0] else None
    out: list[tuple[date, float]] = []
    for (_, prev), (d, cur) in zip(rows, rows[1:]):
        if prev and cur:
            change = (cur - prev) / prev * 100.0
            lo, hi = (wide_down, wide_up) if listed_on and d <= listed_on else (down, up)
            if change <= -lo or change >= hi:
                out.append((date.fromisoformat(d), round(change, 2)))
    return out


def gap_note(gaps: list[tuple[date, float]], exchange: str) -> str:
    """Why a series is flagged, in Vietnamese, for the dashboard."""
    day, change = gaps[-1]
    return (f"{len(gaps)} phiên biến động vượt biên độ {exchange}, gần nhất {day:%d/%m/%Y} ({change:+.1f}%) — "
            "có thể là quyền chưa được điều chỉnh hoặc lần lên sàn mới; chưa đưa vào bộ lọc cho tới khi kiểm tra")


def recheck_gaps(settings: BackfillSettings | None = None, path: Path | None = None) -> dict[str, int]:
    """Recompute the gap flag for every stored series, without the network."""
    settings = settings or BackfillSettings.from_config()
    flagged = cleared = 0
    for ticker, exchange in universe_tickers(path):
        with connect(path) as conn:
            has = conn.execute("SELECT 1 FROM prices_daily WHERE ticker = ? LIMIT 1", (ticker,)).fetchone()
        if not has:
            continue
        gaps = recent_gaps(ticker, exchange, settings.gap_check_sessions, path)
        if gaps:
            mark_freshness(DATASET_PRICES_DAILY, ticker, stale=True, path=path, note=gap_note(gaps, exchange))
            flagged += 1
        else:
            mark_freshness(DATASET_PRICES_DAILY, ticker, stale=False, path=path)
            cleared += 1
    return {"flagged": flagged, "clean": cleared}


def universe_tickers(path: Path | None = None) -> list[tuple[str, str]]:
    """(ticker, exchange) for every active ticker, universe and watchlist alike."""
    with connect(path) as conn:
        rows = conn.execute("SELECT ticker, exchange FROM tickers WHERE active = 1 ORDER BY ticker").fetchall()
    return [(r[0], r[1]) for r in rows]


HISTORY_MARKER = Path(__file__).resolve().parents[2] / "data" / "history_depth.json"


def _load_marker(marker: Path) -> dict[str, dict[str, Any]]:
    try:
        return json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def deepen_history(
    tickers: Sequence[tuple[str, str]],
    provider: Any | None = None,
    settings: BackfillSettings | None = None,
    end: date | None = None,
    marker: Path = HISTORY_MARKER,
    progress: Callable[[int, int, CollectionOutcome], None] | None = None,
    path: Path | None = None,
) -> CollectionReport:
    """Read the whole `backfill_history_days` window once for series stored with less.

    The window was 5 years until 2026-10-06 and the daily backfill only reads
    forward, so older years never arrive on their own. Each shallow series is
    read in full (one request) and replaces the stored one: old and new bars
    then share the vendor's current adjustment for corporate actions. A read
    that does not reach the last stored session, or holds fewer bars than are
    stored, is not used -- the stored series stays as it was.

    Tickers checked against the current window are noted in `marker` and not
    read again (a recent listing has nothing older to fetch).
    """
    settings = settings or BackfillSettings.from_config()
    provider = provider or live_market_provider()
    end = end or datetime.now(timezone.utc).date()
    full_start = end - timedelta(days=settings.history_days)
    done_marks = _load_marker(marker)
    report = CollectionReport(run_id=uuid.uuid4().hex[:16], started_at=datetime.now(timezone.utc))

    def note(ticker: str, first: str | None) -> None:
        done_marks[ticker] = {"history_days": settings.history_days, "from": first, "checked": end.isoformat()}

    for k, (ticker, exchange) in enumerate(tickers, 1):
        outcome = CollectionOutcome(scope=ticker, dataset=DATASET_PRICES_DAILY, source_id=None)
        with connect(path) as conn:
            first, last, count = conn.execute(
                "SELECT MIN(trade_date), MAX(trade_date), COUNT(*) FROM prices_daily WHERE ticker = ?", (ticker,)).fetchone()
        if not count:
            outcome.note = "chưa có giá -- để backfill thường tải"
        elif int((done_marks.get(ticker) or {}).get("history_days", 0)) >= settings.history_days:
            outcome.note = "đã kiểm với khoảng này"
        elif date.fromisoformat(first) <= full_start + timedelta(days=60):
            # Within two months of the window's start: as deep as the vendor goes (DNSE from 2015-09-15).
            outcome.note = "đã đủ sâu"
            note(ticker, first)
        else:
            notes: list[str] = []
            try:
                bars = _verified_daily_bars(provider, ticker, full_start, end, notes=notes)
            except ProviderError as exc:
                outcome.stale, outcome.note = True, f"{type(exc).__name__}: {exc}"
                bars = None
            if bars is not None:
                outcome.bars = len(bars)
                fetched_last = bars[-1].trade_date.isoformat() if bars else None
                if not bars or fetched_last < last or len(bars) < count:
                    outcome.stale = True
                    outcome.note = (f"không dùng lượt đọc: {len(bars)} phiên tới {fetched_last}, "
                                    f"đang lưu {count} phiên tới {last}")
                elif bars[0].trade_date.isoformat() >= first:
                    outcome.note = f"DNSE không có lịch sử trước {first}"
                    note(ticker, first)
                else:
                    upsert_daily_bars(bars, path=path)
                    keep = [b.trade_date.isoformat() for b in bars]
                    with connect(path) as conn:
                        # Stored sessions the fresh read no longer has go too: the series is the vendor's, whole.
                        conn.execute("CREATE TEMP TABLE IF NOT EXISTS _keep (d TEXT PRIMARY KEY)")
                        conn.execute("DELETE FROM _keep")
                        conn.executemany("INSERT OR IGNORE INTO _keep VALUES (?)", [(d,) for d in keep])
                        conn.execute("DELETE FROM prices_daily WHERE ticker = ? AND trade_date NOT IN (SELECT d FROM _keep)",
                                     (ticker,))
                    outcome.persisted = True
                    outcome.source_id = bars[0].provenance.source_id
                    outcome.note = f"thêm lịch sử từ {bars[0].trade_date} (trước đây từ {first})"
                    note(ticker, bars[0].trade_date.isoformat())
                    if exchange:
                        outcome.gaps = recent_gaps(ticker, exchange, settings.gap_check_sessions, path)
                    mark_freshness(DATASET_PRICES_DAILY, ticker, stale=bool(outcome.gaps), path=path,
                                   note=gap_note(outcome.gaps, exchange) if outcome.gaps and exchange else None)
        report.outcomes.append(outcome)
        if progress:
            progress(k, len(tickers), outcome)
        if k % 10 == 0 or k == len(tickers):
            marker.parent.mkdir(parents=True, exist_ok=True)
            marker.write_text(json.dumps(done_marks, ensure_ascii=False, indent=0), encoding="utf-8")
    report.finished_at = datetime.now(timezone.utc)
    _record_run(report, job_type="BACKFILL", path=path)
    return report


def run_backfill(
    tickers: Sequence[tuple[str, str]] | None = None,
    benchmarks: Sequence[str] | None = None,
    end: date | None = None,
    provider: Any | None = None,
    settings: BackfillSettings | None = None,
    progress: Callable[[int, int, CollectionOutcome], None] | None = None,
    path: Path | None = None,
) -> CollectionReport:
    cfg = load_config()
    settings = settings or BackfillSettings.from_config()
    provider = provider or live_market_provider()
    end = end or datetime.now(timezone.utc).date()
    scope = list(tickers) if tickers is not None else universe_tickers(path)
    index_scope = list(benchmarks) if benchmarks is not None else list(cfg.watchlist.benchmarks)

    report = CollectionReport(run_id=uuid.uuid4().hex[:16], started_at=datetime.now(timezone.utc))
    total = len(index_scope) + len(scope)
    done = 0

    for symbol in index_scope:
        outcome = _backfill_one(
            symbol, table="index_daily", dataset=DATASET_INDEX_DAILY,
            fetch=lambda s, e, sym=symbol: provider.get_index_ohlcv(sym, s, e),
            writer=upsert_index_bars, exchange=None, end=end, settings=settings, path=path,
        )
        report.outcomes.append(outcome)
        done += 1
        if progress:
            progress(done, total, outcome)

    for ticker, exchange in scope:
        notes: list[str] = []
        outcome = _backfill_one(
            ticker, table="prices_daily", dataset=DATASET_PRICES_DAILY,
            fetch=lambda s, e, t=ticker, n=notes: _verified_daily_bars(provider, t, s, e, notes=n),
            writer=upsert_daily_bars, exchange=exchange, end=end, settings=settings, path=path, notes=notes,
        )
        if notes and not outcome.note:
            outcome.note = notes[0]
        report.outcomes.append(outcome)
        done += 1
        if progress:
            progress(done, total, outcome)

    report.finished_at = datetime.now(timezone.utc)
    _record_run(report, job_type="BACKFILL", path=path)
    return report
