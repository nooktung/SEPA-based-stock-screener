"""Stream lifecycle tests. No network: the transport is injected."""
import asyncio
import json
from datetime import datetime, timedelta, timezone

import pytest

from src.collectors.dnse_stream import (
    CLIENT_KEEPALIVE_INTERVAL, MAX_CONNECTION_LIFETIME, RECONNECT_MARGIN,
    SERVER_PING_INTERVAL, DnseStreamCollector,
)


class FakeConn:
    def __init__(self, frames):
        self.frames, self.sent, self.closed = list(frames), [], False

    async def send(self, msg): self.sent.append(msg)

    async def recv(self):
        await asyncio.sleep(0)
        return self.frames.pop(0) if self.frames else None

    async def close(self): self.closed = True


def collector(handler=None, symbols=("ssi", "vix")):
    received = []
    h = handler or (lambda ch, data: received.append((ch, data)))
    c = DnseStreamCollector("tok", symbols, h)
    return c, received


def test_symbols_forced_uppercase():
    """DNSE requires uppercase symbols; lowercase silently returns nothing."""
    c, _ = collector(symbols=("ssi", "vix"))
    assert c.symbols == ["SSI", "VIX"]


def test_keepalive_interval_inside_server_ping_window():
    """Client keepalive must fire well before the server's 3-minute PING."""
    assert CLIENT_KEEPALIVE_INTERVAL < SERVER_PING_INTERVAL


def test_rotation_margin_leaves_room_before_the_8h_cap():
    assert RECONNECT_MARGIN > timedelta(minutes=10)
    assert MAX_CONNECTION_LIFETIME == timedelta(hours=8)


def test_connection_rotates_before_expiry_not_after():
    c, _ = collector()
    c.stats.connected_at = datetime.now(timezone.utc) - timedelta(hours=7, minutes=45)
    assert c.stats.should_rotate is True


def test_fresh_connection_does_not_rotate():
    c, _ = collector()
    c.stats.connected_at = datetime.now(timezone.utc) - timedelta(minutes=30)
    assert c.stats.should_rotate is False


def test_subscribes_to_the_needed_channels_only():
    c, _ = collector()
    channels = [s["channel"] for s in c.subscriptions()]
    assert "ohlc_closed.1D.json" in channels
    assert "foreign.G1.json" in channels
    assert "security_definition.G1.json" in channels
    assert any(ch.startswith("market_index.VNINDEX") for ch in channels)
    # tick/top_price fire on every match and would swamp the budget
    assert not any(ch.startswith("tick.") for ch in channels)
    assert not any(ch.startswith("top_price.") for ch in channels)


def test_ping_is_counted_and_not_dispatched_to_handler():
    c, received = collector()
    asyncio.run(c._handle_raw(json.dumps({"type": "ping"})))
    assert c.stats.pings_received == 1
    assert received == []


def test_real_ohlc_closed_payload_dispatched():
    """Payload copied from DNSE's OHLC Closed docs."""
    c, received = collector()
    frame = {"channel": "ohlc_closed.1D.json",
             "data": {"time": 1757992500, "open": 30.4, "high": 30.4, "low": 30.25,
                      "close": 30.3, "volume": 1398200, "symbol": "HPG",
                      "resolution": "15", "type": "STOCK"}}
    asyncio.run(c._handle_raw(json.dumps(frame)))
    assert received[0][0] == "ohlc_closed.1D.json"
    assert received[0][1]["symbol"] == "HPG"


def test_real_market_index_payload_carries_breadth():
    """Breadth counts come straight off the market_index channel."""
    c, received = collector()
    frame = {"channel": "market_index.VNINDEX.json",
             "data": {"indexName": "VNINDEX", "valueIndexes": 1669.38,
                      "fluctuationUpIssueCount": 144,
                      "fluctuationDownIssueCount": 158,
                      "fluctuationSteadinessIssueCount": 67}}
    asyncio.run(c._handle_raw(json.dumps(frame)))
    d = received[0][1]
    assert d["fluctuationUpIssueCount"] / d["fluctuationDownIssueCount"] < 1.0


def test_undecodable_frame_dropped_not_crashed():
    c, received = collector()
    asyncio.run(c._handle_raw("{not json"))
    assert received == []
    assert c.stats.messages == 0


def test_run_once_subscribes_then_consumes_then_closes():
    c, received = collector()
    conn = FakeConn([json.dumps({"channel": "x", "data": {"a": 1}})])
    asyncio.run(c.run_once(conn))
    subs = [json.loads(s) for s in conn.sent if "action" in json.loads(s)]
    assert len(subs) == len(c.subscriptions())
    assert all(s["action"] == "subscribe" for s in subs)
    assert conn.closed is True
    assert len(received) == 1


def test_connection_closed_by_none_frame_ends_cleanly():
    c, _ = collector()
    conn = FakeConn([])
    asyncio.run(c.run_once(conn))
    assert conn.closed is True


def test_reconnect_records_a_gap_for_the_freshness_watchdog():
    c, _ = collector()
    c.stats.last_message_at = datetime.now(timezone.utc) - timedelta(minutes=5)

    async def factory(url):
        return FakeConn([])

    c._connect_factory = factory
    asyncio.run(c.run_forever(max_cycles=1))
    assert len(c.stats.gaps) == 1
