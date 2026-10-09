"""End-of-session index statistics from DNSE's market-index endpoint.

`/price/ohlc` gives the index level, but its volume adds put-through deals to
matched trading and, for some sessions, matches neither (VN-Index 02/10/2026:
785.1M stored vs 615.8M matched, 829.4M total). `GET /price/{index}/market-index`
splits the two. Its matched volume (`contauctAccTrdVol`) equals VNDirect's
VN-Index volume exactly (5 of 5 sessions checked on 2026-10-08), so it is the
figure distribution days and follow-through days are measured on
(`sepa_screen.market.index_volume`, congthuc.md section 11). The same
snapshot carries breadth -- advancers / unchanged / decliners and the ceiling
/ floor counts -- which the end-of-day job used to have no source for.

One request per session (`limit=1`: snapshots are newest first, so the first is
the final one, stamped 15:05). Each figure is attached to the session's
existing index_daily row, never a new row: a session without an OHLC bar has
no level to attach breadth to. Nothing is written when

  * the session has not closed, or the snapshot predates `final_snapshot_after`;
  * the snapshot's close differs from the stored close by more than
    `close_tolerance_pct` -- two DNSE endpoints disagreeing is reported, not
    averaged away.

DNSE keeps only some months of snapshots (2026-04-01 answered on 2026-10-08,
2026-01-06 did not). After `stop_after_empty` consecutive empty sessions the
walk back stops and the date is remembered in `data/index_session_depth.json`,
so later runs never ask for those sessions again. Sessions before that keep
only the OHLC volume; the rules fall back to it pair by pair and say so.
Settings: `index_session` in config/thresholds.yaml.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime
from pathlib import Path
from typing import Any, Callable, Mapping

from src.collectors.base import ProviderError
from src.database import PROJECT_ROOT, connect
from src.market_calendar import now_vn

log = logging.getLogger(__name__)

DEPTH_MARKER = PROJECT_ROOT / "data" / "index_session_depth.json"
COLUMNS = ("matched_volume", "matched_value_vnd", "put_through_volume", "put_through_value_vnd",
           "advancers", "decliners", "unchanged", "ceilings", "floors", "session_high", "session_low")


@dataclass
class IndexSessionReport:
    symbol: str
    written: list[str] = field(default_factory=list)
    empty: list[str] = field(default_factory=list)          # DNSE had no snapshot for the session
    not_final: list[str] = field(default_factory=list)
    mismatched: dict[str, str] = field(default_factory=dict)
    errors: dict[str, str] = field(default_factory=dict)
    history_starts_after: str | None = None                  # DNSE's snapshots start after this session

    @property
    def requests(self) -> int:
        return len(self.written) + len(self.empty) + len(self.not_final) + len(self.mismatched) + len(self.errors)


def _time(text: str) -> dtime:
    h, m = (int(x) for x in str(text).split(":"))
    return dtime(h, m)


def session_closed(day: date, now: datetime, cfg: Mapping[str, Any]) -> bool:
    """Has `day`'s session finished, by `pipeline.session_closed_after`?"""
    if day < now.date():
        return True
    return day == now.date() and now.time() >= _time(cfg["session_closed_after"])


def load_depth(path: Path | None = None) -> dict[str, str]:
    try:
        return json.loads((path or DEPTH_MARKER).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def save_depth(depth: Mapping[str, str], path: Path | None = None) -> None:
    target = path or DEPTH_MARKER
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(dict(depth), indent=1), encoding="utf-8")


def pending_sessions(symbol: str, limit: int, after: str | None, db_path: Path | None = None) -> list[tuple[str, float]]:
    """(session, stored close) of the newest `limit` index_daily rows still lacking market-index figures."""
    with connect(db_path) as conn:
        rows = conn.execute(
            "SELECT trade_date, close FROM index_daily WHERE symbol = ? AND session_observed_at IS NULL "
            "AND trade_date > ? ORDER BY trade_date DESC LIMIT ?",
            (symbol.upper(), after or "0000-00-00", int(limit))).fetchall()
    return [(r[0], r[1]) for r in rows]


def store(symbol: str, snap: Mapping[str, Any], db_path: Path | None = None) -> int:
    with connect(db_path) as conn:
        cur = conn.execute(
            f"UPDATE index_daily SET {', '.join(c + ' = ?' for c in COLUMNS)}, session_source_id = ?, "
            "session_observed_at = ? WHERE symbol = ? AND trade_date = ?",
            (*[snap.get(c) for c in COLUMNS], snap["source_id"], snap["observed_at"], symbol.upper(),
             snap["trade_date"].isoformat()))
        return cur.rowcount


def collect_index_sessions(fetch: Callable[[str, date], Mapping[str, Any] | None], cfg: Mapping[str, Any],
                           pipeline_cfg: Mapping[str, Any], symbol: str = "VNINDEX", sessions: int | None = None,
                           now: datetime | None = None, db_path: Path | None = None,
                           depth_path: Path | None = None) -> IndexSessionReport:
    """Fetch and attach the final market-index snapshot of each pending session, newest first.

    `fetch(symbol, day)` is the provider's `get_market_index_close`.
    """
    moment = now or now_vn()
    report = IndexSessionReport(symbol=symbol.upper())
    depth = load_depth(depth_path)
    after = depth.get(report.symbol)
    limit = int(sessions if sessions is not None else cfg["backfill_sessions"])
    tolerance = float(cfg["close_tolerance_pct"]) / 100.0
    final_after = _time(cfg["final_snapshot_after"])
    stop_after = int(cfg["stop_after_empty"])
    run_of_empty: list[str] = []
    for day_text, stored_close in pending_sessions(report.symbol, limit, after, db_path):
        day = date.fromisoformat(day_text)
        if not session_closed(day, moment, pipeline_cfg):
            report.not_final.append(day_text)
            continue
        try:
            snap = fetch(report.symbol, day)
        except ProviderError as exc:
            report.errors[day_text] = str(exc)
            log.warning("%s %s: %s", report.symbol, day_text, exc)
            if len(report.errors) >= 3:
                break                                   # an outage: stop rather than hammer the endpoint
            continue
        if not snap:
            report.empty.append(day_text)
            run_of_empty.append(day_text)
            if len(run_of_empty) >= stop_after:
                report.history_starts_after = run_of_empty[0]
                depth[report.symbol] = run_of_empty[0]
                save_depth(depth, depth_path)
                break
            continue
        run_of_empty = []
        stamp = str(snap.get("transaction_time") or "")
        if len(stamp) < 16 or _time(stamp[11:16]) < final_after:
            report.not_final.append(day_text)
            continue
        close = snap.get("close")
        if close is None or stored_close is None or abs(close - stored_close) > abs(stored_close) * tolerance:
            report.mismatched[day_text] = f"market-index {close} vs OHLC {stored_close}"
            continue
        if store(report.symbol, snap, db_path):
            report.written.append(day_text)
    return report
