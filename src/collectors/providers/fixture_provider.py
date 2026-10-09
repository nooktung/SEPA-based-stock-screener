"""Deterministic offline providers.

These exist so the whole pipeline can run, and every test can pass, with no
network and no credentials. They generate reproducible synthetic data from a
fixed seed derived from the symbol.

They are NOT a data source. Nothing they return may ever reach a user-facing
report: the runner refuses to publish a report whose market data came only
from a fixture provider.
"""

from __future__ import annotations

import hashlib
import math
from datetime import date, datetime, timedelta
from typing import Sequence

from src.collectors.base import (
    AssertionType,
    FinancialDataProvider,
    FinancialMetric,
    FlowDataProvider,
    FlowRecord,
    MacroDataProvider,
    MacroObservation,
    MarketDataProvider,
    NewsArticle,
    NewsProvider,
    OHLCV,
)

IS_FIXTURE = True


def _seed(symbol: str) -> int:
    return int(hashlib.sha256(symbol.encode()).hexdigest()[:8], 16)


def _sessions(start: date, end: date) -> list[date]:
    days, cur = [], start
    while cur <= end:
        if cur.weekday() < 5:
            days.append(cur)
        cur += timedelta(days=1)
    return days


class FixtureMarketProvider(MarketDataProvider):
    is_fixture = True

    def _series(self, symbol: str, start: date, end: date, base: float) -> list[OHLCV]:
        seed = _seed(symbol)
        bars: list[OHLCV] = []
        for i, d in enumerate(_sessions(start, end)):
            drift = math.sin((seed % 97 + i) / 11.0) * 0.02
            close = base * (1 + drift)
            spread = close * 0.012
            bars.append(
                OHLCV(
                    symbol=symbol,
                    trade_date=d,
                    open=round(close - spread / 2, 2),
                    high=round(close + spread, 2),
                    low=round(close - spread, 2),
                    close=round(close, 2),
                    volume=int(1_000_000 + (seed % 500_000) + i * 1_000),
                    value_vnd=round(close * 1_000_000, 2),
                    adj_close=round(close, 2),
                    provenance=self._provenance(url=None),
                )
            )
        return bars

    def get_daily_ohlcv(self, symbol: str, start: date, end: date) -> list[OHLCV]:
        return self._series(symbol, start, end, base=20_000.0)

    def get_index_ohlcv(self, symbol: str, start: date, end: date) -> list[OHLCV]:
        return self._series(symbol, start, end, base=1_700.0)


class FixtureNewsProvider(NewsProvider):
    is_fixture = True

    def fetch_articles(self, tickers: Sequence[str], since: datetime) -> list[NewsArticle]:
        out: list[NewsArticle] = []
        for t in tickers:
            # Two near-duplicate articles per ticker so the deduplicator has
            # something real to collapse in tests.
            for n in (1, 2):
                out.append(
                    NewsArticle(
                        article_id=f"fixture-{t}-{n}",
                        title=f"[FIXTURE] {t} announces a scheduled corporate action",
                        url=f"https://example.invalid/fixture/{t}/{n}",
                        published_at=since + timedelta(hours=n),
                        provenance=self._provenance(url=f"https://example.invalid/fixture/{t}/{n}"),
                        body="Synthetic fixture body. Not a real disclosure.",
                        tickers_hint=(t,),
                    )
                )
        return out


class FixtureFinancialProvider(FinancialDataProvider):
    is_fixture = True

    GENERIC = ("revenue", "net_income", "roe", "total_equity")
    SECURITIES = ("brokerage_revenue", "margin_balance", "prop_trading_pnl", "market_share_hose")

    def _emit(self, ticker: str, names: Sequence[str], periods: int) -> list[FinancialMetric]:
        seed = _seed(ticker)
        out: list[FinancialMetric] = []
        for p in range(periods):
            year = 2026 - p // 4
            quarter_end = [(3, 31), (6, 30), (9, 30), (12, 31)][3 - (p % 4)]
            for name in names:
                out.append(
                    FinancialMetric(
                        ticker=ticker,
                        period_end=date(year, *quarter_end),
                        period_type="Q",
                        metric_name=name,
                        value=float((seed % 1000) * (p + 1)),
                        unit="VND",
                        assertion_type=AssertionType.FACT,
                        provenance=self._provenance(),
                    )
                )
        return out

    def get_metrics(self, ticker: str, periods: int = 8) -> list[FinancialMetric]:
        return self._emit(ticker, self.GENERIC, periods)

    def get_sector_specific_metrics(self, ticker: str, sector_profile: str, periods: int = 8) -> list[FinancialMetric]:
        if sector_profile != "securities":
            return []
        return self._emit(ticker, self.SECURITIES, periods)


class FixtureMacroProvider(MacroDataProvider):
    is_fixture = True

    def get_series(self, series_id: str, start: date, end: date) -> list[MacroObservation]:
        seed = _seed(series_id)
        out, cur = [], start
        while cur <= end:
            out.append(
                MacroObservation(
                    series_id=series_id,
                    observation_date=cur,
                    value=round((seed % 500) / 100.0, 2),
                    unit="pct",
                    region="US" if series_id.startswith(("FED", "US")) else "VN",
                    provenance=self._provenance(),
                )
            )
            cur += timedelta(days=30)
        return out


class FixtureFlowProvider(FlowDataProvider):
    is_fixture = True

    def get_flows(self, scope: str, scope_key: str, start: date, end: date) -> list[FlowRecord]:
        seed = _seed(scope_key)
        out = []
        for i, d in enumerate(_sessions(start, end)):
            net = float(((seed + i * 37) % 2000) - 1000) * 1_000_000
            out.append(
                FlowRecord(
                    scope=scope,
                    scope_key=scope_key,
                    trade_date=d,
                    foreign_buy_value=abs(net) + 5e8,
                    foreign_sell_value=5e8,
                    foreign_net_value=net,
                    provenance=self._provenance(),
                )
            )
        return out

    def get_market_breadth(self, trade_date: date) -> dict[str, int]:
        seed = _seed(trade_date.isoformat())
        adv = 150 + seed % 200
        return {"advancers": adv, "decliners": 400 - adv, "unchanged": 50}
