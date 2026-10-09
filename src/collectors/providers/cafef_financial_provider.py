"""Quarterly income statements from CafeF (tier A: aggregator, unverified).

URL: /du-lieu/bao-cao-tai-chinh/<ticker>/incsta/<year>/<quarter>/0/0/ket-qua-hoat-dong-kinh-doanh-x.chn
(the slug only needs its prefix). One page shows four quarters ending at
<year>/<quarter>; two pages give the eight needed for year-on-year growth.

Two traps found on 2026-09-27, both handled by refusing rather than guessing:

* The label says "tỷ đồng" but the numbers are full VND (2.065.597.768.695).
  Values are parsed as VND with an explicit scale of 1.
* CafeF renders every company on the manufacturing (TT200) template. Banks and
  securities firms file different statements, and their numbers land on the
  wrong rows (SSI's parent net profit row is blank and its EPS row holds a
  1,835 billion figure; VCB's "net revenue" is meaningless). A quarter is
  accepted only if net profit = parent + minority within 1% and net revenue is
  positive -- which rejects those misplaced layouts instead of scoring them.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import date
from typing import Any, Callable

from src.collectors.base import AssertionType, FinancialDataProvider, FinancialMetric, ProviderDataMissing
from src.collectors.providers.web_news import PoliteFetcher

BASE = "https://cafef.vn/du-lieu/bao-cao-tai-chinh/{t}/incsta/{y}/{q}/0/0/ket-qua-hoat-dong-kinh-doanh-x.chn"
SOURCE_ID = "cafef_financial"

# Row label prefixes on CafeF's TT200 income statement -> canonical metric names.
ROWS = {
    "revenue_net": re.compile(r"^3\.\s*Doanh thu thuần", re.I),
    "net_income": re.compile(r"^18\.\s*Lợi nhuận sau thuế thu nhập doanh nghiệp", re.I),
    "net_income_parent": re.compile(r"^19\.\s*Lợi nhuận sau thuế công ty mẹ\s*$", re.I),
    "net_income_minority": re.compile(r"^20\.\s*Lợi nhuận sau thuế công ty mẹ không kiểm soát", re.I),
}
_QUARTER = re.compile(r"Quý\s*(\d)\s*-\s*(\d{4})")


def quarter_end(q: int, y: int) -> date:
    return {1: date(y, 3, 31), 2: date(y, 6, 30), 3: date(y, 9, 30), 4: date(y, 12, 31)}[q]


def parse_vnd(text: str) -> float | None:
    """'2.065.597.768.695' -> 2065597768695.0; '(1.234)' or '-1.234' -> negative; '' -> None."""
    t = text.strip()
    if not t or t in {"-", "—"}:
        return None
    negative = t.startswith("(") and t.endswith(")") or t.startswith("-")
    digits = re.sub(r"[^\d]", "", t)
    if not digits:
        return None
    value = float(digits)
    return -value if negative else value


def _rows(page: str) -> list[list[str]]:
    out = []
    for tr in re.findall(r"<tr[^>]*>(.*?)</tr>", page, re.S):
        cells = [" ".join(html.unescape(re.sub(r"<[^>]+>", " ", c)).split())
                 for c in re.findall(r"<td[^>]*>(.*?)</td>", tr, re.S)]
        if cells and any(cells):
            out.append(cells)
    return out


def parse_income_page(page: str) -> dict[date, dict[str, float | None]]:
    """{quarter end: {metric: VND}} for the quarters shown, in page order."""
    quarters: list[date] = []
    for q, y in _QUARTER.findall(page):
        d = quarter_end(int(q), int(y))
        if d not in quarters:
            quarters.append(d)
    if not quarters:
        raise ProviderDataMissing("no quarter headers on the CafeF income page")
    quarters = quarters[:4]
    # The columns must be consecutive quarters in ascending order; anything else
    # means the header scan picked up a quarter mentioned elsewhere on the page.
    for a, b in zip(quarters, quarters[1:]):
        months = (b.year - a.year) * 12 + b.month - a.month
        if months != 3:
            raise ProviderDataMissing(f"unexpected quarter columns on the CafeF page: {quarters}")
    out: dict[date, dict[str, float | None]] = {d: {} for d in quarters}
    for cells in _rows(page):
        for metric, pattern in ROWS.items():
            if pattern.search(cells[0]):
                for d, raw in zip(quarters, cells[1:1 + len(quarters)]):
                    out[d][metric] = parse_vnd(raw)
    return out


def validate_quarter(values: dict[str, float | None], tolerance_pct: float = 1.0) -> str | None:
    """Why a quarter cannot be trusted, or None if it passes the accounting checks."""
    rev, ni, parent = values.get("revenue_net"), values.get("net_income"), values.get("net_income_parent")
    minority = values.get("net_income_minority") or 0.0
    if parent is None or ni is None:
        return "thiếu LNST hoặc LNST công ty mẹ (thường là mẫu BCTC ngân hàng / chứng khoán bị hiển thị sai)"
    if rev is None or rev <= 0:
        return "doanh thu thuần không dương"
    if abs(ni - (parent + minority)) > abs(ni) * tolerance_pct / 100.0 + 1e6:
        return "LNST ≠ LNST công ty mẹ + cổ đông thiểu số (dòng số liệu bị lệch)"
    return None


@dataclass
class QuarterlyIncome:
    ticker: str
    quarters: dict[date, dict[str, float | None]]
    rejected: dict[date, str]
    source_urls: list[str]


def latest_completed_quarter(as_of: date) -> tuple[int, int]:
    q = (as_of.month - 1) // 3          # 0..3 = quarters completed this year
    return (as_of.year - 1, 4) if q == 0 else (as_of.year, q)


def fetch_quarterly_income(ticker: str, as_of: date, pages: int = 2,
                           fetch: Callable[[str], str] | None = None) -> QuarterlyIncome:
    fetch = fetch or PoliteFetcher(2.5, 20.0)
    y, q = latest_completed_quarter(as_of)
    merged: dict[date, dict[str, float | None]] = {}
    urls: list[str] = []
    for _ in range(pages):
        url = BASE.format(t=ticker.lower(), y=y, q=q)
        urls.append(url)
        for d, values in parse_income_page(fetch(url)).items():
            merged.setdefault(d, values)
        y -= 1                           # one page = four quarters back
    accepted, rejected = {}, {}
    for d in sorted(merged):
        reason = validate_quarter(merged[d])
        if reason:
            rejected[d] = reason
        else:
            accepted[d] = merged[d]
    return QuarterlyIncome(ticker.upper(), accepted, rejected, urls)


class CafefFinancialProvider(FinancialDataProvider):
    """Registry adapter: quarterly income metrics, newest first, all unverified."""

    is_fixture = False

    def __init__(self, source_id: str, source_level: int, config: dict[str, Any] | None = None,
                 fetch: Callable[[str], str] | None = None):
        super().__init__(source_id, source_level, config)
        self._fetch = fetch or PoliteFetcher(float(self.config.get("request_interval_seconds", 2.5)), 20.0)

    def get_metrics(self, ticker: str, periods: int = 8) -> list[FinancialMetric]:
        data = fetch_quarterly_income(ticker, date.today(), pages=max(1, (periods + 3) // 4), fetch=self._fetch)
        out: list[FinancialMetric] = []
        for d in sorted(data.quarters, reverse=True)[:periods]:
            for name, value in data.quarters[d].items():
                out.append(FinancialMetric(
                    ticker=data.ticker, period_end=d, period_type="Q", metric_name=name, value=value,
                    unit="VND", provenance=self._provenance(url=data.source_urls[0]),
                    assertion_type=AssertionType.ESTIMATE))
        if not out:
            raise ProviderDataMissing(f"CafeF has no usable quarters for {ticker}: {data.rejected}")
        return out
