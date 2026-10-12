"""Quarterly income statements of banks and securities firms from VNDirect's finfo API (tier A).

CafeF renders every company on the manufacturing template (TT200), and a bank's or a
broker's figures land on the wrong rows there (cafef_financial_provider.py rejects them).
VNDirect publishes each filer's own template, keyed by item code:

    https://api-finfo.vndirect.com.vn/v4/financial_statements?q=code:VCB~reportType:QUARTER~
        modelType:90,102~fiscalDate:2026-06-30,...
    https://api-finfo.vndirect.com.vn/v4/financial_models?q=modelType:102   (the item names)

modelType 102 is the bank income statement (TT49/2014/TT-NHNN), 90 the securities firm's
(TT334/2016/TT-BTC); 2 is the ordinary company's, read from CafeF instead. Values are full
VND (VCB Q2/2026 parent net profit 14,542,276,000,000; checked against the parent +
minority = net profit identity). Read-only, one request per ticker plus the item names
once per run, paced like CafeF. Read live on 2026-10-12 (VCB, SSI).

"Revenue" for the growth grade is the template's own top line, named in the output:
bank = total operating income (TOI, 421701), securities firm = total operating revenue
(21000). Every item code used is checked against its name in `financial_models` before
anything is stored: a renamed or moved item is refused, never mapped by position.
"""

from __future__ import annotations

import json
from datetime import date
from typing import Any, Callable, Mapping

from src.collectors.base import AssertionType, FinancialDataProvider, FinancialMetric, ProviderDataMissing
from src.collectors.providers.cafef_financial_provider import (QuarterlyIncome, latest_completed_quarter,
                                                               quarter_end, validate_quarter)

SOURCE_ID = "vndirect_finfo"
BASE = "https://api-finfo.vndirect.com.vn/v4/"
TEMPLATES = {102: "bank", 90: "securities"}
TEMPLATE_TEXT = {"bank": "ngân hàng", "securities": "công ty chứng khoán"}
REVENUE_TEXT = {"bank": "tổng thu nhập hoạt động (TOI)", "securities": "tổng doanh thu hoạt động"}

# metric -> (item code, words the item's Vietnamese name must contain), per template.
ITEMS: dict[str, dict[str, tuple[int, tuple[str, ...]]]] = {
    "bank": {
        "revenue_net": (421701, ("tổng thu nhập hoạt động",)),
        "net_interest_income": (421900, ("thu nhập lãi thuần",)),
        "provision_expense": (422900, ("dự phòng rủi ro tín dụng",)),
        "pretax_income": (23800, ("trước thuế",)),
        "net_income": (23003, ("lợi nhuận sau thuế thu nhập doanh nghiệp",)),
        "net_income_parent": (23000, ("công ty mẹ",)),
        "net_income_minority": (23500, ("thiểu số",)),
    },
    "securities": {
        "revenue_net": (21000, ("tổng doanh thu hoạt động",)),
        "fvtpl_gain": (700039, ("fvtpl",)),
        "brokerage_revenue": (621110, ("môi giới",)),
        "lending_income": (700044, ("cho vay",)),
        "pretax_income": (23800, ("trước thuế",)),
        "net_income": (23003, ("lợi nhuận sau thuế thu nhập doanh nghiệp",)),
        "net_income_parent": (23000, ("công ty mẹ",)),
        "net_income_minority": (23500, ("thiểu số",)),
    },
}


def quarter_ends(as_of: date, n: int = 9) -> list[date]:
    """The latest `n` completed quarter ends, newest first."""
    y, q = latest_completed_quarter(as_of)
    out = []
    for _ in range(n):
        out.append(quarter_end(q, y))
        y, q = (y - 1, 4) if q == 1 else (y, q - 1)
    return out


def check_items(models: Mapping[int, list[Mapping[str, Any]]]) -> None:
    """Refuse when an item code no longer carries the name it was mapped by."""
    for model, template in TEMPLATES.items():
        names = {int(r["itemCode"]): str(r.get("itemVnName") or "").lower() for r in models.get(model, [])}
        for metric, (code, words) in ITEMS[template].items():
            name = names.get(code)
            if name is None or not all(w in name for w in words):
                raise ProviderDataMissing(f"mẫu {model}: chỉ tiêu {code} ({metric}) nay là '{name}' — không đọc theo vị trí")


def parse_statements(rows: list[Mapping[str, Any]]) -> tuple[str, dict[date, dict[str, float | None]]]:
    """(template, {quarter end: {metric: VND}}) from financial_statements rows."""
    models = {int(r["modelType"]) for r in rows if r.get("modelType") is not None}
    found = models & set(TEMPLATES)
    if len(found) != 1:
        raise ProviderDataMissing("không có mẫu BCTC ngân hàng / công ty chứng khoán cho mã này"
                                  + (f" (mẫu {sorted(models)})" if models else ""))
    model = found.pop()
    template = TEMPLATES[model]
    by_code = {code: metric for metric, (code, _) in ITEMS[template].items()}
    out: dict[date, dict[str, float | None]] = {}
    for r in rows:
        if int(r.get("modelType") or 0) != model or str(r.get("reportType")) != "QUARTER":
            continue
        metric = by_code.get(int(r["itemCode"]))
        if metric is None:
            continue
        value = r.get("numericValue")
        out.setdefault(date.fromisoformat(str(r["fiscalDate"])[:10]), {})[metric] = (
            None if value is None else float(value))
    return template, out


def fetch_models(fetch_json: Callable[[str], Any]) -> dict[int, list[Mapping[str, Any]]]:
    return {m: fetch_json(f"{BASE}financial_models?sort=displayOrder:asc&q=modelType:{m}&size=500").get("data", [])
            for m in TEMPLATES}


def fetch_quarterly_income(ticker: str, as_of: date, fetch_json: Callable[[str], Any],
                           quarters: int = 9) -> tuple[str, QuarterlyIncome]:
    """(template, quarters) for a bank or securities firm; ProviderDataMissing for anything else."""
    ends = ",".join(d.isoformat() for d in quarter_ends(as_of, quarters))
    url = (f"{BASE}financial_statements?q=code:{ticker.upper()}~reportType:QUARTER~"
           f"modelType:{','.join(str(m) for m in TEMPLATES)}~fiscalDate:{ends}&size=1000")
    rows = fetch_json(url).get("data", [])
    template, values = parse_statements(rows)
    accepted, rejected = {}, {}
    for d in sorted(values):
        reason = validate_quarter(values[d])
        if reason:
            rejected[d] = reason
        else:
            accepted[d] = values[d]
    return template, QuarterlyIncome(ticker.upper(), accepted, rejected, [url])


def json_fetcher(fetch_text: Callable[[str], str]) -> Callable[[str], Any]:
    return lambda url: json.loads(fetch_text(url))


class VndirectFinfoProvider(FinancialDataProvider):
    """Registry adapter: bank / securities quarterly income metrics, newest first, all unverified."""

    is_fixture = False

    def __init__(self, source_id: str, source_level: int, config: dict[str, Any] | None = None,
                 fetch: Callable[[str], str] | None = None):
        super().__init__(source_id, source_level, config)
        if fetch is None:
            from src.collectors.providers.web_news import PoliteFetcher  # noqa: PLC0415

            fetch = PoliteFetcher(float(self.config.get("request_interval_seconds", 2.5)), 20.0)
        self._json = json_fetcher(fetch)

    def get_metrics(self, ticker: str, periods: int = 8) -> list[FinancialMetric]:
        check_items(fetch_models(self._json))
        _, data = fetch_quarterly_income(ticker, date.today(), self._json, quarters=periods + 1)
        out: list[FinancialMetric] = []
        for d in sorted(data.quarters, reverse=True)[:periods]:
            for name, value in data.quarters[d].items():
                out.append(FinancialMetric(
                    ticker=data.ticker, period_end=d, period_type="Q", metric_name=name, value=value,
                    unit="VND", provenance=self._provenance(url=data.source_urls[0]),
                    assertion_type=AssertionType.ESTIMATE))
        if not out:
            raise ProviderDataMissing(f"VNDirect không có quý hợp lệ cho {ticker}: {data.rejected}")
        return out
