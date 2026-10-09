"""Shared pieces for web news sources: polite HTTP and per-site body extraction.

Politeness is not optional: one request every `request_interval_seconds`,
a timeout, and no retries that hammer a site. robots.txt for every host used
here was checked on 2026-09-27 (cafef.vn: Allow /; vietstock.vn: disallows only
/export, /manager, /cache, which nothing here touches).
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urljoin, urlsplit, urlunsplit

from src.collectors.base import ProviderDataMissing, ProviderUnavailable
from src.collectors.providers.html_text import extract_element

USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) vn-stock-agent/1.0 (personal research)"

Fetcher = Callable[[str], str]


class PoliteFetcher:
    """GET with a minimum interval between requests, shared by one run."""

    def __init__(self, interval_seconds: float = 2.5, timeout_seconds: float = 20.0,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        self.interval = interval_seconds
        self.timeout = timeout_seconds
        self._sleep = sleep
        self._clock = clock
        self._last: float | None = None
        self._session = None

    def __call__(self, url: str) -> str:
        response = self._get(url)
        response.encoding = response.encoding or "utf-8"
        if response.encoding.lower() in ("iso-8859-1", "latin-1"):
            response.encoding = "utf-8"
        return response.text

    def fetch_bytes(self, url: str) -> tuple[str, bytes]:
        """(final URL after redirects, raw body) -- for images."""
        response = self._get(url)
        return response.url, response.content

    def _get(self, url: str):
        if self._last is not None:
            wait = self.interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        if self._session is None:
            import requests  # noqa: PLC0415

            self._session = requests.Session()
            self._session.headers["User-Agent"] = USER_AGENT
        try:
            response = self._session.get(url, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailable(f"GET {url} failed: {exc}") from exc
        finally:
            self._last = self._clock()
        if response.status_code == 404:
            raise ProviderDataMissing(f"404 at {url}")
        if response.status_code >= 400:
            raise ProviderUnavailable(f"HTTP {response.status_code} at {url}")
        return response


def canonical_url(url: str, base: str | None = None) -> str:
    """Absolute https URL without query or fragment (tracking params vary per link)."""
    absolute = urljoin(base or "", url)
    if absolute.startswith("//"):
        absolute = "https:" + absolute
    parts = urlsplit(absolute)
    scheme = "https" if parts.scheme in ("http", "https", "") else parts.scheme
    return urlunsplit((scheme, parts.netloc.lower(), parts.path, "", ""))


def article_id_for(url: str) -> str:
    return hashlib.sha1(canonical_url(url).encode()).hexdigest()[:20]


@dataclass
class ArticleBody:
    text: str
    summary: str = ""
    document_links: list[str] = field(default_factory=list)   # e.g. the issuer's original PDF
    origin: str | None = None                                  # "Theo HOSE" -> "HOSE"


def is_cafef_disclosure(url: str) -> bool:
    """CafeF's /du-lieu/<TICKER>-<id>/ pages are reposted company disclosures."""
    return "cafef.vn" in url and "/du-lieu/" in url


def extract_body(url: str, page: str) -> ArticleBody:
    host = urlsplit(url).netloc.lower()
    if "cafef.vn" in host and is_cafef_disclosure(url):
        text, links = extract_element(page, element_id="newscontent")
        origin = None
        m = re.search(r"\bTheo\s+([A-ZĐ][A-Za-zĐđ.\- ]{1,30})\s*$", text)
        if m:
            origin = m.group(1).strip()
        docs = [canonical_url(link) for link in links if ".pdf" in link.lower()]
        return ArticleBody(text=text, document_links=docs, origin=origin)
    if "cafef.vn" in host:
        summary, _ = extract_element(page, css_class="sapo")
        text, _ = extract_element(page, css_class="detail-content")
        return ArticleBody(text=_drop_widget_lines(text), summary=summary)
    if "vietstock.vn" in host:
        summary, _ = extract_element(page, css_class="pHead")
        text, _ = extract_element(page, css_class="pBody")
        return ArticleBody(text=text, summary=summary)
    raise ProviderDataMissing(f"no body extractor for {host}")


_WIDGET_LINES = {"Giá hiện tại", "Thay đổi", "Xem hồ sơ doanh nghiệp", "TIN MỚI"}


def _drop_widget_lines(text: str) -> str:
    """CafeF embeds a price widget at the top of article bodies."""
    lines = [ln for ln in text.split("\n") if ln.strip() not in _WIDGET_LINES
             and not re.fullmatch(r"[A-Z0-9]{3}:", ln.strip())]
    return "\n".join(lines).strip()
