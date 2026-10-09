"""HOSE's own end-of-day index values, used to check the index bars DNSE serves.

The "Biểu đồ cơ bản" page on hsx.vn draws VN-Index from
`GET https://api.hsx.vn/mk/api/v1/market/basic-chart?fromDate=&toDate=&symbolString=VNINDEX|`
(found 2026-10-09; undocumented, read-only, robots.txt allows everything). One
request returns every session since 2015-01-05. It is the exchange's own figure
(source level 1), so it serves as the official reference -- but the user chose
on 2026-10-09 to keep DNSE's bars and only *report* the differences until they
decide (`python -m src.main index-check`).

Conventions, checked on 137 sessions (25/03-09/10/2026):

* `openPrice` is the previous session's close -- HOSE opens the index at its
  last value. TradingView, VNDirect and DNSE show the first computed value
  after the opening auction instead.
* `highPrice` / `lowPrice` are the session's extremes including that opening
  value. DNSE's market-index snapshots agree on 135 / 136 of 137 sessions;
  DNSE's OHLC bars on only 94 highs / 57 lows; VNDirect on 102 / 100.
* `totalShare` / `totalValue` are 0: no volume here.

Safety: GET to one path on one host, nothing else (`ALLOWED_URL`).
"""

from __future__ import annotations

from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import urlsplit

from src.collectors.base import BaseProvider, ProviderDataMissing, ProviderError, ProviderUnavailable

ALLOWED_URL = "https://api.hsx.vn/mk/api/v1/market/basic-chart"
DEFAULT_TIMEOUT_SECONDS = 30.0


def parse_points(body: Any, symbol: str) -> list[dict[str, Any]]:
    """`chartPoints` -> [{trade_date, open, high, low, close}] for `symbol`, oldest first."""
    try:
        points = body["data"][0]["chartPoints"]
    except (KeyError, IndexError, TypeError) as exc:
        raise ProviderDataMissing(f"HOSE basic-chart: no chartPoints for {symbol}") from exc
    out = []
    for p in points or []:
        if str(p.get("stockSymbol", "")).upper() != symbol.upper():
            continue
        try:
            day = datetime.fromtimestamp(int(p["time"]), timezone.utc).date()   # midnight UTC of the session
            row = {"trade_date": day.isoformat(), "open": float(p["openPrice"]), "high": float(p["highPrice"]),
                   "low": float(p["lowPrice"]), "close": float(p["closePrice"])}
        except (KeyError, TypeError, ValueError):
            continue
        if row["close"] > 0 and row["low"] <= min(row["open"], row["close"]) and row["high"] >= max(row["open"], row["close"]):
            out.append(row)
    return sorted(out, key=lambda r: r["trade_date"])


class HoseIndexProvider(BaseProvider):
    """Configuration (sources.yaml, kind `crosscheck`): `base_url`, `timeout_seconds`."""

    kind = "crosscheck"

    def __init__(self, source_id: str, source_level: int, config: dict[str, Any] | None = None, session: Any = None):
        super().__init__(source_id, source_level, config)
        self.url = str(self.config.get("base_url", ALLOWED_URL))
        if self.url != ALLOWED_URL:
            raise PermissionError(f"{self.url}: chỉ được đọc {ALLOWED_URL}")
        self.timeout = float(self.config.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        self._session = session

    def _get(self, params: dict[str, str]) -> Any:
        if urlsplit(self.url).hostname != "api.hsx.vn":
            raise PermissionError(self.url)
        if self._session is None:
            import requests  # noqa: PLC0415

            self._session = requests.Session()
            self._session.headers.update({"User-Agent": "vn-stock-agent (research, data only)",
                                          "Origin": "https://www.hsx.vn", "Referer": "https://www.hsx.vn/"})
        try:
            response = self._session.get(self.url, params=params, timeout=self.timeout)
        except Exception as exc:  # noqa: BLE001 - requests and friends
            raise ProviderUnavailable(f"HOSE basic-chart: {type(exc).__name__}: {exc}") from exc
        status = int(getattr(response, "status_code", 0))
        if status >= 500 or status == 429:
            raise ProviderUnavailable(f"HOSE basic-chart: HTTP {status}")
        if status >= 400:
            raise ProviderError(f"HOSE basic-chart: HTTP {status}: {str(getattr(response, 'text', ''))[:200]}")
        try:
            return response.json()
        except ValueError as exc:
            raise ProviderError("HOSE basic-chart: response is not JSON") from exc

    def index_bars(self, symbol: str, start: date, end: date) -> list[dict[str, Any]]:
        body = self._get({"fromDate": start.isoformat(), "toDate": end.isoformat(), "symbolString": f"{symbol.upper()}|"})
        return parse_points(body, symbol)


def hose_provider() -> HoseIndexProvider:
    from src.config import load_config  # noqa: PLC0415

    spec = next((s for s in load_config().sources.for_kind("crosscheck") if s.id == "hose_index_chart"), None)
    if spec is None:
        raise ProviderError("sources.yaml không có (hoặc đã tắt) mục crosscheck: hose_index_chart")
    return HoseIndexProvider(spec.id, spec.source_level, spec.config)
