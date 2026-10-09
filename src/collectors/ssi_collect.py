"""Collect SSI FastConnect Data end-of-day snapshots into `ssi_stock_daily` and
`ssi_index_daily` (a cross-check source: the DNSE tables stay the primary series).

One `DailyStockPrice` call per exchange per day covers every listed security; only
tickers in the project's universe (`tickers`) are kept. Indices are fetched once
per index for the whole date range. A failed call is reported and skipped -- no
value is ever filled in for it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Iterable

from src.collectors.base import ProviderError
from src.collectors.providers.ssi_fcdata_provider import SsiFcDataProvider, SsiIndexDay, SsiStockDay
from src.database import connect

MARKETS = ("HOSE", "HNX", "UPCOM")
INDICES = ("VNINDEX", "HNXINDEX", "UPCOMINDEX", "VN30")

STOCK_COLUMNS = ("trade_date", "ticker", "market", "ref_price", "ceiling_price", "floor_price", "open", "high", "low",
                 "close", "close_adjusted", "avg_price", "match_volume", "match_value", "deal_volume", "deal_value",
                 "foreign_buy_volume", "foreign_sell_volume", "foreign_buy_value", "foreign_sell_value", "foreign_room",
                 "source_id", "observed_at")
INDEX_COLUMNS = ("trade_date", "index_id", "value", "change", "change_pct", "match_volume", "match_value", "deal_volume",
                 "deal_value", "total_volume", "total_value", "advances", "declines", "unchanged", "ceilings", "floors",
                 "session", "source_id", "observed_at")


@dataclass
class SsiCollectReport:
    stock_rows: dict[str, int] = field(default_factory=dict)      # date -> rows kept
    index_rows: dict[str, int] = field(default_factory=dict)      # index -> rows
    skipped_outside_universe: int = 0
    errors: list[str] = field(default_factory=list)


def _observed(rec) -> str:
    return rec.provenance.observed_at.astimezone(timezone.utc).isoformat()


def store_stock_days(rows: Iterable[SsiStockDay], path: Path | None = None) -> int:
    data = [(r.trade_date.isoformat(), r.ticker, r.market, r.ref_price, r.ceiling_price, r.floor_price, r.open, r.high,
             r.low, r.close, r.close_adjusted, r.avg_price, r.match_volume, r.match_value, r.deal_volume, r.deal_value,
             r.foreign_buy_volume, r.foreign_sell_volume, r.foreign_buy_value, r.foreign_sell_value, r.foreign_room,
             r.provenance.source_id, _observed(r)) for r in rows]
    if not data:
        return 0
    marks = ", ".join("?" * len(STOCK_COLUMNS))
    updates = ", ".join(f"{c}=excluded.{c}" for c in STOCK_COLUMNS[2:])
    with connect(path) as conn:
        conn.executemany(f"INSERT INTO ssi_stock_daily ({', '.join(STOCK_COLUMNS)}) VALUES ({marks}) "
                         f"ON CONFLICT(trade_date, ticker) DO UPDATE SET {updates}", data)
    return len(data)


def store_index_days(rows: Iterable[SsiIndexDay], path: Path | None = None) -> int:
    data = [(r.trade_date.isoformat(), r.index_id, r.value, r.change, r.change_pct, r.match_volume, r.match_value,
             r.deal_volume, r.deal_value, r.total_volume, r.total_value, r.advances, r.declines, r.unchanged,
             r.ceilings, r.floors, r.session, r.provenance.source_id, _observed(r)) for r in rows]
    if not data:
        return 0
    marks = ", ".join("?" * len(INDEX_COLUMNS))
    updates = ", ".join(f"{c}=excluded.{c}" for c in INDEX_COLUMNS[2:])
    with connect(path) as conn:
        conn.executemany(f"INSERT INTO ssi_index_daily ({', '.join(INDEX_COLUMNS)}) VALUES ({marks}) "
                         f"ON CONFLICT(trade_date, index_id) DO UPDATE SET {updates}", data)
    return len(data)


def weekdays(end: date, days: int) -> list[date]:
    return [d for d in (end - timedelta(days=k) for k in range(days, -1, -1)) if d.weekday() < 5]


def collect_ssi(provider: SsiFcDataProvider, days: int = 5, end: date | None = None,
                path: Path | None = None) -> SsiCollectReport:
    end = end or datetime.now(timezone(timedelta(hours=7))).date()
    report = SsiCollectReport()
    with connect(path) as conn:
        universe = {r[0] for r in conn.execute("SELECT ticker FROM tickers")}
    for day in weekdays(end, days):
        kept = 0
        for market in MARKETS:
            try:
                rows = provider.daily_stock_prices(day, market)
            except ProviderError as exc:
                report.errors.append(f"{day} {market}: {exc}")
                continue
            ours = [r for r in rows if r.ticker in universe]
            report.skipped_outside_universe += len(rows) - len(ours)
            kept += store_stock_days(ours, path)
        report.stock_rows[day.isoformat()] = kept
    start = end - timedelta(days=days)
    for index_id in INDICES:
        try:
            report.index_rows[index_id] = store_index_days(provider.daily_index(index_id, start, end), path)
        except ProviderError as exc:
            report.errors.append(f"{index_id}: {exc}")
    return report
