"""Industry classification from Vietstock company profile pages (tier A: aggregator).

URL: https://finance.vietstock.vn/<TICKER>/ho-so-doanh-nghiep.htm

The page header carries the company's place in Vietstock's GICS-like sector
tree as a breadcrumb of links, found on 2026-09-27:

    Ngành: <a href=/nganh/20-cong-nghiep-.htm>Công nghiệp</a> >
           <a href=/nganh/2010-hang-hoa-cong-nghiep-.htm>Hàng hóa công nghiệp</a> >
           <a href=/nganh/201030-ky-thuat-xay-dung-.htm>Kỹ thuật xây dựng</a>

Codes are 2 / 4 / 6 digits (sector / industry group / industry). A page
without the breadcrumb raises ProviderDataMissing; no industry is guessed.
The block appears twice (desktop and mobile); only the desktop copy is read.

The page title also carries the company's name with its Vietnamese diacritics,
"HCM: CTCP Chứng khoán Thành phố Hồ Chí Minh - HSC - Hồ sơ doanh nghiệp |
VietstockFinance", which DNSE gives for some tickers without them ("CTCP Chung
khoan TP Ho Chi Minh"; tester report B18). It is read from the same request.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Callable

from src.collectors.base import FinancialDataProvider, FinancialMetric, ProviderDataMissing
from src.collectors.providers.web_news import PoliteFetcher

BASE = "https://finance.vietstock.vn/{t}/ho-so-doanh-nghiep.htm"
SOURCE_ID = "vietstock_industry"
_LINK = re.compile(r'href=["\']?/nganh/(\d+)-[^>]*>([^<]+)</a>')
_TITLE = re.compile(r"<title>(.*?)</title>", re.S | re.I)


@dataclass(frozen=True)
class IndustryPath:
    ticker: str
    levels: tuple[tuple[str, str], ...]     # ((code, name), ...) broadest first
    source_url: str
    name: str | None = None                 # the company's name from the page title

    def level(self, depth: int) -> tuple[str, str] | None:
        """(code, name) at depth 1 = sector, 2 = industry group, 3 = industry."""
        return self.levels[depth - 1] if len(self.levels) >= depth else None


def parse_industry_page(page: str) -> tuple[tuple[str, str], ...]:
    start = page.find("company-general-sector-desk")
    if start < 0:
        start = page.find("company-general-sector")
    if start < 0:
        raise ProviderDataMissing("no industry breadcrumb on the Vietstock profile page")
    end = page.find("</div>", start)
    block = page[start:end if end > 0 else start + 4000]
    levels = tuple((code, " ".join(html.unescape(name).split())) for code, name in _LINK.findall(block))
    if not levels:
        raise ProviderDataMissing("industry breadcrumb is empty")
    # Each level's code must extend its parent's (20 -> 2010 -> 201030); anything
    # else means the scan picked up an unrelated /nganh/ link.
    for (parent, _), (child, _) in zip(levels, levels[1:]):
        if not child.startswith(parent) or len(child) != len(parent) + 2:
            raise ProviderDataMissing(f"inconsistent industry codes: {levels}")
    return levels


def parse_company_name(page: str, ticker: str) -> str | None:
    """The name in "<TICKER>: <name> - Hồ sơ doanh nghiệp | VietstockFinance", or None."""
    m = _TITLE.search(page)
    if not m:
        return None
    title = " ".join(html.unescape(m.group(1)).split())
    prefix = f"{ticker.upper()}:"
    if not title.upper().startswith(prefix):
        return None
    name = re.split(r"\s+-\s+Hồ sơ doanh nghiệp", title[len(prefix):])[0].strip()
    return name or None


def fetch_industry(ticker: str, fetch: Callable[[str], str] | None = None) -> IndustryPath:
    fetch = fetch or PoliteFetcher(2.5, 20.0)
    url = BASE.format(t=ticker.upper())
    page = fetch(url)
    return IndustryPath(ticker.upper(), parse_industry_page(page), url, parse_company_name(page, ticker))


def fetch_profile(ticker: str, fetch: Callable[[str], str] | None = None) -> tuple[str | None, IndustryPath | None]:
    """(company name, industry) from one profile page; either may be missing."""
    fetch = fetch or PoliteFetcher(2.5, 20.0)
    url = BASE.format(t=ticker.upper())
    page = fetch(url)
    name = parse_company_name(page, ticker)
    try:
        return name, IndustryPath(ticker.upper(), parse_industry_page(page), url, name)
    except ProviderDataMissing:
        return name, None


class VietstockIndustryProvider(FinancialDataProvider):
    """Registry entry so the source is declared (and its source_id exists in the DB).

    It serves only the `industry_classification` capability, through
    fetch_industry(); it has no financial metrics.
    """

    is_fixture = False

    def get_metrics(self, ticker: str, periods: int = 8) -> list[FinancialMetric]:
        raise ProviderDataMissing("vietstock_industry serves industry classification only")
