"""DNSE OpenAPI market data provider.

Reads history over signed REST through `DnseHttpClient`, which implements
`get()` and nothing else. See docs/DNSE.md for the endpoint contracts.

Things this module is deliberately careful about:

1. **Units.** DNSE quotes stock prices in thousands of VND (`24.35` means
   24,350 VND) while index values are points. Every price passes through
   `normalize_price` with an explicit, configured scale -- never a guess.

2. **Dates.** `/price/ohlc` timestamps are epoch seconds on a Vietnamese
   trading day. They are converted at a fixed UTC+07:00 (Vietnam has no DST),
   never at the machine's local timezone, so bars cannot land on the wrong day.

3. **No order path.** The HTTP client has no write method. `_GuardedClient`
   remains as defence in depth for any client object handed to this module:
   reaching for an execution method raises instead of trading.
"""

from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Mapping, Sequence

from src.collectors.base import (
    MarketDataProvider,
    OHLCV,
    ProviderDataMissing,
    ProviderError,
    ProviderUnavailable,
)
from src.collectors.providers.dnse_client import (
    DEFAULT_API_VERSION,
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    DnseCredentials,
    DnseHttpClient,
    client_options,
)

log = logging.getLogger(__name__)

VN_TZ = timezone(timedelta(hours=7))

OHLC_PATH = "/price/ohlc"
MARKET_INDEX_PATH = "/price/{index}/market-index"
RESOLUTION_DAILY = "1D"
RESOLUTION_WEEKLY = "1W"
TYPE_STOCK = "STOCK"
TYPE_INDEX = "INDEX"
DEFAULT_CHUNK_DAYS = 365
DEFAULT_BOARD_ID = "G1"

# Execution and auth-mutating methods this system must never call on any
# client object, whatever library it comes from.
FORBIDDEN_METHODS = frozenset({
    "post_order", "put_order", "cancel_order", "close_position",
    "post_position_pnl_configs", "create_trading_token", "send_email_otp",
})


class _GuardedClient:
    """Thin proxy that raises if anything tries to reach an execution method.

    Defence in depth: even if a future module wraps a broker SDK and gropes for
    `post_order`, it fails loudly rather than placing a trade.
    """

    def __init__(self, inner: Any):
        object.__setattr__(self, "_inner", inner)

    def __getattr__(self, name: str) -> Any:
        if name in FORBIDDEN_METHODS:
            raise PermissionError(
                f"{name} is an execution/auth-mutating method and is "
                f"blocked. This system does not place orders."
            )
        return getattr(self._inner, name)


def normalize_price(raw: float | None, scale: float) -> float | None:
    """Convert a vendor price into VND (or index points when scale is 1.0).

    `scale` is configured per source (1000.0 for DNSE stock quotes, 1.0 for
    index points). It is never inferred from the magnitude of the value: a
    penny stock and a thousands-scaled blue chip occupy the same numeric range,
    so magnitude heuristics silently corrupt exactly the cheap tickers where
    a 1000x error is hardest to spot.
    """
    if raw is None:
        return None
    return float(raw) * scale


def epoch_to_vn_date(epoch: int | float) -> date:
    """Trading date of an epoch-seconds timestamp, at UTC+07:00."""
    return datetime.fromtimestamp(int(epoch), tz=VN_TZ).date()


def vn_day_bounds(day: date) -> tuple[int, int]:
    """Epoch seconds of the first and last second of `day` in Vietnam."""
    start = datetime.combine(day, time(0, 0, 0), tzinfo=VN_TZ)
    end = datetime.combine(day, time(23, 59, 59), tzinfo=VN_TZ)
    return int(start.timestamp()), int(end.timestamp())


class DnseMarketProvider(MarketDataProvider):
    """Historical daily bars via GET /price/ohlc, plus session reference data.

    Configuration (sources.yaml): `auth_env`, `auth_secret_env`, `base_url`,
    `api_version`, `rate_limit_per_min`, `timeout_seconds`, `price_scale`,
    `index_scale`, `chunk_days`, `board_id`, `index_symbol_aliases`,
    `price_series`, `market_index_value_scale`.
    """

    is_fixture = False

    def __init__(
        self,
        source_id: str,
        source_level: int,
        config: dict[str, Any] | None = None,
        client: Any | None = None,
    ):
        super().__init__(source_id, source_level, config)
        cfg = self.config
        self.price_scale = float(cfg.get("price_scale", 1000.0))
        self.index_scale = float(cfg.get("index_scale", 1.0))
        self.chunk_days = int(cfg.get("chunk_days", DEFAULT_CHUNK_DAYS))
        self.board_id = str(cfg.get("board_id", DEFAULT_BOARD_ID))
        self.index_aliases: Mapping[str, str] = {
            str(k).upper(): str(v).upper() for k, v in (cfg.get("index_symbol_aliases") or {}).items()
        }
        # Only a measured "adjusted" lets adj_close be populated; see docs/DNSE.md.
        self.series_adjusted = str(cfg.get("price_series", "unknown")).lower() == "adjusted"
        # market-index amounts are billions of VND (VN-Index 08/10/2026: 15,883.3 = 15.9 trillion);
        # configured, never inferred from magnitude
        self.market_index_value_scale = float(cfg.get("market_index_value_scale", 1e9))
        self._client = _GuardedClient(client) if client is not None else None

    # -- client ------------------------------------------------------------

    @property
    def client(self) -> Any:
        if self._client is None:
            cfg = self.config
            # Raises DnseAuthError (a ProviderUnavailable) when .env is not filled.
            credentials = DnseCredentials.from_env(
                key_env=cfg.get("auth_env", "DNSE_API_KEY"),
                secret_env=cfg.get("auth_secret_env", "DNSE_API_SECRET"),
            )
            self._client = _GuardedClient(
                DnseHttpClient(
                    credentials=credentials,
                    base_url=str(cfg.get("base_url", DEFAULT_BASE_URL)),
                    api_version=str(cfg.get("api_version", DEFAULT_API_VERSION)),
                    timeout=float(cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
                    **client_options(cfg),
                )
            )
        return self._client

    def health_check(self) -> bool:
        try:
            return bool(self.client.health_check())
        except Exception:  # noqa: BLE001 - health check must never raise
            return False

    # -- parsing -----------------------------------------------------------

    def _parse_bars(self, payload: Any, symbol: str, scale: float) -> list[OHLCV]:
        """Turn an /price/ohlc response into OHLCV records.

        The endpoint returns column-oriented arrays (t/o/h/l/c/v), the shape
        TradingView's UDF spec uses. Row-oriented responses are also accepted
        so a future API version does not break collection outright.
        """
        if not payload:
            raise ProviderDataMissing(f"empty OHLC payload for {symbol}")

        rows: list[dict[str, Any]]
        if isinstance(payload, dict) and "t" in payload:
            times = payload.get("t") or []
            rows = [
                {
                    "t": times[i],
                    "o": _at(payload.get("o"), i),
                    "h": _at(payload.get("h"), i),
                    "l": _at(payload.get("l"), i),
                    "c": _at(payload.get("c"), i),
                    "v": _at(payload.get("v"), i),
                }
                for i in range(len(times))
            ]
        elif isinstance(payload, dict) and isinstance(payload.get("data"), list):
            rows = payload["data"]
        elif isinstance(payload, list):
            rows = payload
        else:
            raise ProviderDataMissing(f"unrecognised OHLC payload shape for {symbol}")

        bars: list[OHLCV] = []
        prov = self._provenance(url=f"{self.config.get('base_url', DEFAULT_BASE_URL)}{OHLC_PATH}")
        for row in rows:
            ts = row.get("t") or row.get("time")
            if ts is None:
                continue
            close = normalize_price(row.get("c", row.get("close")), scale)
            bars.append(
                OHLCV(
                    symbol=symbol,
                    trade_date=epoch_to_vn_date(ts),
                    open=normalize_price(row.get("o", row.get("open")), scale),
                    high=normalize_price(row.get("h", row.get("high")), scale),
                    low=normalize_price(row.get("l", row.get("low")), scale),
                    close=close,
                    volume=int(row.get("v", row.get("volume")) or 0),
                    # The feed carries no traded value; never reconstructed.
                    value_vnd=None,
                    provenance=prov,
                    adj_close=close if self.series_adjusted else None,
                )
            )
        if not bars:
            raise ProviderDataMissing(f"no usable bars for {symbol}")
        bars.sort(key=lambda b: b.trade_date)
        return bars

    # -- fetching ----------------------------------------------------------

    def _fetch_range(
        self, symbol: str, vendor_symbol: str, bar_type: str, start: date, end: date, scale: float
    ) -> list[OHLCV]:
        """Fetch [start, end] in `chunk_days` windows and merge by date.

        The endpoint caps how many bars one call returns, and a silently
        truncated multi-year request is worse than several requests.
        """
        if end < start:
            raise ProviderError(f"invalid range for {symbol}: end {end} precedes start {start}")
        merged: dict[date, OHLCV] = {}
        window_start = start
        while window_start <= end:
            window_end = min(end, window_start + timedelta(days=self.chunk_days - 1))
            self._fetch_window(symbol, vendor_symbol, bar_type, window_start, window_end, scale, merged)
            window_start = window_end + timedelta(days=1)
        if not merged:
            raise ProviderDataMissing(f"no bars for {symbol} between {start} and {end}")
        return [merged[d] for d in sorted(merged)]

    def _fetch_window(
        self,
        symbol: str,
        vendor_symbol: str,
        bar_type: str,
        start: date,
        end: date,
        scale: float,
        merged: dict[date, OHLCV],
    ) -> None:
        from_ts, _ = vn_day_bounds(start)
        _, to_ts = vn_day_bounds(end)
        try:
            payload = self.client.get(
                OHLC_PATH,
                {
                    "type": bar_type,
                    "symbol": vendor_symbol,
                    "resolution": RESOLUTION_DAILY,
                    "from": from_ts,
                    "to": to_ts,
                },
            )
        except ProviderError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise ProviderUnavailable(f"DNSE OHLC call failed for {symbol}: {exc}") from exc

        # nextTime has two observed meanings. With no bars in the window it
        # points at the nearest session that has data (X77, silent since 2021,
        # answers a five-year window with t=[] and nextTime in 2021) -- that is
        # "nothing here", not truncation. With bars present a non-zero nextTime
        # would mean the vendor stopped short of `to`; eleven years in one call
        # came back with 0, but the cap is undocumented, so split rather than
        # store a silently truncated window.
        has_bars = isinstance(payload, dict) and bool(payload.get("t") or payload.get("data"))
        if has_bars and payload.get("nextTime"):
            if (end - start).days < 2:
                raise ProviderDataMissing(f"{symbol}: DNSE truncated a {start}..{end} window")
            middle = start + timedelta(days=(end - start).days // 2)
            self._fetch_window(symbol, vendor_symbol, bar_type, start, middle, scale, merged)
            self._fetch_window(symbol, vendor_symbol, bar_type, middle + timedelta(days=1), end, scale, merged)
            return
        try:
            for bar in self._parse_bars(payload, symbol, scale):
                merged[bar.trade_date] = bar
        except ProviderDataMissing:
            # An empty window (holiday stretch, pre-listing) is not an error by
            # itself; an empty *range* is, and the caller raises for that.
            pass

    # -- interface ---------------------------------------------------------

    def get_daily_ohlcv(self, symbol: str, start: date, end: date) -> list[OHLCV]:
        sym = symbol.upper()
        return self._fetch_range(sym, sym, TYPE_STOCK, start, end, self.price_scale)

    def get_index_ohlcv(self, symbol: str, start: date, end: date) -> list[OHLCV]:
        # Bars are labelled with the project's name; only the request uses DNSE's.
        sym = symbol.upper()
        vendor = self.index_aliases.get(sym, sym)
        return self._fetch_range(sym, vendor, TYPE_INDEX, start, end, self.index_scale)

    def get_reference_prices(self, symbol: str) -> dict[str, Any]:
        """Reference / ceiling / floor for the current session, in VND, and when DNSE published them.

        DNSE publishes the next session's definition in the evening (`time`,
        Vietnam time, e.g. "2026-10-05 20:46:29.147"); src/collectors/refprices.py
        uses it to tell which session the values belong to. On an ex-date the
        exchange resets the reference price, the only signal that catches a
        small stock dividend hiding inside the daily price band.
        """
        body = self.client.get(f"/price/{symbol.upper()}/secdef", {"boardId": self.board_id})
        row = body[0] if isinstance(body, list) and body else body
        if not isinstance(row, dict) or row.get("basicPrice") is None:
            raise ProviderDataMissing(f"no security definition for {symbol}")
        return {
            "reference": normalize_price(row.get("basicPrice"), self.price_scale),
            "ceiling": normalize_price(row.get("ceilingPrice"), self.price_scale),
            "floor": normalize_price(row.get("floorPrice"), self.price_scale),
            "published_at": row.get("time"),
        }

    def get_session_average(self, symbol: str, day: date) -> float | None:
        """The session's average matched price in VND (`avgPrice` of its newest trade), or None
        when the symbol did not trade that day. On UPCoM it is the next session's reference
        price once rounded to the tick (src/collectors/refprices.py)."""
        sym = symbol.upper()
        from_ts, to_ts = vn_day_bounds(day)
        body = self.client.get(
            f"/price/{sym}/trades",
            {"boardId": self.board_id, "from": from_ts, "to": to_ts, "limit": 1, "order": "DESC"},
        )
        trades = body.get("trades") if isinstance(body, dict) else None
        if not trades or trades[0].get("avgPrice") is None:
            return None
        return normalize_price(trades[0].get("avgPrice"), self.price_scale)

    def get_session_totals(self, symbol: str, day: date | None = None) -> OHLCV | None:
        """The tape's own view of one session: last match, high, low, open.

        Read from the newest trade of the day. Used only to check whether the
        vendor's daily candle has settled. Volume is carried but must not be
        compared for equality -- the trade feed counts in different units.
        Returns None when the symbol did not trade that day.
        """
        sym = symbol.upper()
        day = day or datetime.now(VN_TZ).date()
        from_ts, to_ts = vn_day_bounds(day)
        body = self.client.get(
            f"/price/{sym}/trades",
            {"boardId": self.board_id, "from": from_ts, "to": to_ts, "limit": 1, "order": "DESC"},
        )
        trades = body.get("trades") if isinstance(body, dict) else None
        if not trades:
            return None
        last = trades[0]
        return OHLCV(
            symbol=sym,
            trade_date=day,
            open=normalize_price(last.get("openPrice"), self.price_scale),
            high=normalize_price(last.get("highestPrice"), self.price_scale),
            low=normalize_price(last.get("lowestPrice"), self.price_scale),
            close=normalize_price(last.get("matchPrice"), self.price_scale),
            volume=_int(last.get("totalVolumeTraded")),
            value_vnd=None,
            provenance=self._provenance(url=f"{self.config.get('base_url', DEFAULT_BASE_URL)}/price/{sym}/trades"),
        )


    def get_market_index_close(self, index: str, day: date) -> dict[str, Any] | None:
        """The index's last snapshot of `day` from GET /price/{index}/market-index, or None.

        DNSE keeps a snapshot every 5 seconds, newest first, so `limit=1` is the
        session's final state once it has closed (15:05, repeated afterwards).
        `from` and `to` must fall on the same date. History reaches back several
        months only (2026-04-01 answered on 2026-10-08, 2026-01-06 did not).
        Matched volume (`contauctAccTrdVol`) is the figure VNDirect and the
        price boards show; `totalVolumeTraded` adds put-through deals
        (`blkTrdAccTrdVol`). Breadth: advancers include the stocks at the
        ceiling and decliners those at the floor (their volumes add up to the
        total).
        """
        name = self.index_aliases.get(index.upper(), index.upper())
        from_ts, to_ts = vn_day_bounds(day)
        body = self.client.get(MARKET_INDEX_PATH.format(index=name), {"from": from_ts, "to": to_ts, "limit": 1})
        rows = body.get("marketIndices") if isinstance(body, dict) else None
        if not rows:
            return None
        return parse_market_index(rows[0], day, self.market_index_value_scale,
                                  source_id=self.source_id, observed_at=datetime.now(timezone.utc))


def parse_market_index(row: Mapping[str, Any], day: date, value_scale: float,
                       source_id: str, observed_at: datetime) -> dict[str, Any] | None:
    """One market-index snapshot -> the fields stored on index_daily, or None if it is not `day`'s."""
    stamp = str(row.get("transactionTime") or "")
    if stamp[:10] != day.isoformat():
        return None

    def value(key: str) -> float | None:
        raw = row.get(key)
        try:
            return float(raw) * value_scale if raw is not None else None
        except (TypeError, ValueError):
            return None

    return {
        "trade_date": day,
        "transaction_time": stamp,
        "close": _float(row.get("valueIndexes")),
        # the session's extremes as HOSE publishes them (opening value included): stored as
        # session_high / session_low beside the OHLC bar, which often misses them (index-check)
        "session_high": _float(row.get("highestValueIndexes")),
        "session_low": _float(row.get("lowestValueIndexes")),
        "prior_close": _float(row.get("priorValueIndexes")),
        "total_volume": _int(row.get("totalVolumeTraded")),
        "matched_volume": _int(row.get("contauctAccTrdVol")),
        "matched_value_vnd": value("contauctAccTrdVal"),
        "put_through_volume": _int(row.get("blkTrdAccTrdVol")),
        "put_through_value_vnd": value("blkTrdAccTrdVal"),
        "advancers": _int(row.get("fluctuationUpIssueCount")),
        "unchanged": _int(row.get("fluctuationSteadinessIssueCount")),
        "decliners": _int(row.get("fluctuationDownIssueCount")),
        "ceilings": _int(row.get("fluctuationUpperLimitIssueCount")),
        "floors": _int(row.get("fluctuationLowerLimitIssueCount")),
        "source_id": source_id,
        "observed_at": observed_at.isoformat(timespec="seconds"),
    }


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _float(v: Any) -> float | None:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _at(seq: Sequence[Any] | None, i: int) -> Any:
    if seq is None or i >= len(seq):
        return None
    return seq[i]


def _int(v: Any) -> int | None:
    if v is None or v == "":
        return None
    try:
        return int(v)
    except (TypeError, ValueError):
        return None
