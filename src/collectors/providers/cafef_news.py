"""CafeF per-ticker news and reposted company disclosures.

The ticker page loads its list from
`/du-lieu/Ajax/Events_RelatedNews_New.aspx?symbol=SSI&PageIndex=1&PageSize=30&Type=2`,
which returns an HTML fragment of (time, link, title) rows, newest first. Two
kinds of link come back:

* `/du-lieu/SSI-2978352/...` -- a company disclosure reposted by CafeF, usually
  ending "Theo HOSE" and linking the issuer's original PDF.
* `cafef.vn/...-188260720182228464.chn` -- a CafeF editorial article.

Both are source level 3: CafeF is an aggregator. A disclosure becomes a FACT
only once checked against the issuer's own document.
"""

from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any, Sequence

from src.collectors.base import NewsArticle, NewsProvider, ProviderDataMissing, ProviderError
from src.collectors.providers.web_news import (
    Fetcher,
    PoliteFetcher,
    article_id_for,
    canonical_url,
)

log = logging.getLogger(__name__)

VN_TZ = timezone(timedelta(hours=7))

_ROW = re.compile(
    r'<span class="timeTitle">\s*([^<]+?)\s*</span>.*?'
    r"<a class=['\"]docnhanhTitle['\"] href=\"([^\"]+)\" title=\"([^\"]*)\"",
    re.S,
)


def parse_list(fragment: str, base_url: str) -> list[tuple[datetime | None, str, str]]:
    """(published_at in VN time, canonical url, title) rows, in page order."""
    out = []
    for when, href, title in _ROW.findall(fragment):
        try:
            published = datetime.strptime(when.strip(), "%d/%m/%Y %H:%M").replace(tzinfo=VN_TZ)
        except ValueError:
            published = None
        out.append((published, canonical_url(html.unescape(href), base_url), html.unescape(title).strip()))
    return out


class CafefTickerNewsProvider(NewsProvider):
    is_fixture = False

    def __init__(self, source_id: str, source_level: int, config: dict[str, Any] | None = None,
                 fetcher: Fetcher | None = None):
        super().__init__(source_id, source_level, config)
        cfg = self.config
        self.list_url = str(cfg.get("list_url", "https://cafef.vn/du-lieu/Ajax/Events_RelatedNews_New.aspx"))
        self.base_url = str(cfg.get("base_url", "https://cafef.vn"))
        self.page_size = int(cfg.get("page_size", 30))
        self.max_pages = int(cfg.get("max_pages", 3))
        self.fetch = fetcher or PoliteFetcher(float(cfg.get("request_interval_seconds", 2.5)),
                                              float(cfg.get("timeout_seconds", 20)))

    def _page_url(self, ticker: str, page: int) -> str:
        return (f"{self.list_url}?symbol={ticker.upper()}&floorID=0&configID=0"
                f"&PageIndex={page}&PageSize={self.page_size}&Type=2")

    def fetch_articles(self, tickers: Sequence[str], since: datetime) -> list[NewsArticle]:
        """Every listed item at or after `since`, for each ticker. No importance filter."""
        since = since if since.tzinfo else since.replace(tzinfo=VN_TZ)
        articles: list[NewsArticle] = []
        failures: dict[str, str] = {}
        for ticker in tickers:
            try:
                articles.extend(self._for_ticker(ticker.upper(), since))
            except ProviderError as exc:
                failures[ticker] = str(exc)
                log.warning("cafef %s: %s", ticker, exc)
        if failures and not articles:
            raise ProviderDataMissing(f"cafef returned nothing; failures: {failures}")
        return articles

    def fetch_market_articles(self, since: datetime) -> list[NewsArticle]:
        """The blank-symbol feed covers disclosures from every company, not just watchlists."""
        market = CafefTickerNewsProvider(self.source_id, int(self.source_level),
                                        {**self.config, "page_size": 30, "max_pages": 100}, self.fetch)
        articles = market._for_ticker("", since)
        self.market_coverage_since = (min(a.published_at for a in articles if a.published_at)
                                      if getattr(market, "history_limited", False) else since)
        return articles

    def _for_ticker(self, ticker: str, since: datetime) -> list[NewsArticle]:
        out: list[NewsArticle] = []
        seen_urls: set[str] = set()
        for page in range(1, self.max_pages + 1):
            rows = parse_list(self.fetch(self._page_url(ticker, page)), self.base_url)
            if not rows:
                if not ticker and not out:
                    raise ProviderDataMissing("market news feed is empty or its markup changed")
                self.history_limited = True
                break
            if not ticker and all(url in seen_urls for _, url, _ in rows):
                self.history_limited = True
                print(f"  cafef: nguồn lặp trang {page}; dừng đọc lịch sử", flush=True)
                break
            seen_urls.update(url for _, url, _ in rows)
            if not ticker and (page == 1 or page % 10 == 0):
                print(f"  cafef toàn thị trường: trang {page}, tới {rows[-1][0]}", flush=True)
            reached_older = False
            for published, url, title in rows:
                if published is not None and published < since:
                    reached_older = True
                    continue
                out.append(NewsArticle(
                    article_id=article_id_for(url),
                    title=title,
                    url=url,
                    published_at=published,
                    provenance=self._provenance(url=url),
                    tickers_hint=(ticker,) if ticker else tuple(re.findall(
                        r"/du-lieu/([A-Z][A-Z0-9]{2})-", url)),
                ))
            if reached_older or len(rows) < self.page_size:
                break
            if not ticker and page == self.max_pages:
                self.history_limited = True
                print("  cafef: đã tới giới hạn 100 trang lịch sử của nguồn", flush=True)
        return out
