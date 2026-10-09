"""Store DNSE's trading calendar (GET /market/working-dates) for src/market_calendar.

The endpoint lists the sessions from today onwards, about a year ahead, with
the public holidays the exchanges have announced left out (24/11/2026 was
missing on 2026-10-08; Tet 2027 was not yet). One read a day is enough: the
stored file is rewritten from the read's first date onwards and keeps the
dates before it, so sessions that have passed stay known. A failed read leaves
the previous file in place -- callers then use it, or weekdays where it has
no answer.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping

import src.market_calendar as mc

WORKING_DATES_PATH = "/market/working-dates"


def load(path: Path | None = None) -> dict[str, Any]:
    try:
        return json.loads((path or mc.CALENDAR_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def merge(stored: Mapping[str, Any], fetched: list[str], fetched_on: date, source_id: str) -> dict[str, Any]:
    """The fetched dates from their first one onwards, the stored ones before it."""
    first, last = min(fetched), max(fetched)
    kept = [d for d in stored.get("dates", []) if d < first]
    return {
        "source_id": source_id,
        "fetched_on": fetched_on.isoformat(),
        "known_from": min(first, str(stored["known_from"])) if stored.get("known_from") else first,
        "known_to": last,
        "dates": sorted(set(kept) | set(fetched)),
    }


def refresh(get: Callable[..., Any], today: date, source_id: str, path: Path | None = None,
            force: bool = False) -> str:
    """Read the calendar unless it was read today. Returns a one-line status (Vietnamese)."""
    target = path or mc.CALENDAR_FILE
    stored = load(target)
    if not force and stored.get("fetched_on") == today.isoformat():
        return f"lịch giao dịch: đã đọc hôm nay ({stored.get('known_from')} → {stored.get('known_to')})"
    body = get(WORKING_DATES_PATH)
    raw = body.get("workingDates") if isinstance(body, dict) else None
    fetched = []
    for item in raw or []:
        try:
            fetched.append(date.fromisoformat(str(item)[:10]).isoformat())
        except ValueError:
            continue
    if not fetched:
        return "lịch giao dịch: DNSE không trả ngày nào — giữ bản đã lưu"
    data = merge(stored, fetched, today, source_id)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(data, indent=1), encoding="utf-8")
    weekdays = sum(1 for k in range((date.fromisoformat(data["known_to"]) - today).days + 1)
                   if date.fromordinal(today.toordinal() + k).weekday() < 5)
    upcoming = sum(1 for d in fetched if d >= today.isoformat())
    return (f"lịch giao dịch: {data['known_from']} → {data['known_to']}, "
            f"{weekdays - upcoming} ngày thường không giao dịch từ hôm nay")
