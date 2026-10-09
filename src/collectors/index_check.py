"""VN-Index bars checked against HOSE's own values -- a report, nothing is changed.

On 2026-10-09 HOSE's published index values (src/collectors/providers/
hose_index_provider.py) showed that DNSE's VN-Index OHLC bars, which every rule
reads, often miss the session's extremes: over 137 sessions (25/03-09/10) their
high matched HOSE on 94, their low on 57, while DNSE's own market-index
snapshot (stored as index_daily.session_high / session_low) matched on 135 / 136.
Over the 11 stored years: 575 lows, 359 highs and 68 closes differ (closes only
up to 2023), and 33 HOSE sessions are missing. The user chose to keep DNSE's
bars for now and get this report every day before deciding
(`python -m src.main index-check`, pipeline step `index-check`).

The report says how often each source agrees with HOSE, lists the recent
sessions that differ, and recomputes the market status (distribution days with
their 5% expiry on the high, FTD failure on the low) with HOSE's high / low in
place of the stored ones, so the effect on the rules is visible. Settings:
`index_check` in config/thresholds.yaml.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

from src.database import PROJECT_ROOT, connect

REPORT_DIR = PROJECT_ROOT / "data" / "audit"


def report_path(session: str, directory: Path | None = None) -> Path:
    return (directory or REPORT_DIR) / f"vnindex_check_{session}.json"


def store_hose(symbol: str, bars: Sequence[Mapping[str, Any]], source_id: str, path: Path | None = None) -> int:
    observed = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect(path) as conn:
        conn.executemany(
            "INSERT INTO hose_index_daily (symbol, trade_date, open, high, low, close, source_id, observed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(symbol, trade_date) DO UPDATE SET open = excluded.open, "
            "high = excluded.high, low = excluded.low, close = excluded.close, source_id = excluded.source_id, "
            "observed_at = excluded.observed_at",
            [(symbol.upper(), b["trade_date"], b["open"], b["high"], b["low"], b["close"], source_id, observed)
             for b in bars])
    return len(bars)


def collect_hose(fetch: Callable[[str, date, date], list[dict[str, Any]]], symbol: str, cfg: Mapping[str, Any],
                 source_id: str, today: date, path: Path | None = None) -> int:
    """Whole history on the first run (one request), then the last `overlap_days` again."""
    with connect(path) as conn:
        latest = conn.execute("SELECT MAX(trade_date) FROM hose_index_daily WHERE symbol = ?",
                              (symbol.upper(),)).fetchone()[0]
    start = (date.fromisoformat(latest) - timedelta(days=int(cfg["overlap_days"])) if latest
             else date.fromisoformat(str(cfg["history_from"])))
    return store_hose(symbol, fetch(symbol, start, today), source_id, path)


def _same(a: float | None, b: float | None, tol: float) -> bool:
    return a is not None and b is not None and abs(a - b) <= tol


def _status_view(frame: pd.DataFrame, settings: Mapping[str, Any]) -> dict[str, Any]:
    from src.quant.screener import active_distribution, market_status  # noqa: PLC0415

    m = market_status(frame, settings)
    flags = active_distribution(frame, settings)
    ftd = m.get("ftd") or {}
    last = ftd.get("last_ftd") or {}
    return {"status": m.get("status"), "distribution_days": m.get("distribution_days"),
            "distribution_dates": [str(frame["trade_date"].iloc[i]) for i in flags[flags].index],
            "ftd_state": ftd.get("state"), "ftd_date": ftd.get("date"), "rally_start": ftd.get("rally_start"),
            "rally_low_date": ftd.get("rally_low_date"),
            "last_ftd": {k: last.get(k) for k in ("date", "outcome", "on")} if last else None}


def compare(symbol: str, cfg: Mapping[str, Any], settings: Mapping[str, Any], path: Path | None = None) -> dict[str, Any]:
    tol = float(cfg["tolerance_points"])
    with connect(path) as conn:
        rows = [dict(r) for r in conn.execute(
            "SELECT trade_date, open, high, low, close, volume, matched_volume, session_high, session_low "
            "FROM index_daily WHERE symbol = ? ORDER BY trade_date", (symbol.upper(),))]
        hose = {r[0]: {"open": r[1], "high": r[2], "low": r[3], "close": r[4]} for r in conn.execute(
            "SELECT trade_date, open, high, low, close FROM hose_index_daily WHERE symbol = ?", (symbol.upper(),))}
    if not rows or not hose:
        return {"symbol": symbol.upper(), "error": "chưa có dữ liệu DNSE hoặc HOSE để so"}
    session = rows[-1]["trade_date"]

    # agreement over the recent sessions
    recent = rows[-int(cfg["sessions"]):]
    agree = {"dnse_ohlc": {"high": 0, "low": 0, "close": 0, "compared": 0},
             "dnse_market_index": {"high": 0, "low": 0, "compared": 0}}
    differ = []
    for r in recent:
        h = hose.get(r["trade_date"])
        if not h:
            continue
        a = agree["dnse_ohlc"]
        a["compared"] += 1
        ok_h, ok_l, ok_c = _same(r["high"], h["high"], tol), _same(r["low"], h["low"], tol), _same(r["close"], h["close"], tol)
        a["high"] += ok_h
        a["low"] += ok_l
        a["close"] += ok_c
        if r["session_high"] is not None:
            b = agree["dnse_market_index"]
            b["compared"] += 1
            b["high"] += _same(r["session_high"], h["high"], tol)
            b["low"] += _same(r["session_low"], h["low"], tol)
        if not (ok_h and ok_l and ok_c):
            differ.append({"date": r["trade_date"], "dnse": [r["high"], r["low"], r["close"]],
                           "hose": [h["high"], h["low"], h["close"]],
                           "market_index": [r["session_high"], r["session_low"]] if r["session_high"] is not None else None})

    # the whole stored history
    stored = {r["trade_date"]: r for r in rows}
    first = rows[0]["trade_date"]
    both = [d for d in stored if d in hose]
    history = {
        "from": first, "compared": len(both),
        "high_differs": sum(not _same(stored[d]["high"], hose[d]["high"], tol) for d in both),
        "low_differs": sum(not _same(stored[d]["low"], hose[d]["low"], tol) for d in both),
        "close_differs": sorted(d for d in both if not _same(stored[d]["close"], hose[d]["close"], tol)),
        "missing_in_dnse": sorted(d for d in hose if d >= first and d not in stored),
    }

    # the rules with HOSE's high / low in place of the stored ones (the closes agree recently)
    tail = pd.DataFrame(rows[-int(cfg["status_sessions"]):])
    frame = tail[["trade_date", "close", "volume", "low", "high", "matched_volume"]].copy()
    alt = frame.copy()
    alt["high"] = [hose.get(d, {}).get("high", h) for d, h in zip(alt["trade_date"], alt["high"])]
    alt["low"] = [hose.get(d, {}).get("low", lo) for d, lo in zip(alt["trade_date"], alt["low"])]
    now_view, hose_view = _status_view(frame, settings), _status_view(alt, settings)
    changed = sorted(k for k in now_view if now_view[k] != hose_view[k])
    return {"symbol": symbol.upper(), "session": session, "sessions": len(recent), "tolerance_points": tol,
            "agreement_recent": agree, "differ_recent": differ, "history": history,
            "rules": {"as_stored": now_view, "with_hose_high_low": hose_view, "changed": changed}}


def write_report(result: Mapping[str, Any], directory: Path | None = None) -> Path:
    target = report_path(str(result["session"]), directory)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return target
