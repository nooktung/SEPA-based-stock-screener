"""Vietnamese trading calendar and clock.

Shared by the collectors and the quant layer, so neither has to import the
other. Everything here answers one question: has this session finished?

Vietnam has no daylight saving, so a fixed +07:00 offset is exact and needs no
tzdata install -- which matters on Windows, where the zoneinfo database is not
always present.
"""

from __future__ import annotations

import json
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path

VN_TZ = timezone(timedelta(hours=7))
DEFAULT_MARKET_CLOSE = dtime(15, 0)

# Trading days as DNSE publishes them (GET /market/working-dates: today onwards, about a
# year, holidays left out once the exchanges announce them -- 24/11/2026 was absent on
# 2026-10-08, Tet 2027 not yet). Written by src/collectors/trading_calendar.py; each read
# keeps the earlier dates, so the known range grows day by day. Outside it: weekdays.
CALENDAR_FILE = Path(__file__).resolve().parent.parent / "data" / "calendar" / "working_dates.json"
_calendar_cache: dict[str, object] = {}


def now_vn() -> datetime:
    return datetime.now(VN_TZ)


def to_vn(moment: datetime) -> datetime:
    """Interpret a naive datetime as Vietnamese local time, convert an aware one."""
    if moment.tzinfo is None:
        return moment.replace(tzinfo=VN_TZ)
    return moment.astimezone(VN_TZ)


def market_close_time() -> dtime:
    """Configured close. Falls back to 15:00 if config is unavailable."""
    from src.config import load_config

    try:
        schedule = load_config().thresholds.get("schedule", {}) or {}
        return dtime.fromisoformat(str(schedule.get("market_close", "15:00")))
    except Exception:  # config problems surface through `doctor`, not through here
        return DEFAULT_MARKET_CLOSE


def price_limit_pct(exchange: str) -> float | None:
    """The exchange's daily price band, in percent.

    HOSE is +/-7%, HNX +/-10%, UPCoM +/-15%. A detector that assumes one band
    for all three reports a normal HNX session as a suspected corporate action,
    and misses a genuine one on HOSE. Returns None for an unknown venue --
    including indices, which have no band at all.
    """
    from src.config import load_config

    try:
        limits = load_config().thresholds.get("exchange_price_limit_pct", {}) or {}
    except Exception:  # config problems surface through `doctor`, not through here
        return None
    value = limits.get(str(exchange).upper())
    return float(value) if value is not None else None


def session_close_at(day: date, close_time: dtime | None = None) -> datetime:
    """The moment this session stopped changing, in Vietnamese time."""
    return datetime.combine(day, close_time or market_close_time(), tzinfo=VN_TZ)


def trading_calendar(path: Path | None = None) -> tuple[frozenset[str], str, str] | None:
    """(trading dates, first known, last known) from the stored DNSE calendar, or None."""
    target = path or CALENDAR_FILE
    try:
        stamp = target.stat().st_mtime
    except OSError:
        return None
    key = str(target)
    cached = _calendar_cache.get(key)
    if cached and cached[0] == stamp:                     # type: ignore[index]
        return cached[1]                                  # type: ignore[index]
    try:
        data = json.loads(target.read_text(encoding="utf-8"))
        dates = frozenset(str(d) for d in data["dates"])
        known = (dates, str(data["known_from"]), str(data["known_to"])) if dates else None
    except (OSError, ValueError, KeyError, TypeError):
        known = None
    _calendar_cache[key] = (stamp, known)
    return known


def is_trading_day(day: date, path: Path | None = None) -> bool:
    """A session is held on `day`: DNSE's calendar where it covers the date, weekdays elsewhere."""
    known = trading_calendar(path)
    text = day.isoformat()
    if known and known[1] <= text <= known[2]:
        return text in known[0]
    return is_weekday(day)


def next_trading_day(day: date, path: Path | None = None) -> date:
    """The first session strictly after `day`."""
    nxt = day + timedelta(days=1)
    while not is_trading_day(nxt, path):
        nxt += timedelta(days=1)
    return nxt


def is_weekday(day: date) -> bool:
    """Monday to Friday.

    Public holidays are not hard-coded: the exchanges publish them yearly and a
    stale list is worse than none. `is_trading_day` uses DNSE's published
    calendar where it is stored; elsewhere a holiday simply returns no data,
    which the collectors treat as "no session", not as a failure.
    """
    return day.weekday() < 5


def session_is_complete(
    trade_date: date,
    now: datetime | None = None,
    close_time: dtime | None = None,
) -> bool:
    """Has this trading session finished?

    Today's bar keeps changing until the close. Its "close" is the last match
    so far and its volume is a fraction of the day's, so anything computed from
    it reports a collapse that is really just the clock. Any earlier date is
    finished by definition.
    """
    moment = to_vn(now or now_vn())
    if trade_date < moment.date():
        return True
    if trade_date > moment.date():
        return False
    return moment.timetz().replace(tzinfo=None) >= (close_time or market_close_time())


def sessions_between(start: date, end: date) -> list[date]:
    """Weekdays in the range, ascending. Holidays are not excluded -- see above."""
    days, cursor = [], start
    while cursor <= end:
        if is_weekday(cursor):
            days.append(cursor)
        cursor += timedelta(days=1)
    return days


def missing_runs(sessions: list[date], held: set[date]) -> list[tuple[date, date]]:
    """Group the sessions not yet held into contiguous (start, end) ranges.

    Used to backfill only what is absent. Ranges rather than single days
    because a source that accepts a window can then answer a run in one call,
    and runs rather than one span from first-missing to last-missing because
    that would refetch everything already held in between.

    Adjacency is measured in sessions, not calendar days: a Friday and the
    following Monday belong to the same run, since the weekend was never a
    session to be missing.
    """
    runs: list[tuple[date, date]] = []
    run_start: date | None = None
    previous: date | None = None

    for session in sessions:
        if session in held:
            if run_start is not None:
                runs.append((run_start, previous))
                run_start = None
        else:
            if run_start is None:
                run_start = session
            previous = session

    if run_start is not None:
        runs.append((run_start, previous))
    return runs
