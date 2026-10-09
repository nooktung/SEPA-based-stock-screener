"""SSI FastConnect Data: end-of-day snapshots used to cross-check DNSE.

Docs: https://guide.ssi.com.vn/ssi-products/tieng-viet/fastconnect-data/danh-sach-cac-api
(read 2026-10-04). Free for SSI account holders per third-party guides; SSI's
own pages state no fee. The key is a ConsumerID / ConsumerSecret pair created
on iBoard -> "Dịch vụ API"; the PrivateKey shown there signs *orders* and is
never read here.

What SSI adds over DNSE, and why it is only a cross-check source for now
(source kind `crosscheck`, never picked by the backfill or the runner):

* `DailyStockPrice` returns a whole exchange for one day in one page of up to
  1,000 rows: reference / ceiling / floor price, OHLC, matched and put-through
  volume separately, foreign buy / sell and room.
* `DailyIndex` splits an index's matched volume from put-through volume and
  carries the advance / decline counts -- DNSE's VN-Index volume mixes the two
  from one session to the next (audit 2026-10-04).

Safety: this is a **data-only** client. It can obtain a token and GET the
`Market/*` endpoints listed in ALLOWED_ENDPOINTS from the FastConnect *Data*
host and nothing else; any other endpoint or host -- FastConnect Trading
included -- raises PermissionError before a request is made. The project never
places orders.

Units: prices pass through `normalize_price` with the configured scale. SSI's
documented samples quote full VND ("28600"), so the configured scale is 1.0;
the first live run's cross-check against DNSE confirms it (a 1000x slip would
fail every close comparison). Field names arrive in mixed case
(`TotalMatchVol` / `Totalmatchvol`), so they are read case-insensitively.
"""

from __future__ import annotations

import logging
import os
import time
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any, Callable, Mapping
from urllib.parse import urljoin, urlsplit

from src.collectors.base import (
    BaseProvider,
    Provenance,
    ProviderDataMissing,
    ProviderError,
    ProviderUnavailable,
)
from src.collectors.providers.dnse_provider import normalize_price

log = logging.getLogger(__name__)

DATA_HOSTS = frozenset({"fc-data.ssi.com.vn"})
ALLOWED_ENDPOINTS = {
    "AccessToken": "POST",
    "Securities": "GET",
    "SecuritiesDetails": "GET",
    "IndexList": "GET",
    "IndexComponents": "GET",
    "DailyOhlc": "GET",
    "DailyIndex": "GET",
    "DailyStockPrice": "GET",
}
PAGE_SIZE = 1000
MAX_PAGES = 10          # SSI accepts pageIndex 1..10


class SsiAuthError(ProviderUnavailable):
    """Credentials missing or rejected by SSI."""


@dataclass(frozen=True)
class SsiCredentials:
    consumer_id: str
    consumer_secret: str

    @classmethod
    def from_env(cls, id_env: str = "SSI_CONSUMER_ID", secret_env: str = "SSI_CONSUMER_SECRET") -> "SsiCredentials":
        cid = os.environ.get(id_env, "").strip()
        secret = os.environ.get(secret_env, "").strip()
        if not cid or not secret:
            raise SsiAuthError(f"chưa có khóa SSI: thêm ${id_env} và ${secret_env} vào .env "
                               "(tạo trên iBoard -> Dịch vụ API; chỉ cần ConsumerID và ConsumerSecret)")
        return cls(cid, secret)

    def __repr__(self) -> str:
        hint = f"{self.consumer_id[:4]}...{self.consumer_id[-3:]}" if len(self.consumer_id) > 10 else "set"
        return f"SsiCredentials(consumer_id={hint!r}, consumer_secret=<redacted>)"


def lower_keys(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(k).lower().replace(" ", ""): v for k, v in row.items()}


def num(value: Any) -> float | None:
    """SSI sends numbers as strings; an empty or null field stays missing, never 0."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"null", "none", "nan"}:
        return None
    try:
        return float(text.replace(",", ""))
    except ValueError:
        return None


def ssi_date(value: Any) -> date | None:
    text = str(value or "").strip()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text[:10], fmt).date()
        except ValueError:
            continue
    return None


class SsiFcDataClient:
    """Token + GET for the FastConnect Data host, and nothing else."""

    def __init__(self, credentials: SsiCredentials, base_url: str, api_paths: list[str], timeout: float = 20.0,
                 interval_seconds: float = 1.0, quota_waits: list[float] | None = None, session: Any = None,
                 sleep: Callable[[float], None] = time.sleep, clock: Callable[[], float] = time.monotonic):
        host = urlsplit(base_url).hostname or ""
        if host not in DATA_HOSTS:
            raise PermissionError(f"{host or base_url} không phải máy chủ FastConnect Data của SSI; "
                                  "hệ thống chỉ đọc dữ liệu thị trường và không kết nối máy chủ đặt lệnh")
        self._credentials = credentials
        self.base_url = base_url if base_url.endswith("/") else base_url + "/"
        self.api_paths = list(api_paths)
        self.timeout = timeout
        self.interval = interval_seconds
        self.quota_waits = list(quota_waits or [5, 15, 60])
        self._sleep, self._clock = sleep, clock
        self._last: float | None = None
        self._token: str | None = None
        self._path: str | None = None          # the API path prefix that answered
        if session is None:
            import requests  # noqa: PLC0415

            session = requests.Session()
            session.headers["User-Agent"] = "vn-stock-agent (research, data only)"
        self._session = session

    # -- guard ------------------------------------------------------------
    def _url(self, endpoint: str, method: str, prefix: str) -> str:
        if ALLOWED_ENDPOINTS.get(endpoint) != method:
            raise PermissionError(f"{method} {endpoint} bị chặn: chỉ cho phép lấy token và đọc dữ liệu thị trường")
        url = urljoin(self.base_url, prefix + endpoint)
        if (urlsplit(url).hostname or "") not in DATA_HOSTS:
            raise PermissionError(f"{url} nằm ngoài máy chủ FastConnect Data")
        return url

    def _pace(self) -> None:
        if self._last is not None:
            wait = self.interval - (self._clock() - self._last)
            if wait > 0:
                self._sleep(wait)
        self._last = self._clock()

    # -- token ------------------------------------------------------------
    def token(self, refresh: bool = False) -> str:
        if self._token and not refresh:
            return self._token
        body = {"consumerID": self._credentials.consumer_id, "consumerSecret": self._credentials.consumer_secret}
        errors = []
        for prefix in ([self._path] if self._path else self.api_paths):
            url = self._url("AccessToken", "POST", prefix)
            self._pace()
            try:
                r = self._session.post(url, json=body, timeout=self.timeout)
            except Exception as exc:  # noqa: BLE001
                errors.append(f"{prefix}: {type(exc).__name__}")
                continue
            data = _json(r)
            token = ((data or {}).get("data") or {}).get("accessToken") if isinstance(data, dict) else None
            if r.status_code == 200 and token:
                self._token, self._path = token, prefix
                return token
            errors.append(f"{prefix}: HTTP {r.status_code} {(data or {}).get('message', '') if isinstance(data, dict) else ''}".strip())
        raise SsiAuthError("SSI không cấp token: " + "; ".join(errors))

    # -- requests ---------------------------------------------------------
    def get(self, endpoint: str, params: Mapping[str, Any]) -> dict[str, Any]:
        url = self._url(endpoint, "GET", self._path or self.api_paths[0])
        self.token()
        url = self._url(endpoint, "GET", self._path)
        waits = list(self.quota_waits)
        refreshed = False
        while True:
            self._pace()
            try:
                r = self._session.get(url, params=dict(params), timeout=self.timeout,
                                      headers={"Authorization": f"Bearer {self._token}"})
            except Exception as exc:  # noqa: BLE001
                raise ProviderUnavailable(f"GET {endpoint} lỗi mạng: {type(exc).__name__}") from exc
            data = _json(r)
            message = str((data or {}).get("message", "")) if isinstance(data, dict) else ""
            if r.status_code == 401 and not refreshed:
                self.token(refresh=True)
                refreshed = True
                continue
            if "quota" in message.lower() or r.status_code == 429:
                if not waits:
                    raise ProviderUnavailable(f"SSI: vượt giới hạn lượt gọi ({message or r.status_code})")
                self._sleep(waits.pop(0))
                continue
            if r.status_code >= 400 or not isinstance(data, dict):
                raise ProviderUnavailable(f"SSI {endpoint}: HTTP {r.status_code} {message}".strip())
            status = str(data.get("status", "")).lower()
            if status not in {"200", "success"}:
                raise ProviderDataMissing(f"SSI {endpoint}: {message or status}")
            return data

    def pages(self, endpoint: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for page in range(1, MAX_PAGES + 1):
            data = self.get(endpoint, {**params, "pageIndex": page, "pageSize": PAGE_SIZE})
            chunk = data.get("data") or []
            rows.extend(chunk)
            total = num(data.get("totalRecord"))
            if len(chunk) < PAGE_SIZE or (total is not None and len(rows) >= total):
                break
        return rows


def _json(response: Any) -> Any:
    try:
        return response.json()
    except Exception:  # noqa: BLE001
        return None


@dataclass(frozen=True)
class SsiStockDay:
    trade_date: date
    ticker: str
    market: str | None
    ref_price: float | None
    ceiling_price: float | None
    floor_price: float | None
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    close_adjusted: float | None
    avg_price: float | None
    match_volume: float | None
    match_value: float | None
    deal_volume: float | None
    deal_value: float | None
    foreign_buy_volume: float | None
    foreign_sell_volume: float | None
    foreign_buy_value: float | None
    foreign_sell_value: float | None
    foreign_room: float | None
    provenance: Provenance


@dataclass(frozen=True)
class SsiIndexDay:
    trade_date: date
    index_id: str            # the project's name (VNINDEX), not SSI's (VNIndex)
    value: float | None
    change: float | None
    change_pct: float | None
    match_volume: float | None
    match_value: float | None
    deal_volume: float | None
    deal_value: float | None
    total_volume: float | None
    total_value: float | None
    advances: int | None
    declines: int | None
    unchanged: int | None
    ceilings: int | None
    floors: int | None
    session: str | None
    provenance: Provenance


@dataclass(frozen=True)
class SsiBar:
    trade_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None      # matched volume ("khớp bình thường")


def _int(value: Any) -> int | None:
    n = num(value)
    return None if n is None else int(n)


class SsiFcDataProvider(BaseProvider):
    """Cross-check source (kind `crosscheck`). Configuration: `auth_env`, `auth_secret_env`,
    `base_url`, `api_paths`, `price_scale`, `index_scale`, `index_codes`, `request_interval_seconds`,
    `quota_waits_seconds`, `timeout_seconds`."""

    kind = "crosscheck"

    def __init__(self, source_id: str, source_level: int, config: dict[str, Any] | None = None, client: Any = None):
        super().__init__(source_id, source_level, config)
        cfg = self.config
        self.price_scale = float(cfg.get("price_scale", 1.0))
        self.index_scale = float(cfg.get("index_scale", 1.0))
        self.index_codes = {str(k).upper(): str(v) for k, v in (cfg.get("index_codes") or {}).items()}
        self._client = client

    @property
    def client(self) -> SsiFcDataClient:
        if self._client is None:
            cfg = self.config
            self._client = SsiFcDataClient(
                SsiCredentials.from_env(cfg.get("auth_env", "SSI_CONSUMER_ID"), cfg.get("auth_secret_env", "SSI_CONSUMER_SECRET")),
                base_url=str(cfg.get("base_url", "https://fc-data.ssi.com.vn/")),
                api_paths=list(cfg.get("api_paths") or ["api/v2/Market/", "v2.0/Market/"]),
                timeout=float(cfg.get("timeout_seconds", 20)),
                interval_seconds=float(cfg.get("request_interval_seconds", 1.0)),
                quota_waits=[float(x) for x in cfg.get("quota_waits_seconds") or [5, 15, 60]],
            )
        return self._client

    def _prov(self) -> Provenance:
        return self._provenance(url="https://fc-data.ssi.com.vn/")

    def daily_stock_prices(self, day: date, market: str) -> list[SsiStockDay]:
        d = day.strftime("%d/%m/%Y")
        rows = self.client.pages("DailyStockPrice", {"fromDate": d, "toDate": d, "market": market.upper()})
        prov, px = self._prov(), (lambda v: normalize_price(num(v), self.price_scale))
        out = []
        for raw in rows:
            r = lower_keys(raw)
            t, td = str(r.get("symbol") or "").upper(), ssi_date(r.get("tradingdate"))
            if not t or td is None:
                continue
            out.append(SsiStockDay(
                trade_date=td, ticker=t, market=market.upper(),
                ref_price=px(r.get("refprice")), ceiling_price=px(r.get("ceilingprice")), floor_price=px(r.get("floorprice")),
                open=px(r.get("openprice")), high=px(r.get("highestprice")), low=px(r.get("lowestprice")),
                close=px(r.get("closeprice")), close_adjusted=px(r.get("closepriceadjusted")), avg_price=px(r.get("averageprice")),
                match_volume=num(r.get("totalmatchvol")), match_value=num(r.get("totalmatchval")),
                deal_volume=num(r.get("totaldealvol")), deal_value=num(r.get("totaldealval")),
                foreign_buy_volume=num(r.get("foreignbuyvoltotal")), foreign_sell_volume=num(r.get("foreignsellvoltotal")),
                foreign_buy_value=num(r.get("foreignbuyvaltotal")),
                foreign_sell_value=num(r.get("foreignsellvaltotal", r.get("toreignsellvaltotal"))),
                foreign_room=num(r.get("foreigncurrentroom")), provenance=prov))
        return out

    def daily_index(self, index_id: str, start: date, end: date) -> list[SsiIndexDay]:
        code = self.index_codes.get(index_id.upper(), index_id)
        rows = self.client.pages("DailyIndex", {"indexId": code, "fromDate": start.strftime("%d/%m/%Y"),
                                                "toDate": end.strftime("%d/%m/%Y"), "ascending": "true"})
        prov, px = self._prov(), (lambda v: normalize_price(num(v), self.index_scale))
        out = []
        for raw in rows:
            r = lower_keys(raw)
            td = ssi_date(r.get("tradingdate"))
            if td is None:
                continue
            out.append(SsiIndexDay(
                trade_date=td, index_id=index_id.upper(), value=px(r.get("indexvalue")), change=num(r.get("change")),
                change_pct=num(r.get("ratiochange")), match_volume=num(r.get("totalmatchvol")), match_value=num(r.get("totalmatchval")),
                deal_volume=num(r.get("totaldealvol")), deal_value=num(r.get("totaldealval")),
                total_volume=num(r.get("totalvol")), total_value=num(r.get("totalval")),
                advances=_int(r.get("advances")), declines=_int(r.get("declines")), unchanged=_int(r.get("nochanges")),
                ceilings=_int(r.get("ceilings", r.get("ceiling"))), floors=_int(r.get("floors", r.get("floor"))),
                session=r.get("tradingsession"), provenance=prov))
        return out

    def daily_ohlc(self, symbol: str, start: date, end: date, index: bool = False) -> list[SsiBar]:
        code = self.index_codes.get(symbol.upper(), symbol) if index else symbol.upper()
        rows = self.client.pages("DailyOhlc", {"symbol": code, "fromDate": start.strftime("%d/%m/%Y"),
                                               "toDate": end.strftime("%d/%m/%Y"), "ascending": "true"})
        scale = self.index_scale if index else self.price_scale
        out = []
        for raw in rows:
            r = lower_keys(raw)
            td = ssi_date(r.get("tradingdate"))
            if td is None:
                continue
            out.append(SsiBar(td, normalize_price(num(r.get("open")), scale), normalize_price(num(r.get("high")), scale),
                              normalize_price(num(r.get("low")), scale), normalize_price(num(r.get("close")), scale),
                              num(r.get("volume"))))
        return sorted(out, key=lambda b: b.trade_date)


def ssi_provider() -> SsiFcDataProvider:
    """The configured SSI source; raises SsiAuthError when the keys are not in .env."""
    from src.config import load_config  # noqa: PLC0415

    spec = next((s for s in load_config().sources.for_kind("crosscheck", enabled_only=False) if s.id == "ssi_fcdata"), None)
    if spec is None:
        raise ProviderError("sources.yaml không có mục crosscheck: ssi_fcdata")
    provider = SsiFcDataProvider(spec.id, spec.source_level, spec.config)
    provider.client       # noqa: B018 -- fail early, with the message on how to add the keys
    return provider
