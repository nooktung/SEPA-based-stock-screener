"""Market breadth capture from the index stream."""

import asyncio
from datetime import date, datetime, timezone

import pytest

from src.collectors.base import OHLCV, Provenance, SourceLevel
from src.collectors.breadth import (
    BreadthSnapshot,
    capture_breadth,
    capture_breadth_async,
    parse_breadth,
)
from src.collectors.market_store import upsert_index_bars
from src.collectors.providers.dnse_stream import StreamEvent
from src.database import connect, init_db, sync_reference_data
from src.market_calendar import VN_TZ

FRAME = {
    "T": "mi",
    "indexName": "VNINDEX",
    "valueIndexes": 1828.05,
    "fluctuationUpIssueCount": 118,
    "fluctuationDownIssueCount": 152,
    "fluctuationSteadinessIssueCount": 75,
}


class FakeStream:
    """Stands in for DnseMarketStream: emits queued frames to the handler."""

    def __init__(self, frames=(), delay=0.0):
        self.frames = list(frames)
        self.delay = delay
        self.subscriptions = []
        self.closed = False

    def add_subscription(self, channel, symbols=()):
        self.subscriptions.append(channel)

    async def run(self, handler, max_sessions=None):
        for frame in self.frames:
            await asyncio.sleep(self.delay)
            handler(StreamEvent(event="market_index", channel_type="mi", payload=frame))
        # A live stream keeps running after the frame; the capture is what stops it.
        while not self.closed:
            await asyncio.sleep(0.01)

    async def close(self):
        self.closed = True


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    import src.database as database

    monkeypatch.setattr(database, "db_path", lambda: path)
    init_db(path)
    sync_reference_data(path)
    return path


def _index_bar(day=date(2026, 8, 28)):
    return OHLCV(
        symbol="VNINDEX", trade_date=day, open=1820.0, high=1834.3, low=1815.1,
        close=1828.05, volume=252_058_011, value_vnd=None,
        provenance=Provenance("dnse_openapi", SourceLevel.DATA_VENDOR, datetime.now(timezone.utc)),
    )


# -- parsing -----------------------------------------------------------------

def test_counts_are_pulled_from_the_frame():
    assert parse_breadth(FRAME) == (118, 152, 75)


def test_a_frame_without_counts_yields_nothing():
    assert parse_breadth({"T": "mi", "indexName": "VNINDEX"}) == (None, None, None)


def test_ratio_and_total_derive_from_the_counts():
    snapshot = BreadthSnapshot("VNINDEX", date(2026, 8, 28), 150, 100, 50)
    assert snapshot.ratio == pytest.approx(1.5)
    assert snapshot.total == 300


def test_ratio_is_undefined_when_nothing_fell():
    snapshot = BreadthSnapshot("VNINDEX", date(2026, 8, 28), 150, 0, 50)
    assert snapshot.ratio is None


def test_an_empty_snapshot_is_not_ok():
    assert BreadthSnapshot("VNINDEX", date(2026, 8, 28)).ok is False


# -- capture -----------------------------------------------------------------

def test_the_first_frame_is_taken_and_the_stream_closed():
    stream = FakeStream([FRAME])
    snapshot = asyncio.run(
        capture_breadth_async(stream=stream, now=datetime(2026, 8, 28, 12, 0, tzinfo=VN_TZ))
    )
    assert (snapshot.advancers, snapshot.decliners, snapshot.unchanged) == (118, 152, 75)
    assert stream.closed is True
    assert stream.subscriptions == ["market_index.VNINDEX.json"]


def test_silence_is_reported_not_guessed():
    # Outside trading hours the channel pushes nothing. Breadth is then
    # unavailable, which is a different statement from "no stocks moved".
    snapshot = asyncio.run(
        capture_breadth_async(stream=FakeStream([]), timeout=0.05,
                              now=datetime(2026, 8, 29, 12, 0, tzinfo=VN_TZ))
    )
    assert snapshot.ok is False
    assert snapshot.advancers is None
    assert "unavailable" in snapshot.note


def test_a_later_frame_does_not_overwrite_the_first():
    second = {**FRAME, "fluctuationUpIssueCount": 999}
    snapshot = asyncio.run(
        capture_breadth_async(stream=FakeStream([FRAME, second]),
                              now=datetime(2026, 8, 28, 12, 0, tzinfo=VN_TZ))
    )
    assert snapshot.advancers == 118


def test_session_completeness_is_recorded():
    during = asyncio.run(
        capture_breadth_async(stream=FakeStream([FRAME]),
                              now=datetime(2026, 8, 28, 12, 0, tzinfo=VN_TZ))
    )
    after = asyncio.run(
        capture_breadth_async(stream=FakeStream([FRAME]),
                              now=datetime(2026, 8, 28, 16, 0, tzinfo=VN_TZ))
    )
    assert during.session_complete is False
    assert after.session_complete is True


# -- persistence -------------------------------------------------------------

def test_counts_attach_to_the_stored_index_session(db):
    upsert_index_bars([_index_bar()], path=db)
    snapshot = capture_breadth(
        path=db, stream=FakeStream([FRAME]), now=datetime(2026, 8, 28, 16, 0, tzinfo=VN_TZ)
    )
    assert snapshot.written == 1
    with connect(db) as conn:
        row = conn.execute("SELECT advancers, decliners, unchanged FROM index_daily").fetchone()
    assert (row["advancers"], row["decliners"], row["unchanged"]) == (118, 152, 75)


def test_without_an_index_bar_nothing_is_written_and_it_says_so(db):
    snapshot = capture_breadth(
        path=db, stream=FakeStream([FRAME]), now=datetime(2026, 8, 28, 16, 0, tzinfo=VN_TZ)
    )
    assert snapshot.written == 0
    assert "run `collect` first" in snapshot.note


def test_an_intraday_capture_says_the_counts_are_still_moving(db):
    upsert_index_bars([_index_bar()], path=db)
    snapshot = capture_breadth(
        path=db, stream=FakeStream([FRAME]), now=datetime(2026, 8, 28, 11, 0, tzinfo=VN_TZ)
    )
    assert snapshot.written == 1
    assert "keep moving" in snapshot.note


def test_dry_run_writes_nothing(db):
    upsert_index_bars([_index_bar()], path=db)
    snapshot = capture_breadth(
        path=db, persist=False, stream=FakeStream([FRAME]),
        now=datetime(2026, 8, 28, 16, 0, tzinfo=VN_TZ),
    )
    assert snapshot.ok is True
    assert snapshot.written == 0
    with connect(db) as conn:
        assert conn.execute("SELECT advancers FROM index_daily").fetchone()["advancers"] is None
