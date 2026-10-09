"""Market breadth capture.

An index can rise on four large caps while most of the market falls. Breadth —
how many stocks went up, down, or nowhere — is the only thing that separates
those two cases, which is why the schema keeps it beside the index level rather
than derived from it.

Settled end-of-day counts now come from REST: `GET /price/{index}/market-index`
(src/collectors/index_session.py, 2026-10-08) keeps a snapshot every 5 seconds
and answers after the close and for past sessions. This module remains for
intraday readings: it opens the `market_index` websocket channel, takes the
first frame, and closes again. The channel updates every few seconds during a
session; outside trading hours nothing is pushed, and this reports that rather
than inventing a number.

The counts are cumulative for the day and keep moving until the close. A
capture at noon is a true statement about noon; the settled figure needs a run
after 15:00, which is what the end-of-day job is for.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from src.collectors.market_store import update_breadth
from src.collectors.providers.dnse_client import DnseCredentials
from src.collectors.providers.dnse_stream import DnseMarketStream, market_index_channel
from src.market_calendar import now_vn, session_is_complete

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT_SECONDS = 45.0

FIELD_ADVANCERS = "fluctuationUpIssueCount"
FIELD_DECLINERS = "fluctuationDownIssueCount"
FIELD_UNCHANGED = "fluctuationSteadinessIssueCount"


@dataclass
class BreadthSnapshot:
    index_name: str
    trade_date: date
    advancers: int | None = None
    decliners: int | None = None
    unchanged: int | None = None
    session_complete: bool = False
    written: int = 0
    note: str = ""

    @property
    def ok(self) -> bool:
        return self.advancers is not None and self.decliners is not None

    @property
    def ratio(self) -> float | None:
        """Advancers over decliners. The regime classifier's breadth input."""
        if not self.ok or not self.decliners:
            return None
        return self.advancers / self.decliners

    @property
    def total(self) -> int | None:
        if not self.ok:
            return None
        return self.advancers + self.decliners + (self.unchanged or 0)


def parse_breadth(payload: Mapping[str, Any]) -> tuple[int | None, int | None, int | None]:
    """Pull the three counts out of a market_index frame."""
    return (
        _as_int(payload.get(FIELD_ADVANCERS)),
        _as_int(payload.get(FIELD_DECLINERS)),
        _as_int(payload.get(FIELD_UNCHANGED)),
    )


def _as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


async def capture_breadth_async(
    index_name: str = "VNINDEX",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    credentials: DnseCredentials | None = None,
    stream: DnseMarketStream | None = None,
    now: datetime | None = None,
) -> BreadthSnapshot:
    """Open the market_index stream, take one frame, close.

    `stream` is injectable so the capture logic can be tested without a socket.
    """
    moment = now or now_vn()
    snapshot = BreadthSnapshot(
        index_name=index_name.upper(),
        trade_date=moment.date(),
        session_complete=session_is_complete(moment.date(), now=moment),
    )

    live = stream or DnseMarketStream(credentials or DnseCredentials.from_env())
    live.add_subscription(market_index_channel(snapshot.index_name), [])

    received = asyncio.Event()

    def on_event(event) -> None:
        if event.event != "market_index" or received.is_set():
            return
        advancers, decliners, unchanged = parse_breadth(event.payload)
        snapshot.advancers, snapshot.decliners, snapshot.unchanged = advancers, decliners, unchanged
        received.set()

    task = asyncio.create_task(live.run(on_event, max_sessions=1))
    try:
        await asyncio.wait_for(received.wait(), timeout=timeout)
    except asyncio.TimeoutError:
        snapshot.note = (
            f"no market_index frame within {timeout:.0f}s -- the channel is silent "
            "outside trading hours, so breadth for this session is unavailable"
        )
        log.warning("%s: %s", snapshot.index_name, snapshot.note)
    finally:
        await live.close()
        task.cancel()
        try:
            await task
        except (asyncio.CancelledError, Exception):  # shutdown noise, already reported
            pass

    return snapshot


def capture_breadth(
    index_name: str = "VNINDEX",
    timeout: float = DEFAULT_TIMEOUT_SECONDS,
    persist: bool = True,
    path: Path | None = None,
    **kwargs: Any,
) -> BreadthSnapshot:
    """Synchronous wrapper. Writes onto the day's index row when one exists."""
    snapshot = asyncio.run(capture_breadth_async(index_name=index_name, timeout=timeout, **kwargs))

    if snapshot.ok and persist:
        snapshot.written = update_breadth(
            snapshot.index_name,
            snapshot.trade_date,
            snapshot.advancers,
            snapshot.decliners,
            snapshot.unchanged,
            path=path,
        )
        if snapshot.written == 0:
            # Breadth belongs beside that session's index level. Creating a row
            # with counts and no prices would be a bar that never traded.
            snapshot.note = (
                f"no {snapshot.index_name} bar stored for {snapshot.trade_date} -- "
                "run `collect` first, then capture breadth"
            )
    if snapshot.ok and not snapshot.session_complete and not snapshot.note:
        snapshot.note = "intraday reading -- the counts keep moving until the close"

    return snapshot
