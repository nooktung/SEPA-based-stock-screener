"""RSS news feeds (CafeF, Vietstock) -- market-wide context.

Feeds are not per ticker; the news pipeline tags tickers afterwards. The item
description is kept as a short summary; full bodies are fetched later only for
articles that turn out to concern a monitored ticker.
"""

from __future__ import annotations

import html
import logging
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from typing import Any, Sequence

from src.collectors.base import NewsArticle, NewsProvider, ProviderDataMissing, ProviderError
from src.collectors.providers.html_text import strip_tags
from src.collectors.providers.web_news import Fetcher, PoliteFetcher, article_id_for, canonical_url

log = logging.getLogger(__name__)
VN_TZ = timezone(timedelta(hours=7))


def parse_feed(xml_text: str) -> list[tuple[datetime | None, str, str, str]]:
    """(published_at, canonical url, title, plain-text description) per item."""
    root = ET.fromstring(xml_text.encode("utf-8") if isinstance(xml_text, str) else xml_text)
    out = []
    for item in root.iter("item"):
        title = html.unescape((item.findtext("title") or "").strip())
        link = (item.findtext("link") or item.findtext("guid") or "").strip()
        if not title or not link:
            continue
        raw_date = item.findtext("pubDate")
        try:
            published = parsedate_to_datetime(raw_date) if raw_date else None
        except (TypeError, ValueError):
            published = None
        if published is not None and published.tzinfo is None:
            published = published.replace(tzinfo=VN_TZ)
        description = strip_tags(html.unescape(item.findtext("description") or ""))
        out.append((published, canonical_url(link), title, description))
    return out


class RssNewsProvider(NewsProvider):
    is_fixture = False

    def __init__(self, source_id: str, source_level: int, config: dict[str, Any] | None = None,
                 fetcher: Fetcher | None = None):
        super().__init__(source_id, source_level, config)
        self.feeds = list(self.config.get("feeds") or [])
        self.fetch = fetcher or PoliteFetcher(float(self.config.get("request_interval_seconds", 2.5)),
                                              float(self.config.get("timeout_seconds", 20)))

    def fetch_articles(self, tickers: Sequence[str], since: datetime) -> list[NewsArticle]:
        since = since if since.tzinfo else since.replace(tzinfo=VN_TZ)
        seen: set[str] = set()
        out: list[NewsArticle] = []
        errors: dict[str, str] = {}
        for feed in self.feeds:
            try:
                items = parse_feed(self.fetch(feed))
            except (ProviderError, ET.ParseError) as exc:
                errors[feed] = str(exc)
                log.warning("%s: %s", feed, exc)
                continue
            for published, url, title, description in items:
                if published is not None and published < since:
                    continue
                aid = article_id_for(url)
                if aid in seen:
                    continue
                seen.add(aid)
                out.append(NewsArticle(
                    article_id=aid, title=title, url=url, published_at=published,
                    provenance=self._provenance(url=url), body=description or None,
                ))
        if errors and not out:
            raise ProviderDataMissing(f"{self.source_id}: no feed answered ({errors})")
        return out
