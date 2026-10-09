"""DNSE market-data WebSocket collector.

Implements the connection contract from DNSE's docs exactly:

  * base URL wss://ws-openapi.dnse.com.vn
  * a connection is valid for at most 8 hours; the server closes it after that
  * the server sends PING every 3 minutes and expects PONG within 1 minute
  * the client may send PONG proactively to survive missed PINGs behind NAT
  * symbols must be uppercase

The 8-hour cap matters for this system specifically: a session opened at 07:00
for the pre-market job expires at 15:00, mid-afternoon. `RECONNECT_MARGIN`
rotates the connection early so the expiry never lands inside a trading
session.

Streaming feeds intraday alerts (Phase 11). Daily bars come from the REST
provider, not from here -- a stream that was disconnected for ten minutes
leaves a hole, and a hole in the bar series must be visible as missing data
rather than silently interpolated.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Awaitable, Callable, Sequence

log = logging.getLogger(__name__)

WS_BASE_URL = "wss://ws-openapi.dnse.com.vn"

# Per DNSE docs.
SERVER_PING_INTERVAL = timedelta(minutes=3)
PONG_DEADLINE = timedelta(minutes=1)
MAX_CONNECTION_LIFETIME = timedelta(hours=8)
# Rotate well before expiry so a forced close never lands mid-session.
RECONNECT_MARGIN = timedelta(minutes=20)
# Proactive keepalive, safely inside the server's 3-minute cadence.
CLIENT_KEEPALIVE_INTERVAL = timedelta(seconds=100)


def channel_ohlc_closed(resolution: str, encoding: str = "json") -> str:
    return f"ohlc_closed.{resolution}.{encoding}"


def channel_market_index(index_name: str, encoding: str = "json") -> str:
    return f"market_index.{index_name}.{encoding}"


def channel_foreign(board_id: str = "G1", encoding: str = "json") -> str:
    return f"foreign.{board_id}.{encoding}"


def channel_security_definition(board_id: str = "G1", encoding: str = "json") -> str:
    return f"security_definition.{board_id}.{encoding}"


def channel_tick(board_id: str = "G1", encoding: str = "json") -> str:
    return f"tick.{board_id}.{encoding}"


@dataclass
class StreamStats:
    connected_at: datetime | None = None
    reconnects: int = 0
    messages: int = 0
    pings_received: int = 0
    pongs_sent: int = 0
    last_message_at: datetime | None = None
    gaps: list[tuple[datetime, datetime]] = field(default_factory=list)

    @property
    def age(self) -> timedelta:
        if self.connected_at is None:
            return timedelta(0)
        return datetime.now(timezone.utc) - self.connected_at

    @property
    def should_rotate(self) -> bool:
        """True once the connection is close enough to the 8h cap to rotate."""
        return self.age >= MAX_CONNECTION_LIFETIME - RECONNECT_MARGIN


Handler = Callable[[str, dict[str, Any]], Awaitable[None] | None]


class DnseStreamCollector:
    """Subscribes to market-data channels and dispatches decoded payloads.

    Transport is injectable so the reconnect and keepalive logic can be tested
    without a network. `connect_factory` returns an object exposing
    `send`, `recv`, `close` -- the shape `websockets` already provides.
    """

    def __init__(
        self,
        token: str,
        symbols: Sequence[str],
        handler: Handler,
        connect_factory: Callable[[str], Any] | None = None,
        encoding: str = "json",
    ):
        # Uppercase is a hard requirement of the DNSE protocol.
        self.symbols = [s.upper() for s in symbols]
        self.token = token
        self.handler = handler
        self.encoding = encoding
        self._connect_factory = connect_factory
        self.stats = StreamStats()
        self._running = False

    def subscriptions(self, indices: Sequence[str] = ("VNINDEX", "VN30")) -> list[dict[str, Any]]:
        """Channels this system actually needs.

        Deliberately narrow. `tick` and `top_price` fire on every match and
        would dominate the message budget; the pre-market brief needs closed
        daily candles, index breadth and foreign flow, nothing more.
        """
        subs: list[dict[str, Any]] = [
            {"channel": channel_ohlc_closed("1D", self.encoding), "symbols": self.symbols},
            {"channel": channel_foreign("G1", self.encoding), "symbols": self.symbols},
            {"channel": channel_security_definition("G1", self.encoding), "symbols": self.symbols},
        ]
        subs += [
            {"channel": channel_market_index(idx, self.encoding), "symbols": []}
            for idx in indices
        ]
        return subs

    # -- lifecycle ---------------------------------------------------------

    async def _keepalive(self, conn: Any) -> None:
        """Send PONG proactively.

        The docs explicitly permit an unsolicited PONG, and recommend it where
        a library auto-handles ping frames or a NAT drops idle connections.
        Relying only on reactive PONG means one missed PING kills the session.
        """
        while self._running:
            await asyncio.sleep(CLIENT_KEEPALIVE_INTERVAL.total_seconds())
            try:
                await conn.send(json.dumps({"type": "pong"}))
                self.stats.pongs_sent += 1
            except Exception as exc:  # noqa: BLE001
                log.warning("keepalive send failed: %s", exc)
                return

    async def _handle_raw(self, raw: str | bytes) -> None:
        try:
            msg = json.loads(raw) if isinstance(raw, (str, bytes)) else raw
        except (ValueError, TypeError):
            log.warning("undecodable frame dropped")
            return

        mtype = msg.get("type")
        if mtype == "ping":
            self.stats.pings_received += 1
            return
        channel = msg.get("channel", "")
        payload = msg.get("data", msg)
        self.stats.messages += 1
        self.stats.last_message_at = datetime.now(timezone.utc)
        result = self.handler(channel, payload)
        if asyncio.iscoroutine(result):
            await result

    async def run_once(self, conn: Any) -> None:
        """Consume one connection until it rotates or closes."""
        self._running = True
        self.stats.connected_at = datetime.now(timezone.utc)
        for sub in self.subscriptions():
            await conn.send(json.dumps({"action": "subscribe", **sub}))

        keepalive = asyncio.create_task(self._keepalive(conn))
        try:
            while self._running:
                if self.stats.should_rotate:
                    log.info("rotating connection at age %s", self.stats.age)
                    break
                raw = await conn.recv()
                if raw is None:
                    break
                await self._handle_raw(raw)
        finally:
            self._running = False
            keepalive.cancel()
            try:
                await conn.close()
            except Exception:  # noqa: BLE001
                pass

    async def run_forever(self, max_cycles: int | None = None) -> None:
        """Reconnect loop with exponential backoff.

        A recorded gap is not a failure to hide -- `stats.gaps` feeds the
        freshness watchdog, which lowers confidence on any signal computed
        from a window that overlaps a gap.
        """
        if self._connect_factory is None:
            raise RuntimeError("connect_factory is required to run the stream")

        cycles, backoff = 0, 1.0
        while max_cycles is None or cycles < max_cycles:
            cycles += 1
            disconnected_at = self.stats.last_message_at
            try:
                conn = await self._connect_factory(self._url())
                if disconnected_at:
                    self.stats.gaps.append((disconnected_at, datetime.now(timezone.utc)))
                backoff = 1.0
                await self.run_once(conn)
            except Exception as exc:  # noqa: BLE001
                log.warning("stream cycle failed: %s; retrying in %.0fs", exc, backoff)
                await asyncio.sleep(backoff)
                backoff = min(backoff * 2, 60.0)
            self.stats.reconnects += 1

    def _url(self) -> str:
        return f"{WS_BASE_URL}?token={self.token}"
