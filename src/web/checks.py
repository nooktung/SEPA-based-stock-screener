"""Invariants over what the dashboard shows, checked on the real data.

Unit tests prove the rules on made-up bars; two errors still reached the user in the
tester's second report because nothing checked the stored numbers: stops rounded down
to an 8.4% risk (M03) and "since the screen" read across an ex-rights rescale (N01).
`python -m src.main check` reads the latest monitor state of every monitored ticker and
the recent screen runs, and writes data/audit/invariants_<session>.json:

  risk_8pct            (pivot - stop) / pivot <= base.max_stop_pct
  legs                 no base counts more than vcp.max_contractions contractions (0 is
                       possible: a base from a left-side high whose top has not pulled back 3%)
  breakout_above_pivot "Vừa breakout" only with the close at or above the pivot
  breached_flag        a close at or below the stop carries stop_breached
  stop_needs_base      only a forming base has a stop
  on_tick              pivot, stop and the nearest zones sit on the exchange's tick grid
  since_screen         a move beyond since_screen_pct since a screen run, across a recorded
                       rescale, means the comparison mixed price scales

A ticker still carrying a pivot / stop from the earlier base rules (no `base_rules`, or
`legacy_hold`) is listed as "in transition", not as a violation. The base rules (risk, legs,
tick grid, no stop without a base) are also checked on every ticker of the market, read
afresh -- the monitor covers only the focus list. Read-only.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from src.config import PROJECT_ROOT, load_config
from src.database import connect
from src.quant.screener import FORMING_BASES, round_to_tick, screen_settings

REPORT_DIR = PROJECT_ROOT / "data" / "audit"
TEXT = {
    "risk_8pct": "rủi ro từ pivot tới stop vượt mức tối đa",
    "legs": "số nhịp co hẹp vượt mức tối đa",
    "breakout_above_pivot": "'Vừa breakout' nhưng giá dưới pivot",
    "breached_flag": "giá ≤ stop nhưng không có cờ đã thủng",
    "stop_needs_base": "có stop dù không có nền đạt chuẩn",
    "on_tick": "mức giá không nằm đúng bước giá",
    "since_screen": "'từ ngày lọc' lệch lớn qua một lần điều chỉnh quyền",
}


def _on_tick(value: float | None, exchange: str | None) -> bool:
    if value is None or not exchange:
        return True
    return abs(round_to_tick(value, exchange, up=False) - value) < 1e-6


def state_violations(state: Mapping[str, Any], exchange: str | None, settings: Mapping[str, Any]) -> list[tuple[str, str]]:
    """(rule, detail) for one ticker's monitor state."""
    b = settings["base"]
    out: list[tuple[str, str]] = []
    close, pivot, stop = state.get("close"), state.get("pivot"), state.get("stop")
    kind = state.get("base_kind")
    if pivot and stop:
        risk = (pivot - stop) / pivot * 100.0
        if risk > float(b["max_stop_pct"]) + 1e-9:
            out.append(("risk_8pct", f"pivot {pivot:,.0f}, stop {stop:,.0f}: {risk:.2f}%"))
    legs = len(state.get("contractions") or [])
    if legs > int(b["vcp"]["max_contractions"]):
        out.append(("legs", f"{kind}: {legs} nhịp"))
    status = (state.get("setup") or {}).get("status")
    if status == "BREAKOUT" and pivot and close is not None and close < pivot:
        out.append(("breakout_above_pivot", f"giá {close:,.0f} < pivot {pivot:,.0f}"))
    if stop and close is not None and close <= stop and not state.get("stop_breached"):
        out.append(("breached_flag", f"giá {close:,.0f} ≤ stop {stop:,.0f}"))
    if stop and kind not in FORMING_BASES:
        out.append(("stop_needs_base", f"{kind}, stop {stop:,.0f}"))
    for name, value in (("pivot", pivot), ("stop", stop)):
        if not _on_tick(value, exchange):
            out.append(("on_tick", f"{name} {value}"))
    return out


def run_checks(path: Path | None = None, thresholds: Mapping[str, Any] | None = None,
               since_screen_pct: float = 25.0, runs: int = 20, zones: bool = True,
               market: bool = True) -> dict[str, Any]:
    from src.quant.channel import chart_levels  # noqa: PLC0415
    from src.quant.monitor import monitored_tickers  # noqa: PLC0415

    th = thresholds or load_config().thresholds.raw
    settings = screen_settings(th)
    tickers, _, _ = monitored_tickers(path)
    violations: list[dict[str, Any]] = []
    transition: list[dict[str, Any]] = []
    marks = ",".join("?" * len(tickers)) or "''"
    with connect(path) as conn:
        exchanges = dict(conn.execute("SELECT ticker, exchange FROM tickers"))
        states = {t: (d, json.loads(j)) for t, d, j in conn.execute(
            f"SELECT s.ticker, s.trade_date, s.state_json FROM monitor_state s JOIN (SELECT ticker, MAX(trade_date) d "
            f"FROM monitor_state WHERE ticker IN ({marks}) GROUP BY ticker) x ON x.ticker = s.ticker AND x.d = s.trade_date",
            tuple(tickers))}
        actions: dict[str, list[str]] = {}
        for t, through in conn.execute("SELECT ticker, through_session FROM price_adjustments"):
            actions.setdefault(t, []).append(str(through))
        recent = conn.execute("SELECT run_id, as_of_date FROM screen_runs ORDER BY created_at DESC LIMIT ?",
                              (runs,)).fetchall()
    session = max((d for d, _ in states.values()), default=None)
    for t, (day, state) in sorted(states.items()):
        found = state_violations(state, exchanges.get(t), settings)
        if not found:
            continue
        rows = [{"ticker": t, "rule": r, "text": TEXT[r], "detail": d, "session": day} for r, d in found]
        if not state.get("base_rules") or state.get("legacy_hold"):
            transition += rows                         # numbers still from the earlier base rules
        else:
            violations += rows

    if zones:
        for t in tickers:
            ex = exchanges.get(t)
            frame = _frame(t, path)
            if ex is None or frame is None:
                continue
            levels = chart_levels(frame, th, ex)
            for z in levels["zones"] + ([levels["at_zone"]] if levels.get("at_zone") else []):
                for name in ("center", "zone_low", "zone_high"):
                    if not _on_tick(z[name], ex):
                        violations.append({"ticker": t, "rule": "on_tick", "text": TEXT["on_tick"],
                                           "detail": f"vùng {z['side']} {name} {z[name]}", "session": session})

    # "Since the screen" on the dashboard (web.server.api_stocks), for the recent runs but the latest.
    from src.web.server import api_stocks  # noqa: PLC0415

    info: list[dict[str, Any]] = []
    seen_days = {recent[0][1]} if recent else set()
    for run_id, as_of in recent[1:]:
        if as_of in seen_days:
            continue                                   # several runs of one session: the latest stands for it
        seen_days.add(as_of)
        for row in api_stocks({"run_id": run_id}, path):
            since = row.get("since_screen") or {}
            pct = since.get("pct")
            if pct is None or abs(pct) <= since_screen_pct:
                continue
            across = [a for a in actions.get(row["ticker"], []) if a >= str(as_of)]
            item = {"ticker": row["ticker"], "rule": "since_screen", "text": TEXT["since_screen"],
                    "detail": f"lượt {as_of}: {pct:+.1f}%", "session": session}
            if across:
                violations.append({**item, "detail": item["detail"] + f", quyền sau phiên {across[0]}"})
            else:
                info.append({**item, "text": f"'từ ngày lọc' lệch hơn {since_screen_pct:.0f}% (không có quyền xen giữa)"})

    market_checked = 0
    if market and session:
        market_checked = _market_base_checks(settings, session, violations, path)

    by_rule: dict[str, int] = {}
    for v in violations:
        by_rule[v["rule"]] = by_rule.get(v["rule"], 0) + 1
    return {"session": session, "created_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            "checked": len(states), "market_checked": market_checked,
            "violations": violations, "transition": transition, "info": info,
            "by_rule": by_rule, "rules": TEXT}


def _market_base_checks(settings: Mapping[str, Any], session: str, violations: list[dict[str, Any]],
                        path: Path | None) -> int:
    """The base rules on every ticker with clean data, read as the market view reads it."""
    from datetime import date  # noqa: PLC0415

    from src.quant.screener import _load_universe_frames, read_base  # noqa: PLC0415

    frames, exchanges, stale = _load_universe_frames(date.fromisoformat(session),
                                                     int(settings["min_history_sessions"]) + 120, path)
    seen = {v["ticker"] for v in violations}
    n = 0
    for t, f in frames.items():
        if t in stale or t not in exchanges or len(f) < 30:
            continue
        b = read_base(f, exchanges[t], settings)
        n += 1
        state = {"close": float(f["close"].iloc[-1]), "pivot": b.pivot, "stop": b.stop, "base_kind": b.kind,
                 "contractions": b.contractions, "stop_breached": bool(b.stop) and float(f["close"].iloc[-1]) <= b.stop}
        for rule, detail in state_violations(state, exchanges[t], settings):
            if rule in ("risk_8pct", "legs", "on_tick", "stop_needs_base") and t not in seen:
                violations.append({"ticker": t, "rule": rule, "text": TEXT[rule] + " (toàn thị trường)",
                                   "detail": detail, "session": f.index[-1].date().isoformat()})
    return n


def _frame(ticker: str, path: Path | None) -> pd.DataFrame | None:
    with connect(path) as conn:
        rows = conn.execute("SELECT trade_date, open, high, low, close, volume FROM (SELECT * FROM prices_daily "
                            "WHERE ticker = ? ORDER BY trade_date DESC LIMIT 520) ORDER BY trade_date",
                            (ticker,)).fetchall()
    if len(rows) < 30:
        return None
    df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume"])
    return df.set_index(pd.to_datetime(df["trade_date"])).drop(columns="trade_date").astype("float64")


def report_path(session: str | None) -> Path:
    return REPORT_DIR / f"invariants_{session or 'none'}.json"


def write_report(report: Mapping[str, Any]) -> Path:
    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    out = report_path(report.get("session"))
    out.write_text(json.dumps(report, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    return out


def latest_report() -> dict[str, Any] | None:
    files = sorted(REPORT_DIR.glob("invariants_*.json")) if REPORT_DIR.exists() else []
    if not files:
        return None
    try:
        return json.loads(files[-1].read_text(encoding="utf-8"))
    except ValueError:
        return None


def summary(report: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """What the dashboard line needs: counts, plus the rows."""
    if not report:
        return None
    return {"session": report.get("session"), "created_at": report.get("created_at"),
            "checked": report.get("checked"), "market_checked": report.get("market_checked"),
            "violations": len(report.get("violations") or []),
            "transition": len({r["ticker"] for r in report.get("transition") or []}),
            "rows": report.get("violations") or [], "transition_rows": report.get("transition") or [],
            "info_rows": report.get("info") or []}
