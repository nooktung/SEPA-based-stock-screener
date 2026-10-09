"""Market data collection run.

Walks the watchlist, asks the registry for whichever market-data source is
healthy, and writes what comes back. The rules it enforces are the ones the
rest of the system depends on:

* Fixture data never reaches the database. A fixture is a development
  convenience; persisting it would put synthetic bars behind the same API as
  real ones and nothing downstream could tell them apart afterwards.
* A failure marks the dataset stale and continues to the next ticker. One
  unreachable symbol does not abandon the run, and nothing is interpolated to
  fill the hole.
* Every run is recorded in collection_runs with its per-source tally, so a
  degraded morning is visible after the fact rather than inferred.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Sequence

from src.collectors.base import OHLCV, ProviderError
from src.collectors.market_store import (
    bar_disagrees_with_tape,
    tape_adjustment_factor,
    DATASET_FLOW_DAILY,
    DATASET_INDEX_DAILY,
    DATASET_PRICES_DAILY,
    mark_freshness,
    record_source_outcome,
    stored_flow_observations,
    suspicious_gaps,
    upsert_daily_bars,
    upsert_flows,
    upsert_index_bars,
)
from src.collectors.registry import fetch_with_fallback
from src.config import load_config
from src.database import connect
from src.market_calendar import (
    missing_runs,
    now_vn,
    session_close_at,
    session_is_complete,
    sessions_between,
    to_vn,
)

log = logging.getLogger(__name__)

DEFAULT_LOOKBACK_DAYS = 400  # ~18 trading months: enough for a 200-day average
# Flow costs one request per ticker per session, so the default window is the
# 20-session lookback the regime classifier asks for in thresholds.yaml, plus
# room for weekends and a holiday.
DEFAULT_FLOW_LOOKBACK_DAYS = 35


@dataclass
class CollectionOutcome:
    scope: str                    # ticker or benchmark symbol
    dataset: str
    source_id: str | None
    bars: int = 0
    persisted: bool = False
    stale: bool = False
    note: str = ""
    gaps: list[tuple[date, float]] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.persisted and not self.stale


@dataclass
class CollectionReport:
    run_id: str
    started_at: datetime
    finished_at: datetime | None = None
    outcomes: list[CollectionOutcome] = field(default_factory=list)

    @property
    def bars_written(self) -> int:
        return sum(o.bars for o in self.outcomes if o.persisted)

    @property
    def ok_scopes(self) -> list[CollectionOutcome]:
        return [o for o in self.outcomes if o.ok]

    @property
    def failed_scopes(self) -> list[CollectionOutcome]:
        return [o for o in self.outcomes if not o.ok]

    @property
    def sources_used(self) -> set[str]:
        return {o.source_id for o in self.outcomes if o.source_id}

    @property
    def fixture_only(self) -> bool:
        """True when nothing better than a fixture answered."""
        used = self.sources_used
        return bool(used) and all(s.startswith("fixture") for s in used)

    @property
    def status(self) -> str:
        if not self.ok_scopes:
            return "FAILED"
        return "OK" if not self.failed_scopes else "PARTIAL"


def _verified_daily_bars(
    provider,
    ticker: str,
    start: date,
    end: date,
    now: datetime | None = None,
    notes: list[str] | None = None,
) -> list[OHLCV]:
    """Daily bars, with the current session dropped unless it has settled.

    A vendor's daily candle is not final the moment the market closes -- it can
    lag the tape by an undocumented interval, during which its close is not the
    close and its volume is a fraction of the day. Since the lag is not
    knowable, the bar is checked against the tape rather than waited out.
    """
    bars = provider.get_daily_ohlcv(ticker, start, end)
    if not bars:
        return bars

    last = bars[-1]
    if last.trade_date != to_vn(now or now_vn()).date():
        return bars

    try:
        totals = provider.get_session_totals(ticker)
    except ProviderError as exc:
        if notes is not None:
            notes.append(f"today's bar could not be verified against the tape ({exc})")
        return bars

    if totals is None:
        return bars

    factor = tape_adjustment_factor(last, totals)
    if factor is not None and notes is not None:
        notes.append(f"today's bar is already adjusted x{factor:.4f} for an ex-date (the tape is not); kept")
    disagreement = bar_disagrees_with_tape(last, totals)
    if disagreement:
        if notes is not None:
            notes.append(f"today's bar has not settled -- {disagreement}; excluded")
        log.warning("%s: %s", ticker, disagreement)
        return bars[:-1]
    return bars


def collect_market_data(
    lookback_days: int = DEFAULT_LOOKBACK_DAYS,
    tickers: Sequence[str] | None = None,
    benchmarks: Sequence[str] | None = None,
    end: date | None = None,
    persist: bool = True,
    job_type: str = "MORNING",
    now: datetime | None = None,
    path: Path | None = None,
) -> CollectionReport:
    cfg = load_config()
    end_date = end or datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=lookback_days)
    scope_tickers = list(tickers) if tickers is not None else list(cfg.watchlist.active_tickers)
    scope_benchmarks = list(benchmarks) if benchmarks is not None else list(cfg.watchlist.benchmarks)

    report = CollectionReport(run_id=uuid.uuid4().hex[:16], started_at=datetime.now(timezone.utc))

    for ticker in scope_tickers:
        notes: list[str] = []
        outcome = _collect_one(
            scope=ticker,
            dataset=DATASET_PRICES_DAILY,
            capability="ohlcv_daily",
            fetch=lambda provider, t=ticker, n=notes: _verified_daily_bars(
                provider, t, start_date, end_date, now=now, notes=n
            ),
            writer=upsert_daily_bars,
            persist=persist,
            path=path,
            exchange=_exchange_of(cfg, ticker),
        )
        if notes and not outcome.note:
            outcome.note = notes[0]
        report.outcomes.append(outcome)

    for symbol in scope_benchmarks:
        report.outcomes.append(
            _collect_one(
                scope=symbol,
                dataset=DATASET_INDEX_DAILY,
                capability="index_ohlcv",
                fetch=lambda provider, s=symbol: provider.get_index_ohlcv(s, start_date, end_date),
                writer=upsert_index_bars,
                persist=persist,
                path=path,
            )
        )

    report.finished_at = datetime.now(timezone.utc)
    if persist:
        _record_run(report, job_type=job_type, path=path)
    return report


def collect_foreign_flows(
    lookback_days: int = DEFAULT_FLOW_LOOKBACK_DAYS,
    tickers: Sequence[str] | None = None,
    end: date | None = None,
    persist: bool = True,
    refetch_stored: bool = False,
    now: datetime | None = None,
    path: Path | None = None,
) -> CollectionReport:
    """Collect foreign-investor flow for each ticker.

    The endpoint serves one session per request, so this asks only for sessions
    it does not already hold. The most recent stored session is always refetched
    when it has not closed yet: an intraday snapshot is a running total, and
    treating it as final would freeze the day's flow at whatever it was at
    lunchtime.
    """
    cfg = load_config()
    end_date = end or datetime.now(timezone.utc).date()
    start_date = end_date - timedelta(days=lookback_days)
    scope_tickers = list(tickers) if tickers is not None else list(cfg.watchlist.active_tickers)

    report = CollectionReport(run_id=uuid.uuid4().hex[:16], started_at=datetime.now(timezone.utc))

    for ticker in scope_tickers:
        already: set[date] = set()
        if persist and not refetch_stored:
            # Held *and* read after that session closed. A record captured
            # intraday is a running total; the closing bell does not make it
            # final, another read does.
            already = {
                day
                for day, observed in stored_flow_observations(
                    "TICKER", ticker, start_date, end_date, path=path
                ).items()
                if session_is_complete(day, now=now) and observed >= session_close_at(day)
            }

        runs = missing_runs(sessions_between(start_date, end_date), already)
        if not runs:
            report.outcomes.append(
                CollectionOutcome(
                    scope=ticker, dataset=DATASET_FLOW_DAILY, source_id=None,
                    persisted=persist, note=f"up to date ({len(already)} sessions held)",
                )
            )
            continue

        report.outcomes.append(
            _collect_one(
                scope=ticker,
                dataset=DATASET_FLOW_DAILY,
                capability="foreign_net",
                fetch=lambda provider, t=ticker, r=runs: [
                    record
                    for run_start, run_end in r
                    for record in provider.get_flows("TICKER", t, run_start, run_end)
                ],
                writer=upsert_flows,
                persist=persist,
                path=path,
                kind="flow",
            )
        )

    report.finished_at = datetime.now(timezone.utc)
    if persist:
        _record_run(report, job_type="MORNING", path=path)
    return report


def _collect_one(
    scope: str,
    dataset: str,
    capability: str,
    fetch,
    writer,
    persist: bool,
    path: Path | None,
    kind: str = "market_data",
    exchange: str | None = None,
) -> CollectionOutcome:
    result = fetch_with_fallback(kind, fetch, capability=capability)
    outcome = CollectionOutcome(scope=scope, dataset=dataset, source_id=result.source_id)

    if result.stale or result.value is None:
        outcome.stale = True
        outcome.note = "; ".join(f"{k}: {v}" for k, v in result.errors.items()) or "no source answered"
        if persist:
            mark_freshness(dataset, scope, stale=True, path=path)
        log.warning("%s: no %s data (%s)", scope, kind, outcome.note)
        return outcome

    records = list(result.value)
    outcome.bars = len(records)
    if kind == "market_data":
        # Only equities have a price band. An index has none, so gap detection
        # is meaningless there and is skipped rather than run with a made-up
        # threshold.
        outcome.gaps = suspicious_gaps(records, exchange=exchange)

    if result.from_fixture_only:
        outcome.stale = True
        outcome.note = "fixture data -- not persisted, no real source is configured"
        if persist:
            mark_freshness(dataset, scope, stale=True, path=path)
        return outcome

    if not records:
        # For prices, an empty answer over a long window is a failure. For
        # flow, the window is a handful of days and may legitimately contain
        # only holidays -- which this system does not have a calendar for, so
        # it says "nothing came back" rather than guessing which case it is.
        outcome.stale = kind == "market_data"
        outcome.note = f"{result.source_id} returned nothing for the requested range"
        if persist and outcome.stale:
            mark_freshness(dataset, scope, stale=True, path=path)
        return outcome

    if persist:
        writer(records, path=path)
        mark_freshness(dataset, scope, stale=False, path=path)
        if result.source_id:
            record_source_outcome(result.source_id, ok=True, path=path)
    outcome.persisted = persist
    if outcome.gaps:
        outcome.note = (
            f"{len(outcome.gaps)} overnight move(s) beyond the {exchange} daily band -- "
            "verify the series is adjusted for corporate actions before trusting the technicals"
        )
    return outcome


def _exchange_of(cfg, ticker: str, path: Path | None = None) -> str | None:
    for entry in cfg.watchlist.entries:
        if entry.ticker == ticker.upper():
            return entry.exchange
    # Screening-universe tickers are not in the watchlist; the universe sync
    # stores their exchange in `tickers`.
    try:
        with connect(path) as conn:
            row = conn.execute("SELECT exchange FROM tickers WHERE ticker = ?", (ticker.upper(),)).fetchone()
    except Exception:  # noqa: BLE001 - a missing DB means "unknown venue", which flags nothing
        return None
    return row[0] if row else None


def _record_run(report: CollectionReport, job_type: str, path: Path | None) -> None:
    failures = report.failed_scopes
    detail = "; ".join(f"{o.scope}: {o.note}" for o in failures)[:2000] or None
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO collection_runs
                (run_id, started_at, finished_at, job_type, status, sources_ok, sources_failed, failure_detail)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                report.run_id,
                report.started_at.isoformat(timespec="seconds"),
                (report.finished_at or datetime.now(timezone.utc)).isoformat(timespec="seconds"),
                job_type,
                report.status,
                len(report.ok_scopes),
                len(failures),
                detail,
            ),
        )
    log.info("collection run %s finished: %s", report.run_id, report.status)


__all__ = [
    "CollectionOutcome",
    "CollectionReport",
    "collect_market_data",
    "collect_foreign_flows",
    "DEFAULT_LOOKBACK_DAYS",
    "DEFAULT_FLOW_LOOKBACK_DAYS",
]
