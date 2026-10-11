"""Whole-market snapshot for the dashboard's "Toàn thị trường" view (display only).

Every listed ticker with stored prices, one row each: last price and change,
liquidity, the Trend Template, price against MA50/150/200, distance from the
52-week high and the base read -- the same functions the screen uses
(`src/quant/screener.py`), measured on each ticker's own latest session.

It is a research table, not a candidate list: nothing here changes a screen
run, a score or a group. Rules carried over from the screen:

* RS is the latest screen run's rating (one scale across the dashboard). A
  ticker below the liquidity floor has no RS, so TT8 is "not assessable",
  never a fail.
* A series flagged in `data_freshness` (an unadjusted corporate action, a
  re-listing) gets its price only -- no indicator is computed on data known
  to be suspect.
* A ticker with less than `min_history_sessions` has only TT7 measured, like
  a new listing in the monitor.

`python -m src.main market` builds and stores it (`market_snapshot`); the
daily job runs it after the whole-market backfill. Settings: `market_view`.
"""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from src.config import load_config
from src.database import connect
from src.quant import indicators as ind
from src.quant.screener import (_load_universe_frames, liquidity, liquidity_tier, read_base,
                                screen_settings, trend_template)
from src.quant.themes import load_themes

INDUSTRY_LEVEL_COLUMN = {1: "sector", 2: "industry_group", 3: "industry"}


def _latest_run(conn) -> tuple[dict[str, float], dict[str, float], set[str], str | None]:
    """RS of every rated ticker, industry RS percentiles, hot themes and the run's session date,
    from the latest screen run. The date travels with each row (`rs_from`): a snapshot built
    before a newer run would otherwise show the older RS under the newer date (tester, 09/10)."""
    row = conn.execute("SELECT funnel_json, as_of_date FROM screen_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    funnel = json.loads(row[0] or "{}") if row else {}
    industries = {i["name"]: i.get("rs_percentile") for i in funnel.get("industries") or []}
    hot = {t["name"] for t in funnel.get("themes") or [] if t.get("hot")}
    return funnel.get("rs_ratings") or {}, industries, hot, (row[1] if row else None)


def ticker_row(ticker: str, exchange: str, frame: pd.DataFrame, rs: float | None, flagged: bool,
               settings: Mapping[str, Any], stored_sessions: int | None = None) -> dict[str, Any]:
    """One ticker's row: price facts always, indicators only on data that is not flagged.
    `stored_sessions`: every session stored for it, which tells a recent listing from a stock
    that trades on few days (tester N04: BHP read "mới niêm yết")."""
    close = frame["close"].astype("float64")
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) > 1 else None
    value, _ = liquidity(frame, settings)
    floor = float(settings["liquidity"]["min_value_vnd"].get(exchange.upper(),
                                                             max(settings["liquidity"]["min_value_vnd"].values())))
    row: dict[str, Any] = {
        "ticker": ticker, "exchange": exchange, "trade_date": frame.index[-1].date().isoformat(),
        "close": round(last, 2), "change_pct": round((last / prev - 1) * 100, 2) if prev else None,
        "liquidity_vnd": round(value) if value is not None else None,
        "liquidity_tier": (liquidity_tier(value, settings) if value is not None and value >= floor
                           else ("UNDER" if value is not None else None)),
        "rs_rating": rs, "flagged": flagged, "sessions": len(frame),
    }
    if flagged:
        return row

    min_hist = int(settings["min_history_sessions"])
    if len(frame) < min_hist:
        row["history"] = "SPARSE" if (stored_sessions or 0) >= min_hist else "NEW"
    criteria = trend_template(frame, rs, settings)
    for code, c in criteria.items():
        if len(frame) < min_hist and code != "TT7":
            c.assessable = False                       # a recent listing: only TT7 is measurable
        elif code == "TT8" and rs is None:
            c.assessable = False                       # no RS: flagged data or not enough history
    row["tt_passed"] = sum(1 for c in criteria.values() if c.assessable and c.passed)
    row["missing"] = [k for k, c in criteria.items() if c.assessable and not c.passed]
    row["not_assessable"] = [k for k, c in criteria.items() if not c.assessable]

    mas = {n: ind.sma(close, n).iloc[-1] for n in (50, 150, 200)}
    row["above_ma"] = {str(n): (None if v != v else bool(last > float(v))) for n, v in mas.items()}
    window = frame.tail(int(settings["lookback_52w_sessions"]))
    high = float(window["high"].max())
    row["pct_from_high"] = round((last / high - 1) * 100, 2) if high else None

    base = read_base(frame, exchange, settings)
    row.update({"base_kind": base.kind, "pivot": base.pivot,
                "distance": round(base.distance_to_pivot_pct, 2) if base.distance_to_pivot_pct is not None else None})
    return row


def build_snapshot(as_of: date | None = None, path: Path | None = None,
                   thresholds: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    settings = screen_settings(thresholds)
    as_of = as_of or datetime.now(timezone.utc).date()
    frames, exchanges, stale = _load_universe_frames(as_of, int(settings["min_history_sessions"]) + 120, path)
    level = int((settings.get("industry") or {}).get("level", 2))
    with connect(path) as conn:
        rs_all, industry_rs, hot, rs_from = _latest_run(conn)
        names = {t: n for t, n in conn.execute("SELECT ticker, name FROM tickers")}
        ind_rows = {r["ticker"]: r for r in conn.execute("SELECT * FROM ticker_industry")}
        graded = {r[0] for r in conn.execute("SELECT DISTINCT ticker FROM financial_metrics WHERE period_type = 'Q'")}
        flag_notes = dict(conn.execute("SELECT scope_key, note FROM data_freshness "
                                       "WHERE dataset = 'prices_daily' AND is_stale = 1").fetchall())
        stored = dict(conn.execute("SELECT ticker, COUNT(*) FROM prices_daily WHERE trade_date <= ? GROUP BY ticker",
                                   (as_of.isoformat(),)).fetchall())
    themes = load_themes()
    rows = []
    for t, frame in frames.items():
        if frame.empty or t not in exchanges:
            continue
        row = ticker_row(t, exchanges[t], frame, rs_all.get(t), t in stale, settings, stored.get(t))
        row["name"] = names.get(t)
        row["rs_from"] = rs_from
        if row["flagged"]:
            row["flag_note"] = flag_notes.get(t)
        ir = ind_rows.get(t)
        ind_name = (ir[INDUSTRY_LEVEL_COLUMN.get(level, "industry_group")] or ir["sector"]) if ir else None
        row["industry"] = {"name": ind_name, "rs_percentile": industry_rs.get(ind_name)}
        row["themes"] = [{"name": n, "hot": n in hot} for n, members in themes.items() if t in members]
        if t in graded and "fundamentals" in settings:
            from src.quant.fundamentals import grade_ticker  # noqa: PLC0415 - avoids an import cycle

            f = grade_ticker(t, settings, path).as_dict()
            row["fund"] = {k: f.get(k) for k in ("grade", "grade_text", "reasons", "profit_yoy", "revenue_yoy")}
        else:
            row["fund"] = {"grade": "NO_DATA"}
        rows.append(row)
    rows.sort(key=lambda r: r["ticker"])
    return rows


def store_snapshot(rows: list[dict[str, Any]], path: Path | None = None, keep: int | None = None) -> str | None:
    """Replace the snapshot of the newest price date in `rows`; keep the last `keep` dates."""
    if not rows:
        return None
    keep = keep or int(load_config().thresholds["market_view"]["keep_snapshots"])
    as_of = max(r["trade_date"] for r in rows)
    now = datetime.now(timezone.utc).isoformat()
    with connect(path) as conn:
        conn.execute("DELETE FROM market_snapshot WHERE as_of_date = ?", (as_of,))
        conn.executemany("INSERT INTO market_snapshot (as_of_date, ticker, row_json, created_at) VALUES (?, ?, ?, ?)",
                         [(as_of, r["ticker"], json.dumps(r, ensure_ascii=False), now) for r in rows])
        dates = [d for (d,) in conn.execute("SELECT DISTINCT as_of_date FROM market_snapshot ORDER BY as_of_date DESC")]
        for old in dates[keep:]:
            conn.execute("DELETE FROM market_snapshot WHERE as_of_date = ?", (old,))
    return as_of


def load_snapshot(path: Path | None = None) -> tuple[str | None, list[dict[str, Any]]]:
    with connect(path) as conn:
        row = conn.execute("SELECT MAX(as_of_date) FROM market_snapshot").fetchone()
        if not row or not row[0]:
            return None, []
        rows = [json.loads(j) for (j,) in conn.execute(
            "SELECT row_json FROM market_snapshot WHERE as_of_date = ? ORDER BY ticker", (row[0],))]
    return row[0], rows
