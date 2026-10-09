"""DNSE market data websocket.

Used from Phase 11 (intraday alerts) onward. The REST provider in
dnse_provider.py covers history; this covers "what is happening right now".

Protocol, as documented by DNSE and implemented by their SDK:

    connect   wss://ws-openapi.dnse.com.vn/v1/stream?encoding=json
    welcome   server sends a greeting carrying a session id
    auth      client sends {"action":"auth", "api_key", "signature",
              "timestamp", "nonce"} where signature is
              HMAC-SHA256("{api_key}:{timestamp}:{nonce}", api_secret), hex.
              Server answers {"action":"auth_success"} or an error.
    subscribe {"action":"subscribe","channels":[{"name":..., "symbols":[...]}]}
    data      every payload carries a short type tag in "T" ("t" trade,
              "bc" closed candle, "mi" market index, "f" foreign flow, ...)

Two liveness rules, both load-bearing:

* The server sends {"action":"ping"} roughly every three minutes and drops any
  client that has not answered {"action":"pong"} within one minute. This client
  answers immediately on receipt, and also sends its own ping on a timer, which
  the protocol explicitly allows -- some networks swallow idle frames, and an
  unanswered server ping is indistinguishable from a dead link.
* A connection is capped at eight hours. Rather than be disconnected mid
  session, this client reconnects itself shortly before the cap, re-authing and
  replaying its subscriptions.

Only read-only market channels are exposed. DNSE also publishes order and
position streams; they are deliberately absent here, in keeping with the rule
that this repository holds no path to an account's trading state.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Iterable, Mapping, Sequence

from src.collectors.base import ProviderError, ProviderUnavailable
from src.collectors.providers.dnse_client import DEFAULT_WS_URL, DnseCredentials

log = logging.getLogger(__name__)

# Server pings every 180s and expects a pong inside 60s. 45s of client-side
# keepalive leaves room for two missed frames before the server gives up.
DEFAULT_HEARTBEAT_SECONDS = 45.0
# Server hard-closes at 8h; reconnect at 7h30m so the gap is chosen, not imposed.
DEFAULT_SESSION_SECONDS = 7 * 3600 + 1800

# Message type tag -> event name. Trading events are intentionally not mapped.
MESSAGE_TYPES: dict[str, str] = {
    "t": "trade",
    "te": "trade_extra",
    "q": "quote",
    "b": "ohlc",
    "bc": "ohlc_closed",
    "e": "expected_price",
    "sd": "security_definition",
    "mi": "market_index",
    "emi": "estimated_market_index",
    "f": "foreign",
    "s": "session",
}

VALID_ENCODINGS = ("json", "msgpack")


# ---------------------------------------------------------------------------
# Channel names
# ---------------------------------------------------------------------------

def _suffix(encoding: str) -> str:
    if encoding not in VALID_ENCODINGS:
        raise ProviderError(f"encoding must be one of {VALID_ENCODINGS}, got '{encoding}'")
    return encoding


def trade_channel(board_id: str = "G1", encoding: str = "json") -> str:
    return f"tick.{board_id}.{_suffix(encoding)}"


def quote_channel(board_id: str = "G1", encoding: str = "json") -> str:
    return f"top_price.{board_id}.{_suffix(encoding)}"


def ohlc_channel(resolution: str = "1", encoding: str = "json") -> str:
    return f"ohlc.{resolution}.{_suffix(encoding)}"


def ohlc_closed_channel(resolution: str = "1", encoding: str = "json") -> str:
    return f"ohlc_closed.{resolution}.{_suffix(encoding)}"


def market_index_channel(index_name: str = "VNINDEX", encoding: str = "json") -> str:
    return f"market_index.{index_name.upper()}.{_suffix(encoding)}"


def foreign_channel(board_id: str = "G1", encoding: str = "json") -> str:
    return f"foreign.{board_id}.{_suffix(encoding)}"


def security_definition_channel(board_id: str = "G1", encoding: str = "json") -> str:
    return f"security_definition.{board_id}.{_suffix(encoding)}"


def session_channel(product_group_id: str = "STO", board_id: str = "G1", encoding: str = "json") -> str:
    return f"session.{product_group_id}.{board_id}.{_suffix(encoding)}"


# ---------------------------------------------------------------------------
# Wire messages
# ---------------------------------------------------------------------------

def compute_ws_signature(api_key: str, api_secret: str, timestamp: int, nonce: str) -> str:
    """HMAC-SHA256 of '{api_key}:{timestamp}:{nonce}', hex encoded.

    Different construction from the REST signature (which signs the request
    line and Date header) -- they are not interchangeable.
    """
    message = f"{api_key}:{timestamp}:{nonce}"
    return hmac.new(api_secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).hexdigest()


def build_auth_message(
    credentials: DnseCredentials,
    timestamp: int | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    ts = int(timestamp if timestamp is not None else time.time())
    nonce_value = nonce if nonce is not None else str(int(time.time() * 1_000_000))
    return {
        "action": "auth",
        "api_key": credentials.api_key,
        "signature": compute_ws_signature(credentials.api_key, credentials.api_secret, ts, nonce_value),
        "timestamp": ts,
        "nonce": nonce_value,
    }


def build_subscribe_message(channel: str, symbols: Sequence[str]) -> dict[str, Any]:
    # DNSE requires upper-case symbols on every channel.
    return {
        "action": "subscribe",
        "channels": [{"name": channel, "symbols": [s.upper() for s in symbols]}],
    }


def build_unsubscribe_message(channel: str, symbols: Sequence[str]) -> dict[str, Any]:
    return {
        "action": "unsubscribe",
        "channels": [{"name": channel, "symbols": [s.upper() for s in symbols]}],
    }


class Codec:
    """json or msgpack, chosen per connection."""

    def __init__(self, encoding: str = "json"):
        if encoding not in VALID_ENCODINGS:
            raise ProviderError(f"encoding must be one of {VALID_ENCODINGS}, got '{encoding}'")
        self.encoding = encoding
        self._msgpack = None
        if encoding == "msgpack":
            try:
                import msgpack  # lazy: only needed when msgpack is selected
            except ImportError as exc:
                raise ProviderError(
                    "encoding 'msgpack' needs the msgpack package (pip install msgpack)"
                ) from exc
            self._msgpack = msgpack

    def encode(self, message: Mapping[str, Any]) -> bytes:
        if self.encoding == "json":
            return json.dumps(message).encode("utf-8")
        return self._msgpack.packb(dict(message))

    def decode(self, raw: bytes | str) -> dict[str, Any]:
        if isinstance(raw, str):
            raw = raw.encode("utf-8")
        try:
            if self.encoding == "json":
                decoded = json.loads(raw.decode("utf-8"))
            else:
                decoded = self._msgpack.unpackb(raw, raw=False)
        except Exception as exc:
            raise ProviderError(f"undecodable {self.encoding} frame from DNSE: {exc}") from exc
        if not isinstance(decoded, dict):
            raise ProviderError(f"expected an object frame from DNSE, got {type(decoded).__name__}")
        return decoded


@dataclass
class StreamEvent:
    """One decoded market message."""

    event: str          # trade | ohlc_closed | market_index | foreign | ...
    channel_type: str   # the raw "T" tag
    payload: dict[str, Any]
    received_at: float = field(default_factory=time.time)


Handler = Callable[[StreamEvent], Any | Awaitable[Any]]


class DnseMarketStream:
    """Async websocket consumer for DNSE market channels.

    Usage:

        stream = DnseMarketStream(DnseCredentials.from_env())
        stream.add_subscription(ohlc_closed_channel("1"), ["SSI", "VIX"])
        await stream.run(on_event)

    `connect_factory` is injectable so the handshake, keepalive and dispatch
    logic can be exercised without a network.
    """

    def __init__(
        self,
        credentials: DnseCredentials,
        base_url: str = DEFAULT_WS_URL,
        encoding: str = "json",
        heartbeat_seconds: float = DEFAULT_HEARTBEAT_SECONDS,
        session_seconds: float = DEFAULT_SESSION_SECONDS,
        max_retries: int = 10,
        connect_factory: Callable[[str], Awaitable[Any]] | None = None,
        monotonic: Callable[[], float] = time.monotonic,
    ):
        self.credentials = credentials
        self.base_url = base_url.rstrip("/")
        self.encoding = encoding
        self.codec = Codec(encoding)
        self.heartbeat_seconds = heartbeat_seconds
        self.session_seconds = session_seconds
        self.max_retries = max_retries
        self._connect_factory = connect_factory
        self._monotonic = monotonic
        self._subscriptions: dict[str, list[str]] = {}
        self._connection: Any | None = None
        self._authenticated = False
        self._running = False

    # -- configuration ------------------------------------------------------

    @property
    def url(self) -> str:
        return f"{self.base_url}/v1/stream?encoding={self.encoding}"

    @property
    def subscriptions(self) -> dict[str, list[str]]:
        return {channel: list(symbols) for channel, symbols in self._subscriptions.items()}

    def add_subscription(self, channel: str, symbols: Iterable[str] = ()) -> None:
        """Register a channel. Applied on connect and replayed on reconnect."""
        wanted = [s.upper() for s in symbols]
        existing = self._subscriptions.setdefault(channel, [])
        for symbol in wanted:
            if symbol not in existing:
                existing.append(symbol)

    # -- connection ---------------------------------------------------------

    async def _open(self) -> Any:
        if self._connect_factory is not None:
            return await self._connect_factory(self.url)
        try:
            import websockets  # lazy: only needed for live streaming
        except ImportError as exc:
            raise ProviderError(
                "live streaming needs the websockets package (pip install websockets)"
            ) from exc
        return await websockets.connect(self.url, ping_interval=30, ping_timeout=30)

    async def connect(self) -> None:
        self._connection = await self._open()
        self._authenticated = False

        # Welcome frame first; it carries the session id and confirms the
        # gateway is talking to us before we spend a signature on it.
        await self._recv()
        await self._authenticate()
        for channel, symbols in self._subscriptions.items():
            await self._send(build_subscribe_message(channel, symbols))
            log.info("subscribed to %s %s", channel, symbols or "(no symbol filter)")

    async def _authenticate(self) -> None:
        await self._send(build_auth_message(self.credentials))
        response = await self._recv()
        if response is None:
            raise ProviderUnavailable("DNSE websocket closed before answering the auth message")
        action = response.get("action") or response.get("a")
        if action == "auth_success":
            self._authenticated = True
            return
        message = response.get("message") or response.get("msg") or response
        raise ProviderUnavailable(f"DNSE websocket rejected authentication: {message}")

    async def _send(self, message: Mapping[str, Any]) -> None:
        if self._connection is None:
            raise ProviderUnavailable("DNSE websocket is not connected")
        await self._connection.send(self.codec.encode(message))

    async def _recv(self) -> dict[str, Any] | None:
        """Next decoded frame, or None when the peer signals end of stream."""
        if self._connection is None:
            raise ProviderUnavailable("DNSE websocket is not connected")
        raw = await self._connection.recv()
        if raw is None:
            return None
        return self.codec.decode(raw)

    async def close(self) -> None:
        self._running = False
        if self._connection is not None:
            await self._connection.close()
            self._connection = None
        self._authenticated = False

    # -- main loop ----------------------------------------------------------

    async def run(self, handler: Handler, max_sessions: int | None = None) -> None:
        """Consume until closed, reconnecting on drop or session expiry.

        `max_sessions` bounds how many connections are opened; None means run
        until close() or an unrecoverable error. Tests use a small number.
        """
        self._running = True
        sessions = 0
        attempt = 0

        while self._running and (max_sessions is None or sessions < max_sessions):
            try:
                await self.connect()
                sessions += 1
                attempt = 0
                await self._pump(handler)
            except ProviderUnavailable as exc:
                if not self._running:
                    # close() was called: the socket erroring out afterwards is
                    # the shutdown working, not a connection being lost.
                    log.debug("DNSE stream closed during shutdown: %s", exc)
                    break
                attempt += 1
                if attempt > self.max_retries:
                    raise
                delay = min(2 ** (attempt - 1), 60)
                log.warning("DNSE stream lost (%s); reconnecting in %ss", exc, delay)
                await asyncio.sleep(delay)
            finally:
                if self._connection is not None:
                    try:
                        await self._connection.close()
                    except Exception:  # pragma: no cover - closing a dead socket
                        pass
                    self._connection = None

    async def _pump(self, handler: Handler) -> None:
        """Read frames until the session ends. Returns to trigger a reconnect."""
        started = self._monotonic()
        last_ping = started

        while self._running:
            if self._monotonic() - started >= self.session_seconds:
                log.info("DNSE stream reached its session cap; cycling the connection")
                return

            timeout = max(1.0, self.heartbeat_seconds - (self._monotonic() - last_ping))
            try:
                frame = await asyncio.wait_for(self._recv(), timeout=timeout)
            except asyncio.TimeoutError:
                await self._send({"action": "ping"})
                last_ping = self._monotonic()
                continue
            except ProviderError:
                raise
            except Exception as exc:
                raise ProviderUnavailable(f"DNSE websocket read failed: {exc}") from exc

            if frame is None:
                return
            await self._handle_frame(frame, handler)

    async def _handle_frame(self, frame: Mapping[str, Any], handler: Handler) -> None:
        action = frame.get("action") or frame.get("a")

        if action == "ping":
            # Answer immediately: one minute late is a disconnect.
            await self._send({"action": "pong"})
            return
        if action in ("pong", "subscribed", "unsubscribed"):
            return
        if action == "error":
            log.error("DNSE stream error: %s", frame.get("message") or frame.get("msg") or frame)
            return

        tag = frame.get("T")
        event = MESSAGE_TYPES.get(tag) if isinstance(tag, str) else None
        if event is None:
            log.debug("ignoring unmapped DNSE frame: %s", frame)
            return

        result = handler(StreamEvent(event=event, channel_type=tag, payload=dict(frame)))
        if asyncio.iscoroutine(result):
            await result
