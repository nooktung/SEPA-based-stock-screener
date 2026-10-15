"""DNSE foreign-investor flow provider.

Reads `GET /price/{symbol}/foreign-trading`, which behaves unlike the OHLC
endpoint in three ways that shape everything below.

**Records are intraday snapshots, not daily bars.** Each row is the running
state of foreign trading at a moment in the session -- 12:46, 12:47, 15:32 --
with cumulative day totals in `totalBuyTradedAmount` and
`totalSellTradedAmount`. A day's final numbers are therefore the *last*
snapshot of that day, which is why every request here asks for one row in
descending time order rather than paging through thousands.

**The window is capped at one day.** A two-day range is refused outright, so
history is fetched day by day. That makes a long backfill expensive, and it is
why the collector skips days already stored and skips weekends before asking.

**A non-trading day returns an empty list, not an error.** Holidays are not
encoded anywhere in this system; they simply produce no snapshot, and no
snapshot means no record rather than a zero.

**Day totals cover every board; the board's own figures do not.** On every row,
`totalBuyVolume` / `totalSellVolume` / `total*TradedAmount` are the day's totals over all
boards -- matched round lot (G1), put-through (T1, and T3 after 14:45) and odd lot (G4) --
the same numbers on every board and the ones exchanges and brokers publish. `buyVolume`,
`sellVolume`, `buyTradedAmount`, `sellTradedAmount` on the last G1 snapshot are that board's
own day: G1 + T1 + T3 + G4 add up to the totals exactly (HDB 06/10/2026: sold 2,299,100
matched + 43,770,000 + 30,534,352 put-through + 95 odd lot = 76,603,547; checked on FPT, PNJ,
HPG too). Only the matched part is comparable with the session's matched volume.

Room fields (`foreignerOrderLimitQuantity`, `foreignerBuyPossibleQuantity`)
come back on every row and are genuinely useful for Vietnamese equities, where
a foreign-ownership limit being reached changes who can buy at all. They have
no column in `flow_daily`, so they are not persisted yet -- adding one is a
schema migration, not a provider change.
"""

from __future__ import annotations

import logging
from datetime import date
from typing import Any, Mapping

from src.collectors.base import (
    FlowDataProvider,
    FlowRecord,
    ProviderDataMissing,
    ProviderError,
)
from src.collectors.providers.dnse_client import (
    DEFAULT_API_VERSION,
    DEFAULT_BASE_URL,
    DEFAULT_TIMEOUT_SECONDS,
    DnseCredentials,
    client_options,
    DnseHttpClient,
)
from src.collectors.providers.dnse_provider import vn_day_bounds
from src.market_calendar import sessions_between

log = logging.getLogger(__name__)

SCOPE_TICKER = "TICKER"
DEFAULT_BOARD_ID = "G1"


class DnseFlowProvider(FlowDataProvider):
    """FlowDataProvider backed by the DNSE foreign-trading endpoint.

    Configuration mirrors the market provider: `auth_env`, `auth_secret_env`,
    `base_url`, `api_version`, `rate_limit_per_min`, plus `board_id`.
    """

    is_fixture = False

    def __init__(
        self,
        source_id: str,
        source_level: int,
        config: dict[str, Any] | None = None,
        client: DnseHttpClient | None = None,
    ):
        super().__init__(source_id=source_id, source_level=source_level, config=config)
        self._board_id = str(self.config.get("board_id", DEFAULT_BOARD_ID))
        self._base_url = str(self.config.get("base_url", DEFAULT_BASE_URL))
        self._client = client

    @property
    def client(self) -> DnseHttpClient:
        if self._client is None:
            cfg = self.config
            self._client = DnseHttpClient(
                credentials=DnseCredentials.from_env(
                    key_env=cfg.get("auth_env", "DNSE_API_KEY"),
                    secret_env=cfg.get("auth_secret_env", "DNSE_API_SECRET"),
                ),
                base_url=self._base_url,
                api_version=str(cfg.get("api_version", DEFAULT_API_VERSION)),
                timeout=float(cfg.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS)),
                **client_options(cfg),
            )
        return self._client

    def health_check(self) -> bool:
        return self.client.health_check()

    # -- FlowDataProvider ---------------------------------------------------

    def get_flows(self, scope: str, scope_key: str, start: date, end: date) -> list[FlowRecord]:
        if scope.upper() != SCOPE_TICKER:
            raise ProviderDataMissing(
                f"DNSE serves foreign flow per ticker; scope '{scope}' would have to be "
                "aggregated from constituents, which this provider will not invent"
            )
        if end < start:
            raise ProviderError(f"invalid range for {scope_key}: end {end} precedes start {start}")

        symbol = scope_key.upper()
        records: list[FlowRecord] = []
        for day in sessions_between(start, end):
            snapshot = self._last_snapshot(symbol, day)
            if snapshot is None:
                continue
            record = self._to_record(snapshot, symbol=symbol, day=day)
            if record is not None:
                records.append(record)
        return records

    def get_market_breadth(self, trade_date: date) -> dict[str, int]:
        """Not available over REST.

        Advancer/decliner counts are published on the `market_index` websocket
        channel and nowhere in the REST API. Rather than approximate breadth
        from the handful of tickers on the watchlist -- which would describe
        the watchlist, not the market -- this reports the gap honestly.
        """
        raise ProviderDataMissing(
            "DNSE publishes breadth only on the market_index websocket channel, "
            "not over REST; see docs/DNSE.md"
        )

    # -- internals ----------------------------------------------------------

    def _last_snapshot(self, symbol: str, day: date) -> Mapping[str, Any] | None:
        """Final cumulative reading of one session, or None if it did not trade."""
        from_ts, to_ts = vn_day_bounds(day)
        payload = self.client.get(
            f"/price/{symbol}/foreign-trading",
            {
                "boardId": self._board_id,
                "from": from_ts,
                "to": to_ts,
                "limit": 1,
                "order": "DESC",
            },
        )
        if not isinstance(payload, Mapping):
            raise ProviderError(
                f"DNSE foreign-trading for {symbol}: expected an object, "
                f"got {type(payload).__name__}"
            )
        rows = payload.get("foreigners")
        if rows is None:
            raise ProviderError(
                f"DNSE foreign-trading for {symbol} has no 'foreigners' field; "
                f"keys present: {sorted(payload)}"
            )
        return rows[0] if rows else None

    def _to_record(self, row: Mapping[str, Any], symbol: str, day: date) -> FlowRecord | None:
        buy = _as_float(row.get("totalBuyTradedAmount"))
        sell = _as_float(row.get("totalSellTradedAmount"))
        if buy is None and sell is None:
            log.warning("%s %s: foreign snapshot carries no traded amounts", symbol, day)
            return None

        return FlowRecord(
            scope=SCOPE_TICKER,
            scope_key=symbol,
            trade_date=day,
            foreign_buy_value=buy,
            foreign_sell_value=sell,
            # Net is only meaningful when both sides are present. One side
            # alone would make an absent value read as a zero on that side.
            foreign_net_value=(buy - sell) if buy is not None and sell is not None else None,
            # Proprietary desks and ETF creations are not on this endpoint.
            prop_net_value=None,
            etf_net_value=None,
            total_match_value=None,
            foreign_buy_volume=_as_float(row.get("totalBuyVolume")),
            foreign_sell_volume=_as_float(row.get("totalSellVolume")),
            # The board's own day, read only from a G1 row (the one asked for).
            **(_matched(row) if str(row.get("boardId") or "") == DEFAULT_BOARD_ID == self._board_id else {}),
            provenance=self._provenance(
                url=f"{self._base_url}/price/{symbol}/foreign-trading?boardId={self._board_id}"
            ),
        )


def _matched(row: Mapping[str, Any]) -> dict[str, Any]:
    return {"foreign_buy_volume_matched": _as_float(row.get("buyVolume")),
            "foreign_sell_volume_matched": _as_float(row.get("sellVolume")),
            "foreign_buy_value_matched": _as_float(row.get("buyTradedAmount")),
            "foreign_sell_value_matched": _as_float(row.get("sellTradedAmount")),
            "boards_split": True}


def _as_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
