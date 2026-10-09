"""Signed HTTP transport for the DNSE OpenAPI.

DNSE authenticates every request with an HTTP-signature header rather than a
bearer token: the API key identifies the caller, and the API secret signs the
request line plus the Date header. Nothing is exchanged up front, so there is
no token to refresh and no session to keep alive -- but the signed Date makes
every request time-sensitive, which is the usual cause of a 401 here (a machine
clock more than a few minutes off will be rejected).

Header set sent with each call:

    Date         Wed, 26 Aug 2026 03:11:07 +0000   -- RFC 1123, UTC
    X-Signature  Signature keyId="...",algorithm="hmac-sha256",
                 headers="(request-target) date",signature="...",nonce="..."
    x-api-key    the API key
    version      API version pin, e.g. 2026-07-23

The string that gets signed is, with a literal newline between the parts:

    (request-target): get /price/ohlc
    date: Wed, 26 Aug 2026 03:11:07 +0000
    nonce: 9f2c...

The request target is the path only -- the query string is not signed. The
scheme mirrors the official SDK (dnse-tech/openapi-sdk, python/dnse/api/
common.py) so a change on their side is a diff against a known reference
rather than a re-derivation.

The secret never appears in a log line, an exception message or a repr. Only
the key id does, and only truncated.

Pacing (2026-10-08). DNSE publishes its limits per endpoint group
(developers.dnse.com.vn, "Giới hạn truy cập"): OHLC 50k/hour, trades and
foreign trading 10k/hour, secdef / market-index / instruments 1k/hour,
working dates 100/hour, each with a daily quota. One pace for every endpoint
either wasted the generous OHLC limit or risked the tight secdef one, so
`rate_limits` (sources.yaml) gives each group its own per-minute pace and a
rolling-hour budget; a path no group matches gets the strictest. Responses
carry `X-Ratelimit-Limit / -Remaining / -Reset` for the 24-hour quota: when
fewer than `min_remaining` calls are left the client stops with a clear
"quota" error instead of running into DNSE's 403. A 429 is retried after
`Retry-After` up to `retry_429` times, and that group's pace is halved for the
rest of the run.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
import re
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlencode

from src.collectors.base import ProviderDataMissing, ProviderError, ProviderUnavailable

log = logging.getLogger(__name__)

DEFAULT_BASE_URL = "https://openapi.dnse.com.vn"
DEFAULT_WS_URL = "wss://ws-openapi.dnse.com.vn"
# Pinned deliberately. DNSE ships breaking changes behind this header, so an
# unpinned client would silently change shape one morning.
DEFAULT_API_VERSION = "2026-07-23"
DEFAULT_TIMEOUT_SECONDS = 20.0

_ALGORITHMS = {
    "hmac-sha1": hashlib.sha1,
    "hmac-sha256": hashlib.sha256,
    "hmac-sha384": hashlib.sha384,
    "hmac-sha512": hashlib.sha512,
}


class DnseAuthError(ProviderUnavailable):
    """Credentials missing, malformed or rejected by DNSE."""


class DnseQuotaExceeded(ProviderUnavailable):
    """The endpoint group's daily quota is used up (or nearly): wait for its reset."""


# Endpoint group of each path, for DNSE's per-group limits. Order matters only for readability.
RATE_GROUPS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ohlc", re.compile(r"^/price/ohlc$")),
    ("trades", re.compile(r"^/price/[^/]+/trades$")),
    ("foreign", re.compile(r"^/price/[^/]+/foreign-trading$")),
    ("secdef", re.compile(r"^/price/[^/]+/secdef$")),
    ("market_index", re.compile(r"^/price/[^/]+/market-index$")),
    ("instruments", re.compile(r"^/market/instruments$")),
    ("working_dates", re.compile(r"^/market/working-dates$")),
)
DEFAULT_GROUP = "default"
_QUOTA_WORDS = ("quota", "limit", "exceed")


def rate_group(path: str) -> str:
    """The DNSE limit group a request path belongs to, or "default" for anything unlisted."""
    bare = path.split("?", 1)[0]
    for name, pattern in RATE_GROUPS:
        if pattern.match(bare):
            return name
    return DEFAULT_GROUP


@dataclass
class _Pace:
    """Per-group pacing: a minimum interval plus a rolling one-hour budget."""

    min_interval: float
    per_hour: int | None
    last_at: float | None = None
    calls: deque | None = None

    def __post_init__(self) -> None:
        self.calls = deque()


@dataclass(frozen=True)
class DnseCredentials:
    api_key: str
    api_secret: str

    def __post_init__(self) -> None:
        if not self.api_key or not self.api_secret:
            raise DnseAuthError("DNSE credentials require both an API key and an API secret")

    @classmethod
    def from_env(
        cls,
        key_env: str = "DNSE_API_KEY",
        secret_env: str = "DNSE_API_SECRET",
    ) -> "DnseCredentials":
        key = os.environ.get(key_env, "").strip()
        secret = os.environ.get(secret_env, "").strip()
        if not key or not secret:
            raise DnseAuthError(
                f"missing DNSE credentials: set ${key_env} and ${secret_env} in .env "
                "(the API secret is displayed once at registration and cannot be read back)"
            )
        return cls(api_key=key, api_secret=secret)

    @property
    def key_hint(self) -> str:
        """Loggable fragment of the key. Never the secret."""
        return f"{self.api_key[:6]}...{self.api_key[-4:]}" if len(self.api_key) > 12 else "set"

    def __repr__(self) -> str:
        return f"DnseCredentials(api_key={self.key_hint!r}, api_secret=<redacted>)"


def rfc1123(moment: datetime) -> str:
    """Format as DNSE expects: 'Wed, 26 Aug 2026 03:11:07 +0000', always UTC."""
    return moment.astimezone(timezone.utc).strftime("%a, %d %b %Y %H:%M:%S %z")


def build_signature(
    api_secret: str,
    method: str,
    path: str,
    date_value: str,
    algorithm: str = "hmac-sha256",
    nonce: str | None = None,
) -> tuple[str, str]:
    """Return (signed-headers list, url-escaped base64 signature)."""
    digestmod = _ALGORITHMS.get(algorithm)
    if digestmod is None:
        raise DnseAuthError(f"unsupported signature algorithm: {algorithm}")

    signature_string = f"(request-target): {method.lower()} {path}\ndate: {date_value}"
    if nonce:
        signature_string += f"\nnonce: {nonce}"

    mac = hmac.new(api_secret.encode("utf-8"), signature_string.encode("utf-8"), digestmod)
    encoded = base64.b64encode(mac.digest()).decode("utf-8")
    return "(request-target) date", quote(encoded, safe="")


def build_signature_header(
    credentials: DnseCredentials,
    method: str,
    path: str,
    date_value: str,
    algorithm: str = "hmac-sha256",
    nonce: str | None = None,
) -> str:
    headers_list, signature = build_signature(
        credentials.api_secret, method, path, date_value, algorithm, nonce
    )
    value = (
        f'Signature keyId="{credentials.api_key}",algorithm="{algorithm}",'
        f'headers="{headers_list}",signature="{signature}"'
    )
    if nonce:
        value += f',nonce="{nonce}"'
    return value


class DnseHttpClient:
    """Thin signed-GET client. One instance per provider.

    Deliberately not a general HTTP client: this project only reads. There is
    no post/put/delete here and no trading-token handling, so no code path in
    this repository can place an order even by accident.
    """

    def __init__(
        self,
        credentials: DnseCredentials,
        base_url: str = DEFAULT_BASE_URL,
        api_version: str = DEFAULT_API_VERSION,
        timeout: float = DEFAULT_TIMEOUT_SECONDS,
        rate_limit_per_min: int = 60,
        rate_limits: Mapping[str, Mapping[str, Any]] | None = None,
        min_remaining: int = 0,
        retry_429: int = 0,
        max_retry_wait: float = 60.0,
        algorithm: str = "hmac-sha256",
        session: Any | None = None,
        clock: Callable[[], datetime] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        nonce_factory: Callable[[], str] | None = None,
    ):
        self.credentials = credentials
        self.base_url = base_url.rstrip("/")
        self.api_version = api_version
        self.timeout = timeout
        self.algorithm = algorithm
        self._min_interval = 60.0 / rate_limit_per_min if rate_limit_per_min else 0.0
        # Without `rate_limits` every path shares one pace (the behaviour before 2026-10-08).
        self._paces: dict[str, _Pace] = {}
        self._limits = {str(k): dict(v or {}) for k, v in (rate_limits or {}).items()}
        self.min_remaining = int(min_remaining)
        self.retry_429 = int(retry_429)
        self.max_retry_wait = float(max_retry_wait)
        self.quota: dict[str, dict[str, Any]] = {}     # group -> last X-Ratelimit-* seen
        self._session = session
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        self._monotonic = monotonic
        self._sleep = sleep
        self._nonce_factory = nonce_factory or (lambda: uuid.uuid4().hex)
        self._last_call_at: float | None = None

    # -- request ------------------------------------------------------------

    def get(self, path: str, query: Mapping[str, Any] | None = None) -> Any:
        """Signed GET returning decoded JSON.

        Raises ProviderUnavailable for anything retryable (network, 429, 5xx,
        auth), ProviderDataMissing for a 404, ProviderError otherwise. It never
        returns a partial or guessed body.
        """
        if not path.startswith("/"):
            path = "/" + path
        url = self.base_url + path
        params = {k: v for k, v in (query or {}).items() if v is not None}
        if params:
            url = f"{url}?{urlencode(params)}"

        group = rate_group(path)
        self._check_quota(group, path)
        attempt = 0
        while True:
            # Signed per attempt: the Date header must be fresh and a nonce is single-use.
            date_value = rfc1123(self._clock())
            nonce = self._nonce_factory()
            headers = {
                "Date": date_value,
                "X-Signature": build_signature_header(
                    self.credentials, "GET", path, date_value, self.algorithm, nonce
                ),
                "x-api-key": self.credentials.api_key,
                "version": self.api_version,
                "Accept": "application/json",
            }
            self._throttle(group)
            response = self._send("GET", url, headers)
            self._note_quota(group, response)
            status = int(getattr(response, "status_code", 0))
            if status == 429 and attempt < self.retry_429:
                attempt += 1
                wait = min(self.max_retry_wait, _retry_after(response) or 5.0 * attempt)
                self._slow_down(group)
                log.warning("DNSE 429 on %s; waiting %.1fs (retry %d of %d)", path, wait, attempt, self.retry_429)
                self._sleep(wait)
                continue
            return self._decode(response, path, group)

    # -- internals ----------------------------------------------------------

    def _pace(self, group: str) -> _Pace:
        pace = self._paces.get(group)
        if pace is None:
            if not self._limits:
                pace = _Pace(self._min_interval, None)
                self._paces = {g: pace for g in [name for name, _ in RATE_GROUPS] + [DEFAULT_GROUP]}
                return pace
            cfg = self._limits.get(group)
            if cfg is None:
                # an endpoint no group lists gets the strictest configured pace
                cfg = min(self._limits.values(), key=lambda c: float(c.get("per_min") or 1e9))
            per_min = float(cfg.get("per_min") or 0)
            per_hour = cfg.get("per_hour")
            pace = _Pace(60.0 / per_min if per_min else 0.0, int(per_hour) if per_hour else None)
            self._paces[group] = pace
        return pace

    def _throttle(self, group: str = DEFAULT_GROUP) -> None:
        pace = self._pace(group)
        now = self._monotonic()
        if pace.per_hour and len(pace.calls) >= pace.per_hour:
            while pace.calls and now - pace.calls[0] >= 3600.0:
                pace.calls.popleft()
            if len(pace.calls) >= pace.per_hour:
                wait = 3600.0 - (now - pace.calls[0])
                log.warning("DNSE %s: hourly budget of %d used; waiting %.0fs", group, pace.per_hour, wait)
                self._sleep(wait)
                now = self._monotonic()
        if pace.min_interval and pace.last_at is not None:
            wait = pace.min_interval - (now - pace.last_at)
            if wait > 0:
                self._sleep(wait)
        pace.last_at = self._monotonic()
        if pace.per_hour:
            pace.calls.append(pace.last_at)
        self._last_call_at = pace.last_at

    def _slow_down(self, group: str) -> None:
        pace = self._pace(group)
        pace.min_interval = max(pace.min_interval * 2.0, 0.5)

    def _note_quota(self, group: str, response: Any) -> None:
        headers = getattr(response, "headers", None) or {}
        try:
            limit, remaining = headers.get("X-Ratelimit-Limit"), headers.get("X-Ratelimit-Remaining")
            reset = headers.get("X-Ratelimit-Reset")
        except AttributeError:
            return
        if remaining is None:
            return
        try:
            self.quota[group] = {"limit": int(limit) if limit is not None else None, "remaining": int(remaining),
                                 "reset": int(reset) if reset is not None else None}
        except (TypeError, ValueError):
            return

    def _check_quota(self, group: str, path: str) -> None:
        seen = self.quota.get(group)
        if not seen or not self.min_remaining or seen["remaining"] > self.min_remaining:
            return
        reset = seen.get("reset")
        if reset and self._clock().timestamp() >= reset:
            return                                             # the window has rolled over
        raise DnseQuotaExceeded(
            f"DNSE {group} quota nearly used ({seen['remaining']} of {seen.get('limit')} left) before {path}; "
            f"stopping instead of running into HTTP 403"
            + (f" -- resets at {datetime.fromtimestamp(reset, timezone.utc).astimezone().strftime('%d/%m %H:%M')}"
               if reset else "")
        )

    def _ensure_session(self) -> Any:
        if self._session is None:
            import requests  # lazy: unit tests drive a fake session instead

            self._session = requests.Session()
        return self._session

    def _send(self, method: str, url: str, headers: Mapping[str, str]) -> Any:
        session = self._ensure_session()
        try:
            return session.request(method, url, headers=dict(headers), timeout=self.timeout)
        except Exception as exc:  # requests.RequestException and friends
            raise ProviderUnavailable(f"DNSE request failed: {type(exc).__name__}: {exc}") from exc

    def _decode(self, response: Any, path: str, group: str = DEFAULT_GROUP) -> Any:
        status = int(getattr(response, "status_code", 0))
        body = getattr(response, "text", "") or ""

        # DNSE answers 403 when a quota is used up (429 is the rate). Told apart from a bad
        # signature by the quota headers or the body; otherwise both causes are named.
        if status == 403 and (self.quota.get(group, {}).get("remaining") == 0
                              or any(w in body.lower() for w in _QUOTA_WORDS)):
            raise DnseQuotaExceeded(f"DNSE quota exhausted for {path} (HTTP 403): {body[:300]}")
        if status in (401, 403):
            raise DnseAuthError(
                f"DNSE rejected the signature for {path} (HTTP {status}, key {self.credentials.key_hint}). "
                "Check the API secret, and check that the machine clock is accurate"
                + (" (a 403 can also mean the daily quota is used up)" if status == 403 else "")
                + f". Server said: {body[:300]}"
            )
        if status == 404:
            raise ProviderDataMissing(f"DNSE has no data at {path} (HTTP 404)")
        if status == 429:
            raise ProviderUnavailable(f"DNSE rate limit hit on {path} (HTTP 429)")
        if status >= 500:
            raise ProviderUnavailable(f"DNSE server error on {path} (HTTP {status}): {body[:300]}")
        if status >= 400:
            raise ProviderError(f"DNSE refused {path} (HTTP {status}): {body[:300]}")

        try:
            return response.json()
        except Exception as exc:
            raise ProviderError(f"DNSE returned a non-JSON body for {path}: {body[:200]!r}") from exc

    def health_check(self) -> bool:
        """Cheap reachability probe against the trading-calendar endpoint."""
        try:
            self.get("/market/working-dates")
            return True
        except ProviderError as exc:
            log.warning("DNSE health check failed: %s", exc)
            return False


def _retry_after(response: Any) -> float | None:
    headers = getattr(response, "headers", None) or {}
    try:
        value = headers.get("Retry-After")
        return float(value) if value is not None else None
    except (AttributeError, TypeError, ValueError):
        return None


def client_options(cfg: Mapping[str, Any]) -> dict[str, Any]:
    """DnseHttpClient keyword arguments from a sources.yaml entry (pacing and quota settings)."""
    return {
        "rate_limit_per_min": int(cfg.get("rate_limit_per_min", 60)),
        "rate_limits": cfg.get("rate_limits") or None,
        "min_remaining": int(cfg.get("min_quota_remaining", 0) or 0),
        "retry_429": int(cfg.get("retry_429", 0) or 0),
        "max_retry_wait": float(cfg.get("max_retry_wait_seconds", 60) or 60),
    }
