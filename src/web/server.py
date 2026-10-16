"""Local dashboard: `python -m src.main start`.

Serves the screen results, the daily monitor events and the news for the focus
list from the SQLite database. Standard library only. It binds to 127.0.0.1 and
exposes GET endpoints that read; nothing in it writes, and there is no order
path, by design.
"""

from __future__ import annotations

import json
import logging
import math
import re
import mimetypes
import sys
from datetime import date, datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import parse_qs, urlsplit

import pandas as pd

from src.config import PROJECT_ROOT, load_config
from src.database import connect
from src.collectors import logos, refprices
from src.quant.channel import chart_levels, trend_overlays
from src.quant import indicators as ind
from src.quant.screener import active_distribution, explain_base, market_status, screen_settings
from src.quant.setup_status import market_note

log = logging.getLogger(__name__)
STATIC = Path(__file__).resolve().parent / "static"
VN_TZ = timezone(timedelta(hours=7))


def _clean(value: Any) -> Any:
    """NaN/Infinity are not JSON; browsers reject the whole response over one. Floats are
    rounded to 6 decimals: thousand-scaled DNSE prices arrive as 16149.999999999998 (B13)."""
    if isinstance(value, float):
        return round(value, 6) if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(v) for v in value]
    return value


def _rows(sql: str, params: tuple = (), path: Path | None = None) -> list[dict[str, Any]]:
    with connect(path) as conn:
        return [dict(r) for r in conn.execute(sql, params)]


def _json_cols(row: dict[str, Any], *cols: str) -> dict[str, Any]:
    for c in cols:
        if row.get(c):
            try:
                row[c] = _clean(json.loads(row[c]))
            except ValueError:
                pass
    return row


# ---------------------------------------------------------------------------
# API
# ---------------------------------------------------------------------------

def api_runs(q: dict[str, str], path: Path | None = None) -> Any:
    rows = _rows("SELECT run_id, created_at, as_of_date, funnel_json, market_json, note FROM screen_runs "
                 "ORDER BY created_at DESC LIMIT 200", path=path)
    # Only the latest run of each session date: earlier same-day runs were reruns
    # after rule changes (still stored, append-only, but not worth choosing).
    seen: set[str] = set()
    runs = [r for r in rows if not (r["as_of_date"] in seen or seen.add(r["as_of_date"]))][:30]
    for r in runs:
        _json_cols(r, "funnel_json", "market_json")
        r["members"] = _rows("SELECT COUNT(*) AS n FROM screen_members WHERE run_id = ?", (r["run_id"],), path)[0]["n"]
    return runs


def api_screen(q: dict[str, str], path: Path | None = None) -> Any:
    run_id = q.get("run_id")
    if not run_id:
        latest = _rows("SELECT run_id FROM screen_runs ORDER BY created_at DESC LIMIT 1", path=path)
        if not latest:
            return {"run": None, "members": []}
        run_id = latest[0]["run_id"]
    run = _rows("SELECT * FROM screen_runs WHERE run_id = ?", (run_id,), path)
    if not run:
        raise KeyError(f"no run {run_id}")
    run = _json_cols(run[0], "funnel_json", "market_json")
    run.pop("params_json", None)
    level, note = market_note(run.get("market_json") if isinstance(run.get("market_json"), dict) else {})
    run["market_level"], run["market_note"] = level, note
    # The market as of the latest index bar -- daily updates move it even when the
    # list itself (the screen run) is older.
    idx = _rows(f"SELECT {INDEX_STATUS_COLS} FROM (SELECT * FROM index_daily WHERE symbol = ? "
                "ORDER BY trade_date DESC LIMIT 400) ORDER BY trade_date", (screen_settings()["market"]["index"],), path)
    if idx:
        live = _clean(market_status(pd.DataFrame(idx), screen_settings()))
        live["as_of"] = idx[-1]["trade_date"]
        if len(idx) >= 2 and idx[-2]["close"]:
            live["change_pct"] = round((idx[-1]["close"] / idx[-2]["close"] - 1) * 100, 2)
        live["spark"] = [r["close"] for r in idx[-60:]]
        live["session"] = index_session(idx[-1])
        run["live_market"] = live
        run["live_market_level"], run["live_market_note"] = market_note(live)
    members = _rows("SELECT m.*, t.name FROM screen_members m LEFT JOIN tickers t ON t.ticker = m.ticker "
                    "WHERE m.run_id = ? ORDER BY m.rank", (run_id,), path)
    latest_state = {r["ticker"]: _json_cols(r, "state_json")["state_json"] for r in _rows(
        "SELECT s.ticker, s.state_json FROM monitor_state s JOIN (SELECT ticker, MAX(trade_date) d "
        "FROM monitor_state GROUP BY ticker) x ON x.ticker = s.ticker AND x.d = s.trade_date", path=path)}
    for m in members:
        _json_cols(m, "criteria_json", "base_json")
        crit = {k: v for k, v in (m["criteria_json"] or {}).items() if isinstance(v, dict)}
        m["missing"] = [k for k, v in crit.items() if v.get("pass") is False]
        m["not_assessable"] = [k for k, v in crit.items() if v.get("pass") is None]
        m["today"] = latest_state.get(m["ticker"])
    return {"run": run, "members": members}


def _chart_series(bars: list[dict[str, Any]]) -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    """MA20/50/150/200, RSI 14 and MACD 12/26/9 for a chart, one point per session
    from the first full window on (never a partial average)."""
    ma: dict[str, list[dict[str, Any]]] = {}
    oscillators: dict[str, list[dict[str, Any]]] = {}
    if not bars:
        return ma, oscillators
    close = pd.Series([b["close"] for b in bars], dtype="float64")
    times = [b["trade_date"] for b in bars]

    def points(series: pd.Series, digits: int = 2) -> list[dict[str, Any]]:
        return [{"time": times[i], "value": round(float(v), digits)} for i, v in enumerate(series) if v == v]

    from src.quant.timeframes import periods  # noqa: PLC0415

    for slot, n in periods("D", load_config().thresholds["chart_timeframes"]).items():
        ma[slot] = points(ind.sma(close, n))
    # Same formulas as TradingView's defaults (Wilder RSI 14, MACD 12/26/9 on EMA);
    # checked against TradingView on SSI, 2026-09-26.
    oscillators["RSI"] = points(ind.rsi(close, 14))
    macd_line, signal, hist = ind.macd(close, 12, 26, 9)
    oscillators["MACD"] = points(macd_line, 3)
    oscillators["SIGNAL"] = points(signal, 3)
    oscillators["HIST"] = points(hist, 3)
    return ma, oscillators


INDEX_NAMES = {"VNINDEX": "VN-Index", "VN30": "VN30", "HNXINDEX": "HNX-Index"}


def _rs_line(bars: list[dict[str, Any]], tf: str, path: Path | None) -> dict[str, Any] | None:
    """The stock's RS line against the market index on these bars (src/quant/rs_line.py)."""
    from src.quant import rs_line as rsl  # noqa: PLC0415

    if not bars:
        return None
    symbol = screen_settings()["market"]["index"]
    closes = dict((r["trade_date"], r["close"]) for r in _rows(
        "SELECT trade_date, close FROM index_daily WHERE symbol = ? AND trade_date >= ?",
        (symbol, bars[0]["trade_date"]), path))
    lookback, trend = rsl.settings(load_config().thresholds, tf)
    out = rsl.rs_line(bars, closes, lookback, trend, "trade_date" if tf == "D" else "period_end")
    if out:
        out["index"] = INDEX_NAMES.get(symbol, symbol)
    return out


def _daily_periods() -> dict[str, int]:
    from src.quant.timeframes import periods  # noqa: PLC0415

    return periods("D", load_config().thresholds["chart_timeframes"])


def api_bars(q: dict[str, str], path: Path | None = None) -> Any:
    """Weekly ("W") or monthly ("M") bars with their MAs, RSI and MACD, from the whole stored
    history (daily bars come with /api/ticker and /api/index)."""
    from src.quant import timeframes as tfm  # noqa: PLC0415

    tf = (q.get("tf") or "W").upper()
    if tf not in ("W", "M"):
        raise KeyError("khung thời gian phải là W (tuần) hoặc M (tháng)")
    index, ticker = (q.get("index") or "").upper(), (q.get("ticker") or "").upper()
    if index:
        if index not in INDEX_NAMES:
            raise KeyError(f"không có chỉ số {index}")
        # volume as on the daily index chart: matched trading where market-index has it
        vol = ("COALESCE(matched_volume, volume)" if screen_settings()["market"].get("index_volume") == "matched"
               else "volume")
        rows = _rows(f"SELECT trade_date, open, high, low, close, {vol} AS volume FROM index_daily WHERE symbol = ? "
                     "ORDER BY trade_date", (index,), path)
    elif ticker:
        rows = _rows("SELECT trade_date, open, high, low, close, volume FROM prices_daily WHERE ticker = ? "
                     "ORDER BY trade_date", (ticker,), path)
    else:
        raise KeyError("cần ticker hoặc index")
    if not rows:
        raise KeyError(f"chưa có dữ liệu giá cho {index or ticker}")
    bars = tfm.resample(rows, tf, today=datetime.now(VN_TZ).date())
    per = tfm.periods(tf, load_config().thresholds["chart_timeframes"])
    ma, osc = tfm.series(bars, per)
    # Trendlines and the channel on these candles; the zones stay the daily chart's.
    levels = None
    if len(bars) >= 10:
        try:
            frame = pd.DataFrame(bars).set_index(pd.to_datetime([b["trade_date"] for b in bars]))
            frame = frame[["open", "high", "low", "close", "volume"]].astype("float64")
            levels = trend_overlays(frame, load_config().thresholds["chart_levels"]["trendline_by_tf"][tf], tf)
            levels["note"] = (f"Trendline và kênh đo trên nến {tfm.TIMEFRAMES[tf]}; vùng hỗ trợ / kháng cự lấy từ "
                              "biểu đồ ngày. Mô tả nơi giá từng đảo chiều, không phải dự báo; phần tia kéo dài "
                              "về tương lai là ước tính.")
        except Exception:  # noqa: BLE001 - the chart must still load without the overlays
            log.exception("%s levels for %s", tf, index or ticker)
    return {"tf": tf, "bars": bars, "ma": ma, "osc": osc, "ma_periods": per, "levels": levels,
            "rs_line": _rs_line(bars, tf, path) if ticker else None}


# Index columns the market status reads (distribution days, FTD) plus the session's breadth.
INDEX_STATUS_COLS = ("trade_date, close, volume, low, high, matched_volume, put_through_volume, matched_value_vnd, "
                     "put_through_value_vnd, advancers, unchanged, decliners, ceilings, floors")


def index_session(row: Mapping[str, Any]) -> dict[str, Any] | None:
    """Breadth and the volume split of one index session (DNSE market-index), or None without them."""
    if row.get("matched_volume") is None and row.get("advancers") is None:
        return None
    return {k: row.get(k) for k in ("trade_date", "matched_volume", "put_through_volume", "matched_value_vnd",
                                    "put_through_value_vnd", "advancers", "unchanged", "decliners", "ceilings", "floors")}


def api_index(q: dict[str, str], path: Path | None = None) -> Any:
    """The market index for the dashboard's VN-Index chart: bars, MAs, RSI, MACD, the
    nearest zones and trendlines, the distribution days of the current window and the FTD."""
    settings = screen_settings()
    symbol = (q.get("symbol") or settings["market"]["index"]).upper()
    if symbol not in INDEX_NAMES:
        raise KeyError(f"không có chỉ số {symbol}")
    rows = _rows("SELECT trade_date, open, high, low, close, volume, matched_volume, put_through_volume, "
                 "matched_value_vnd, put_through_value_vnd, advancers, unchanged, decliners, ceilings, floors "
                 "FROM (SELECT * FROM index_daily WHERE symbol = ? ORDER BY trade_date DESC LIMIT 520) "
                 "ORDER BY trade_date", (symbol,), path)
    if not rows:
        raise KeyError(f"chưa có dữ liệu chỉ số {symbol}")
    # The volume bars show what distribution days compare: matched volume where DNSE's
    # market-index has it, the OHLC volume (with put-through deals) before that.
    matched = str(settings["market"].get("index_volume", "total")) == "matched"
    bars = [{"trade_date": r["trade_date"], "open": r["open"], "high": r["high"], "low": r["low"], "close": r["close"],
             "volume": r["matched_volume"] if matched and r["matched_volume"] is not None else r["volume"]}
            for r in rows]
    ma, oscillators = _chart_series(bars)
    frame = pd.DataFrame([{k: r[k] for k in ("trade_date", "close", "volume", "low", "high", "matched_volume")}
                          for r in rows])
    market = _clean(market_status(frame, settings))
    level, note = market_note(market)
    # Only the distribution days still counted (25 sessions, or until a 5% rally): congthuc.md §11.
    flags = active_distribution(frame, settings)
    levels = None
    if len(bars) >= 30:
        indexed = frame.set_index(pd.to_datetime(frame["trade_date"])).drop(columns="trade_date").astype("float64")
        try:
            levels = chart_levels(indexed, load_config().thresholds)
        except Exception:  # noqa: BLE001 - the chart must still load without the overlays
            log.exception("chart levels for %s", symbol)
    return {
        "info": {"ticker": symbol, "name": INDEX_NAMES[symbol], "kind": "index"},
        "bars": bars, "ma": ma, "osc": oscillators, "levels": levels, "base": None, "state": None,
        "decimals": 2, "market": market, "market_level": level, "market_note": note, "tf": "D",
        "ma_periods": _daily_periods(),
        "distribution_dates": [frame["trade_date"].iloc[i] for i in flags[flags].index],
        "distribution_min_drop_pct": float(settings["market"]["distribution_min_drop_pct"]),
        "session": index_session(rows[-1]),
    }


def api_ticker(q: dict[str, str], path: Path | None = None) -> Any:
    ticker = (q.get("ticker") or "").upper()
    if not ticker:
        raise KeyError("ticker is required")
    bars = _rows("SELECT trade_date, open, high, low, close, volume FROM (SELECT * FROM prices_daily "
                 "WHERE ticker = ? ORDER BY trade_date DESC LIMIT 520) ORDER BY trade_date", (ticker,), path)
    info = _rows("SELECT ticker, exchange, name, listed_date FROM tickers WHERE ticker = ?", (ticker,), path)
    if not bars:
        raise KeyError(f"không có dữ liệu giá cho mã {ticker}" if info else f"không có mã {ticker}")
    ma, oscillators = _chart_series(bars)
    base_overlay = None
    levels = None
    if len(bars) >= 30 and info:
        frame = pd.DataFrame(bars).set_index(pd.to_datetime([b["trade_date"] for b in bars]))
        frame = frame.drop(columns="trade_date").astype("float64")
        base_overlay = explain_base(frame, info[0]["exchange"], screen_settings())
        try:
            levels = chart_levels(frame, load_config().thresholds, info[0]["exchange"])
        except Exception:  # noqa: BLE001 - the chart must still load without the overlays
            log.exception("chart levels for %s", ticker)
    state = _rows("SELECT trade_date, state_json FROM monitor_state WHERE ticker = ? ORDER BY trade_date DESC "
                  "LIMIT 1", (ticker,), path)
    if state and base_overlay is not None:
        st = json.loads(state[0]["state_json"] or "{}")
        if st.get("legacy_hold") and state[0]["trade_date"] == bars[-1]["trade_date"]:
            # The list shows the pivot / stop held from the earlier base rules (monitor.legacy_hold); so does the chart.
            base_overlay.update({k: st.get(k) for k in ("pivot", "stop", "held_since", "held_by", "stop_breached",
                                                        "stop_breached_on", "stop_pending", "legacy_until", "legacy_new")})
            if st.get("base_kind"):
                base_overlay["kind"] = st["base_kind"]      # GMD: list "Nền", chart "VCP" (R3-04)
            base_overlay["legacy_hold"] = True
            pivot = base_overlay.get("pivot")
            base_overlay["distance_to_pivot_pct"] = round((bars[-1]["close"] / pivot - 1) * 100, 2) if pivot else None
    # Reference prices of the recent sessions: the header's and the legend's % follow the boards.
    refs = refprices.lookup([(ticker, b["trade_date"]) for b in bars[-60:]], path)
    closes = {b["trade_date"]: b["close"] for b in bars[-60:]}
    member = _rows("SELECT m.* FROM screen_members m JOIN screen_runs r ON r.run_id = m.run_id "
                   "WHERE m.ticker = ? ORDER BY r.created_at DESC LIMIT 1", (ticker,), path)
    alerts = _fill_thresholds(_rows("SELECT alert_type, what_changed, why_it_matters, created_at FROM alerts "
                                    "WHERE ticker = ? ORDER BY what_changed DESC LIMIT 40", (ticker,), path))
    # News about the ticker first, then articles that only mention it (tester report B06).
    news = _rows("SELECT e.event_id, e.event_type, e.headline, e.occurred_at, e.article_count, e.best_source_level, "
                 "e.assertion_type, e.what_happened, e.why_it_matters, e.direction, e.time_horizon, t.role "
                 "FROM news_events e JOIN event_tickers t ON t.event_id = e.event_id WHERE t.ticker = ? "
                 "ORDER BY t.role = 'mention', e.occurred_at DESC LIMIT 40", (ticker,), path)
    fundamentals = None
    try:
        from src.quant.fundamentals import grade_ticker, load_quarters  # noqa: PLC0415

        fundamentals = grade_ticker(ticker, screen_settings(), path).as_dict()
        quarters = load_quarters(ticker, path)
        fundamentals["quarters"] = [
            {"period_end": d.isoformat(), "revenue_net": v.get("revenue_net"),
             "net_income_parent": v.get("net_income_parent")} for d, v in sorted(quarters.items(), reverse=True)[:8]]
    except Exception:  # noqa: BLE001 - the chart must still load without the fundamentals tables
        log.exception("fundamentals for %s", ticker)
    industry = _rows("SELECT * FROM ticker_industry WHERE ticker = ?", (ticker,), path)
    for e in news:
        e["articles"] = _rows("SELECT title, url, source_id, source_level, published_at FROM news_articles "
                              "WHERE event_id = ? ORDER BY published_at", (e["event_id"],), path)
    return {
        "info": info[0] if info else {"ticker": ticker},
        "bars": bars,
        "ma": ma,
        "osc": oscillators,
        "base": base_overlay,
        "levels": levels,
        "state": _json_cols(state[0], "state_json")["state_json"] if state else None,
        "screen": _json_cols(member[0], "criteria_json", "base_json") if member else None,
        "alerts": alerts,
        "news": news,
        "tf": "D",
        "ma_periods": _daily_periods(),
        "change": refprices.change(bars[-1]["close"] if bars else None, bars[-2]["close"] if len(bars) > 1 else None,
                                   refs.get((ticker, bars[-1]["trade_date"])) if bars else None,
                                   info[0]["exchange"] if info else None),
        "references": {d: r["reference"] for (_, d), r in refs.items()
                       if refprices.plausible(closes.get(d), r["reference"], info[0]["exchange"] if info else None)},
        # each session's % as the board printed it (the legend shows these)
        "ref_pct": {d: refprices.board_pct(closes[d], r, info[0]["exchange"] if info else None) for (_, d), r in refs.items()
                    if closes.get(d) and refprices.plausible(closes.get(d), r["reference"], info[0]["exchange"] if info else None)},
        "fundamentals": fundamentals,
        "industry": industry[0] if industry else None,
        "rs_line": _rs_line(bars, "D", path),
    }


def api_industries(q: dict[str, str], path: Path | None = None) -> Any:
    run_id = q.get("run_id")
    if run_id:
        rows = _rows("SELECT run_id, as_of_date, funnel_json FROM screen_runs WHERE run_id = ?", (run_id,), path)
    else:
        rows = _rows("SELECT run_id, as_of_date, funnel_json FROM screen_runs ORDER BY created_at DESC LIMIT 1",
                     path=path)
    if not rows:
        return {"run_id": None, "industries": []}
    r = _json_cols(rows[0], "funnel_json")
    return {"run_id": r["run_id"], "as_of_date": r["as_of_date"],
            "industries": (r["funnel_json"] or {}).get("industries", []) if isinstance(r["funnel_json"], dict) else [],
            "themes": (r["funnel_json"] or {}).get("themes", []) if isinstance(r["funnel_json"], dict) else [],
            "level": screen_settings().get("industry", {}).get("level")}


def api_leaders(q: dict[str, str], path: Path | None = None) -> Any:
    run_id = q.get("run_id")
    if not run_id:
        latest = _rows("SELECT run_id FROM screen_runs ORDER BY created_at DESC LIMIT 1", path=path)
        if not latest:
            return []
        run_id = latest[0]["run_id"]
    rows = _rows("SELECT l.detail_json, t.name FROM screen_industry_leaders l LEFT JOIN tickers t ON t.ticker = l.ticker "
                 "WHERE l.run_id = ? ORDER BY l.rowid", (run_id,), path)
    out = []
    for r in rows:
        d = _clean(json.loads(r["detail_json"]))
        d["name"] = r["name"]
        out.append(d)
    return out


def api_mylist(q: dict[str, str], path: Path | None = None) -> Any:
    """The user's picks with today's read: price, trend template, base, list membership."""
    from src.quant.fundamentals import grade_ticker  # noqa: PLC0415
    from src.quant.monitor import compute_state, monitor_settings  # noqa: PLC0415
    from src.quant.screener import LIQUIDITY_TIER_TEXT, liquidity, liquidity_tier  # noqa: PLC0415
    from src.quant.setup_status import assess  # noqa: PLC0415
    from src.quant.themes import load_themes  # noqa: PLC0415
    from src.quant.user_watchlist import entries  # noqa: PLC0415

    screen, mon = screen_settings(), monitor_settings()
    latest = _rows("SELECT run_id, funnel_json, market_json FROM screen_runs ORDER BY created_at DESC LIMIT 1",
                   path=path)
    run_id = latest[0]["run_id"] if latest else None
    funnel = json.loads(latest[0]["funnel_json"] or "{}") if latest else {}
    market = json.loads(latest[0]["market_json"] or "{}") if latest else {}
    rs_all = funnel.get("rs_ratings") or {}
    hot = {t["name"]: t for t in funnel.get("themes") or []}
    members = {r["ticker"]: r["grp"] for r in _rows(
        "SELECT ticker, grp FROM screen_members WHERE run_id = ?", (run_id,), path)} if run_id else {}
    leaders = {r["ticker"]: r["industry"] for r in _rows(
        "SELECT ticker, industry FROM screen_industry_leaders WHERE run_id = ?", (run_id,), path)} if run_id else {}
    themes = load_themes()
    idx = _rows("SELECT trade_date, close FROM (SELECT * FROM index_daily WHERE symbol = ? ORDER BY trade_date DESC "
                "LIMIT 400) ORDER BY trade_date", (screen["market"]["index"],), path)
    index_close = pd.Series([r["close"] for r in idx], index=pd.to_datetime([r["trade_date"] for r in idx]),
                            dtype="float64") if idx else None
    out = []
    for e in entries(path):
        t = e["ticker"]
        info = _rows("SELECT exchange, name FROM tickers WHERE ticker = ?", (t,), path)
        bars = _rows("SELECT trade_date, open, high, low, close, volume FROM (SELECT * FROM prices_daily "
                     "WHERE ticker = ? ORDER BY trade_date DESC LIMIT 400) ORDER BY trade_date", (t,), path)
        row: dict[str, Any] = {**e, "exchange": info[0]["exchange"] if info else None,
                               "name": info[0]["name"] if info else None, "grp": members.get(t),
                               "leader_of": leaders.get(t), "rs_rating": rs_all.get(t),
                               "themes": [{"name": n, "hot": bool((hot.get(n) or {}).get("hot"))}
                                          for n, ts in themes.items() if t in ts]}
        if len(bars) < 30 or not info:
            row["missing"] = "chưa đủ dữ liệu giá"
            out.append(row)
            continue
        frame = pd.DataFrame(bars).set_index(pd.to_datetime([b["trade_date"] for b in bars]))
        frame = frame.drop(columns="trade_date").astype("float64")
        stored = _rows("SELECT trade_date, state_json FROM monitor_state WHERE ticker = ? ORDER BY trade_date DESC "
                       "LIMIT 1", (t,), path)
        if stored and stored[0]["trade_date"] == bars[-1]["trade_date"]:
            state = _json_cols(stored[0], "state_json")["state_json"]
        else:
            # Added since the last monitor run: read it now, without event history.
            st = compute_state(t, info[0]["exchange"], frame, index_close, rs_all.get(t), screen, mon)
            st.setup = assess(members.get(t), st.base_kind, st.distance_to_pivot_pct, screen, market, [],
                              stop_breached=st.stop_breached)
            state = _clean(st.as_dict())
        value, _ = liquidity(frame, screen)
        tier = liquidity_tier(value, screen)
        crit = state.get("criteria") or {}
        row.update({
            "trade_date": bars[-1]["trade_date"], "close": bars[-1]["close"], "change_pct": state.get("change_pct"),
            "tt_passed": sum(1 for v in crit.values() if isinstance(v, dict) and v.get("pass")),
            "missing_criteria": [k for k, v in crit.items() if isinstance(v, dict) and v.get("pass") is False],
            "base_kind": state.get("base_kind"), "pivot": state.get("pivot"),
            "distance_to_pivot_pct": state.get("distance_to_pivot_pct"), "setup": state.get("setup") or {},
            "liquidity_vnd": value, "liquidity_tier": tier,
            "liquidity_text": LIQUIDITY_TIER_TEXT.get(tier) if tier else "dưới ngưỡng lọc",
            "fundamental_grade": grade_ticker(t, screen, path).grade,
        })
        last_alert = _rows("SELECT alert_type, what_changed FROM alerts WHERE ticker = ? ORDER BY what_changed DESC "
                           "LIMIT 1", (t,), path)
        row["last_alert"] = last_alert[0] if last_alert else None
        out.append(row)
    return _board_change(out, path)


def api_position(q: dict[str, str], path: Path | None = None) -> Any:
    """Hold the user's position (average cost, optional quantity) against the SEPA rules."""
    from src.market_calendar import price_limit_pct  # noqa: PLC0415
    from src.quant.fundamentals import grade_ticker  # noqa: PLC0415
    from src.quant.monitor import compute_state, monitor_settings  # noqa: PLC0415
    from src.quant.position_check import check_position  # noqa: PLC0415
    from src.quant.screener import liquidity, liquidity_tier, locked_worst_case_pct  # noqa: PLC0415

    ticker = (q.get("ticker") or "").upper()
    try:
        cost = float(q.get("cost") or 0)
        qty = float(q["qty"]) if q.get("qty") else None
    except ValueError as exc:
        raise KeyError("giá vốn / số lượng không hợp lệ") from exc
    if cost <= 0:
        raise KeyError("cần giá vốn > 0")
    cfg = load_config().thresholds["position_check"]
    screen, mon = screen_settings(), monitor_settings()
    info = _rows("SELECT exchange FROM tickers WHERE ticker = ?", (ticker,), path)
    bars = _rows("SELECT trade_date, open, high, low, close, volume FROM (SELECT * FROM prices_daily "
                 "WHERE ticker = ? ORDER BY trade_date DESC LIMIT 520) ORDER BY trade_date", (ticker,), path)
    if not info or len(bars) < 30:
        raise KeyError(f"chưa đủ dữ liệu giá cho {ticker}")
    exchange = info[0]["exchange"]
    frame = pd.DataFrame(bars).set_index(pd.to_datetime([b["trade_date"] for b in bars]))
    frame = frame.drop(columns="trade_date").astype("float64")
    close = float(bars[-1]["close"])
    limit = float(cfg["cost_unit_check_ratio"])
    if q.get("confirm") != "1" and not (1 / limit <= cost / close <= limit):
        # Far from the price is almost always a unit slip (68.5 typed for 68,500 VND):
        # ask before checking. The x1000 reading is offered only when it lands near the price.
        k = cost * 1000
        return {"check": "unit", "cost": cost, "close": close, "trade_date": bars[-1]["trade_date"],
                "suggest_cost": k if 1 / limit <= k / close <= limit else None}
    idx = _rows(f"SELECT {INDEX_STATUS_COLS} FROM (SELECT * FROM index_daily WHERE symbol = ? "
                "ORDER BY trade_date DESC LIMIT 400) ORDER BY trade_date", (screen["market"]["index"],), path)
    market = market_status(pd.DataFrame(idx), screen) if idx else {}
    _, note = market_note(market)
    stored = _rows("SELECT trade_date, state_json FROM monitor_state WHERE ticker = ? ORDER BY trade_date DESC LIMIT 1",
                   (ticker,), path)
    if stored and stored[0]["trade_date"] == bars[-1]["trade_date"]:
        state = _json_cols(stored[0], "state_json")["state_json"]
    else:
        latest = _rows("SELECT funnel_json FROM screen_runs ORDER BY created_at DESC LIMIT 1", path=path)
        rs = (json.loads(latest[0]["funnel_json"] or "{}").get("rs_ratings") or {}).get(ticker) if latest else None
        index_close = pd.Series([r["close"] for r in idx], index=pd.to_datetime([r["trade_date"] for r in idx]),
                                dtype="float64") if idx else None
        state = _clean(compute_state(ticker, exchange, frame, index_close, rs, screen, mon).as_dict())
    sessions = [b["trade_date"] for b in bars[-int(cfg["signal_lookback_sessions"]):]]
    alerts = []
    for r in _rows("SELECT alert_type, what_changed FROM alerts WHERE ticker = ? ORDER BY what_changed DESC LIMIT 100",
                   (ticker,), path):
        day = (r["what_changed"] or "")[1:11]
        if day in sessions:
            alerts.append((day, r["alert_type"]))
    since = (datetime.now(VN_TZ) - timedelta(days=int(cfg["news_days"]))).isoformat()
    news = _rows("SELECT e.headline, e.occurred_at FROM news_events e JOIN event_tickers t ON t.event_id = e.event_id "
                 "WHERE t.ticker = ? AND t.role = 'subject' AND e.occurred_at >= ? ORDER BY e.occurred_at DESC",
                 (ticker, since), path)
    value, _ = liquidity(frame, screen)
    band = price_limit_pct(exchange)
    try:   # the same nearest zones the chart draws
        zones = chart_levels(frame, load_config().thresholds, exchange)["zones"]
    except Exception:  # noqa: BLE001 -- a zone failure must not hide the rest of the check
        zones = []
    return _clean(check_position(
        cost=cost, qty=qty, frame=frame, trade_date=bars[-1]["trade_date"],
        criteria=state.get("criteria"), setup=state.get("setup"),
        pivot=state.get("pivot"), stop=state.get("stop"), zones=zones, alerts=alerts,
        flows=api_flow({"ticker": ticker, "sessions": "5"}, path)["rows"],
        fundamentals=grade_ticker(ticker, screen, path).as_dict(), market=market, market_note=note, news=news,
        liquidity_tier=liquidity_tier(value, screen), locked_pct=locked_worst_case_pct(band) if band else None, cfg=cfg,
        exchange=exchange))


def api_stocks(q: dict[str, str], path: Path | None = None) -> Any:
    """Every monitored ticker in one shape: the screen run's members (groups A/B/C), the
    industry leaders, the user's picks and the watchlist, each with its latest monitor
    state, technicals, foreign flow and fundamentals. The dashboard's single table."""
    from src.config import load_config as _cfg  # noqa: PLC0415
    from src.quant.fundamentals import grade_ticker  # noqa: PLC0415
    from src.quant.screener import liquidity_tier  # noqa: PLC0415
    from src.quant.themes import load_themes  # noqa: PLC0415
    from src.quant.user_watchlist import entries as my_entries, web_tickers  # noqa: PLC0415

    latest = _rows("SELECT run_id FROM screen_runs ORDER BY created_at DESC LIMIT 1", path=path)
    latest_id = latest[0]["run_id"] if latest else None
    run_id = q.get("run_id") or latest_id
    run = _rows("SELECT funnel_json, as_of_date FROM screen_runs WHERE run_id = ?", (run_id,), path) if run_id else []
    # Viewing an older run: each member's close on that run's session and the change since (tester B12).
    old_run = bool(run) and run_id != latest_id
    funnel = json.loads(run[0]["funnel_json"] or "{}") if run else {}
    rs_all = funnel.get("rs_ratings") or {}
    hot = {t["name"]: bool(t.get("hot")) for t in funnel.get("themes") or []}
    industries = {i["name"]: i for i in funnel.get("industries") or []}
    members = {r["ticker"]: r for r in _rows("SELECT * FROM screen_members WHERE run_id = ? ORDER BY rank",
                                              (run_id,), path)} if run_id else {}
    leaders = {r["ticker"]: r["industry"] for r in _rows(
        "SELECT ticker, industry FROM screen_industry_leaders WHERE run_id = ?", (run_id,), path)} if run_id else {}
    mine = {e["ticker"]: e for e in my_entries(path)}
    watch = set(_cfg().watchlist.active_tickers)
    order = list(members) + [t for t in leaders if t not in members] \
        + [t for t in mine if t not in members and t not in leaders] \
        + sorted(watch - set(members) - set(leaders) - set(mine))
    # The other web accounts' lists (2026-10-14): rows for the web copy, where each account's page
    # picks its own list; here they belong to no group and stay out of every table.
    order += [t for t in web_tickers(path) if t not in set(order)]
    if not order:
        return []
    marks = ",".join("?" * len(order))
    names = {r["ticker"]: r for r in _rows(f"SELECT ticker, exchange, name FROM tickers WHERE ticker IN ({marks})",
                                           tuple(order), path)}
    states = {r["ticker"]: r for r in _rows(
        f"SELECT s.ticker, s.trade_date, s.state_json FROM monitor_state s JOIN (SELECT ticker, MAX(trade_date) d "
        f"FROM monitor_state WHERE ticker IN ({marks}) GROUP BY ticker) x ON x.ticker = s.ticker AND x.d = s.trade_date",
        tuple(order), path)}
    ind_rows = {r["ticker"]: r for r in _rows(f"SELECT * FROM ticker_industry WHERE ticker IN ({marks})", tuple(order), path)}
    # The two newest bars of each ticker: a name that left the monitored list keeps a stopped state
    # but not a stopped price (tester N02: PET showed 51,500 of 05/10 beside others' 09/10).
    last_bars: dict[str, list[dict[str, Any]]] = {}
    for r in _rows(f"SELECT ticker, trade_date, close FROM (SELECT ticker, trade_date, close, ROW_NUMBER() OVER "
                   f"(PARTITION BY ticker ORDER BY trade_date DESC) AS rn FROM prices_daily WHERE ticker IN ({marks})) "
                   f"WHERE rn <= 5 ORDER BY trade_date DESC", tuple(order), path):
        last_bars.setdefault(r["ticker"], []).append(r)
    # An older run's session close from the stored (adjusted) series: screen_members.close is on the
    # price scale of that day, so a stock dividend since then read as a fall (N01: GMD -29.9%).
    screened = {r["ticker"]: r["close"] for r in _rows(
        f"SELECT ticker, close FROM prices_daily WHERE trade_date = ? AND ticker IN ({marks})",
        (run[0]["as_of_date"], *order), path)} if old_run else {}
    level = screen_settings().get("industry", {}).get("level", 2)
    flows: dict[str, list[dict[str, Any]]] = {}
    for r in _rows(f"SELECT f.scope_key, f.trade_date, {_FLOW_COLS}, p.volume AS vol "
                   f"FROM flow_daily f LEFT JOIN prices_daily p ON p.ticker = f.scope_key "
                   f"AND p.trade_date = f.trade_date WHERE f.scope = 'TICKER' AND f.scope_key IN ({marks}) "
                   f"ORDER BY f.trade_date DESC", tuple(order), path):
        flows.setdefault(r["scope_key"], []).append(r)
    themes = load_themes()
    settings = screen_settings()
    out = []
    for t in order:
        m = members.get(t)
        bj = json.loads(m["base_json"] or "{}") if m else {}
        st = json.loads(states[t]["state_json"]) if t in states else {}
        crit = {k: v for k, v in (st.get("criteria") or (json.loads(m["criteria_json"]) if m else {})).items()
                if isinstance(v, dict)}
        ind_row = ind_rows.get(t) or {}
        ind_name = (bj.get("industry") or {}).get("name") or (
            ind_row.get({1: "sector", 2: "industry_group", 3: "industry"}[int(level)]) or ind_row.get("sector"))
        ind_stat = industries.get(ind_name) or {}
        liq = m["liquidity_vnd"] if m else None
        if liq is None:
            avg = _rows("SELECT AVG(close * volume) AS v FROM (SELECT close, volume FROM prices_daily WHERE ticker = ? "
                        "ORDER BY trade_date DESC LIMIT 20)", (t,), path)
            liq = avg[0]["v"] if avg else None
        scale = share_scale(t, path)
        f = [{"trade_date": r["trade_date"], **flow_row(r, scale(r["trade_date"]))} for r in flows.get(t) or []]

        # "5 phiên" = the ticker's last five SESSIONS, not the last five stored rows: DNSE has no
        # snapshot for a session without foreign trading, and ABB's five rows ran 14/09-30/09 (R3-03).
        sessions5 = {b["trade_date"] for b in (last_bars.get(t) or [])}
        last5 = [r["foreign_net"] for r in f if r["trade_date"] in sessions5]
        fund = bj.get("fundamentals") or grade_ticker(t, settings, path).as_dict()
        state_date = states[t]["trade_date"] if t in states else None
        close, change, trade_date = st.get("close", m["close"] if m else None), st.get("change_pct"), state_date
        pivot = st.get("pivot", m["pivot"] if m else None)
        distance = st.get("distance_to_pivot_pct", bj.get("distance_to_pivot_pct"))
        lb = last_bars.get(t) or []
        newer = bool(lb) and (state_date is None or lb[0]["trade_date"] > state_date)
        if newer:
            close, trade_date = lb[0]["close"], lb[0]["trade_date"]
            change = (close / lb[1]["close"] - 1) * 100.0 if len(lb) > 1 and lb[1]["close"] else None
            distance = (close / pivot - 1) * 100.0 if pivot and close else distance
        base_close = screened.get(t)
        out.append(_clean({
            "ticker": t, "name": (names.get(t) or {}).get("name"), "exchange": (names.get(t) or {}).get("exchange"),
            "grp": m["grp"] if m else None, "rank": m["rank"] if m else None, "score": m["score"] if m else None,
            "leader_of": leaders.get(t), "mine": t in mine, "note": (mine.get(t) or {}).get("note"), "watch": t in watch,
            "trade_date": trade_date, "close": close, "change_pct": change,
            # the monitor state (pivot, status...) is of an earlier session than the price
            "state_date": state_date if newer else None,
            "rs_rating": m["rs_rating"] if m else rs_all.get(t), "rs_tier": bj.get("rs_tier"),
            "tt_passed": sum(1 for v in crit.values() if v.get("pass")),
            "missing": [k for k, v in crit.items() if v.get("pass") is False],
            "not_assessable": [k for k, v in crit.items() if v.get("pass") is None],
            "base_kind": st.get("base_kind") or bj.get("kind"), "pivot": pivot,
            "stop": st.get("stop", m["stop"] if m else None),
            "distance": distance,
            "setup": st.get("setup") or bj.get("setup") or {},
            "liquidity_vnd": liq, "liquidity_tier": bj.get("liquidity_tier") or liquidity_tier(liq, settings),
            "fund": {k: fund.get(k) for k in ("grade", "grade_text", "reasons", "profit_yoy", "revenue_yoy")},
            "themes": bj.get("themes") or [{"name": n, "hot": hot.get(n, False)} for n, ts in themes.items() if t in ts],
            "industry": {"name": ind_name, "rs_percentile": ind_stat.get("rs_percentile"), "leading": ind_stat.get("leading")},
            "tech": st.get("tech") or {},
            "flow": {"date": f[0]["trade_date"] if f else None, "net_volume": f[0]["foreign_net"] if f else None,
                     "buy_volume": f[0]["foreign_buy"] if f else None,
                     "sell_volume": f[0]["foreign_sell"] if f else None,
                     "net_value": f[0]["foreign_net_value"] if f else None,
                     "volume": f[0]["volume"] if f else None,
                     "buy_volume_matched": f[0]["foreign_buy_matched"] if f else None,
                     "sell_volume_matched": f[0]["foreign_sell_matched"] if f else None,
                     "net_volume_put_through": f[0]["foreign_net_put_through"] if f else None,
                     "domestic_buy": f[0]["domestic_buy"] if f else None,
                     "domestic_sell": f[0]["domestic_sell"] if f else None,
                     "net5_volume": sum(v for v in last5 if v is not None) if any(v is not None for v in last5) else None,
                     "net5_value": (sum(r["foreign_net_value"] or 0 for r in f if r["trade_date"] in sessions5)
                                    if last5 else None),
                     "net5_sessions": sum(1 for v in last5 if v is not None), "net5_of": len(sessions5)},
            # From the base shown today (held after a breakout or a breach), not the screen run's:
            # the loss from a buy at the pivot to the stop. A close at or below the stop is
            # `stop_breached`, and then there is no "risk to stop" left to show.
            "risk_to_stop_pct": _risk_to_stop(st, m),
            "stop_breached": bool(st.get("stop_breached")), "stop_breached_on": st.get("stop_breached_on"),
            "stop_pending": bool(st.get("stop_pending")),
            "held_since": st.get("held_since"), "held_by": st.get("held_by"), "legacy_hold": bool(st.get("legacy_hold")),
            "legacy_until": st.get("legacy_until"), "legacy_new": st.get("legacy_new"),
            "locked_worst_case_pct": m["locked_worst_case_pct"] if m else None,
            "demoted": bj.get("demoted"),
            "since_screen": ({"date": run[0]["as_of_date"], "close": base_close,
                              "pct": round((close / base_close - 1) * 100.0, 2) if close and base_close else None}
                             if old_run and m else None),
        }))
    return _board_change(out, path)


def _risk_to_stop(state: Mapping[str, Any], member: Mapping[str, Any] | None) -> float | None:
    """(pivot - stop) / pivot of the base shown today; None once the stop is breached."""
    if state.get("stop_breached"):
        return None
    pivot, stop = state.get("pivot"), state.get("stop")
    if pivot and stop:
        return round((pivot - stop) / pivot * 100.0, 2)
    return member["risk_to_stop_pct"] if member else None


def _board_change(rows: list[dict[str, Any]], path: Path | None) -> list[dict[str, Any]]:
    """% change as the price boards show it: against the session's reference price when one is
    stored (python -m src.main refprices), else against the previous close -- `change_basis` says which.
    UPCoM's reference is the previous session's average price, so there the two differ."""
    refs = refprices.lookup([(r["ticker"], r["trade_date"]) for r in rows if r.get("ticker") and r.get("trade_date")], path)
    for r in rows:
        ref = refs.get((str(r.get("ticker", "")).upper(), r.get("trade_date")))
        r["change_close_pct"] = r.get("change_pct")
        if ref and r.get("close") is not None and refprices.plausible(r["close"], ref["reference"], r.get("exchange")):
            r["change_pct"] = refprices.board_pct(r["close"], ref, r.get("exchange"))
            r["change_basis"], r["reference"] = "ref", ref["reference"]
            r["reference_method"], r["reference_averaged"] = ref.get("method"), ref.get("averaged")
        else:
            r["change_basis"], r["reference"] = "close", None
    return rows


def api_market(q: dict[str, str], path: Path | None = None) -> Any:
    """The whole-market snapshot (python -m src.main market), with each ticker's place in
    the latest screen run and the user's picks added at read time so they are never stale."""
    from src.quant.market_view import load_snapshot  # noqa: PLC0415
    from src.quant.user_watchlist import tickers as my_tickers  # noqa: PLC0415

    as_of, rows = load_snapshot(path)
    if not rows:
        return {"as_of": None, "rows": [], "note": "Chưa có ảnh chụp toàn thị trường. Chạy: python -m src.main market"}
    latest = _rows("SELECT run_id, as_of_date FROM screen_runs ORDER BY created_at DESC LIMIT 1", path=path)
    run_id = latest[0]["run_id"] if latest else None
    groups = {r["ticker"]: r["grp"] for r in _rows(
        "SELECT ticker, grp FROM screen_members WHERE run_id = ?", (run_id,), path)} if run_id else {}
    leaders = {r["ticker"]: r["industry"] for r in _rows(
        "SELECT ticker, industry FROM screen_industry_leaders WHERE run_id = ?", (run_id,), path)} if run_id else {}
    mine = set(my_tickers(path))
    for r in rows:
        r["grp"], r["leader_of"], r["mine"] = groups.get(r["ticker"]), leaders.get(r["ticker"]), r["ticker"] in mine
    _board_change(rows, path)
    # The run the snapshot's RS really came from (a snapshot is rebuilt after every new run).
    rs_from = next((r.get("rs_from") for r in rows if r.get("rs_from")), None)
    return {"as_of": as_of, "rs_from": rs_from, "rows": rows,
            "note": f"Ảnh chụp để nghiên cứu, không phải danh sách ứng viên. RS theo lượt lọc {rs_from or '–'}, "
                    "xếp hạng trên mọi mã đủ dữ liệu. % so với giá tham chiếu với mã đã đọc giá tham chiếu "
                    "(mã đang theo dõi); mã khác so với giá đóng cửa phiên trước — ở UPCoM (gạch chấm) bảng giá "
                    "tính so với giá bình quân phiên trước nên có thể khác."}


def api_logos(q: dict[str, str], path: Path | None = None) -> Any:
    """Tickers with a stored logo (python -m src.main logos); the page asks /logo/<TICKER> only for these."""
    return logos.available(logos.LOGO_DIR)


def share_scale(ticker: str, path: Path | None = None) -> Callable[[str], float]:
    """Multiplier putting a session's share counts on today's scale. DNSE rescales a ticker's
    prices on a stock dividend or split but neither its volumes nor the foreign figures, so a
    sum over sessions across the ex-date mixes two kinds of share (HDB 09/10/2026, factor
    0.769): a session up to an adjustment's `through_session` is divided by its factor."""
    adj = [(r["through_session"], r["factor"]) for r in _rows(
        "SELECT through_session, factor FROM price_adjustments WHERE ticker = ?", (ticker.upper(),), path)]

    def scale(day: str) -> float:
        k = 1.0
        for through, factor in adj:
            if factor and str(day) <= str(through):
                k /= float(factor)
        return k
    return scale


def flow_row(r: Mapping[str, Any], k: float = 1.0) -> dict[str, Any]:
    """One session's foreign and domestic figures (shares on today's scale via `k`, values in VND).

    Foreign totals (buy / sell / net) cover every board, as exchanges and brokers publish them:
    matched (G1), put-through (T1, T3) and odd lot. Domestic = matched volume minus the foreign
    MATCHED side (user's definition, 2026-10-03; until 2026-10-13 it took the totals, so a
    put-through block made it wrong or blank: HDB 06/10 sold 76.6M with 13.8M matched)."""
    def sh(v: Any) -> float | None:
        return None if v is None else float(v) * k

    fb, fs, vol = sh(r.get("fb")), sh(r.get("fs")), sh(r.get("vol"))
    split = bool(r.get("split"))
    fbm, fsm = (sh(r.get("fbm")), sh(r.get("fsm"))) if split else (None, None)
    net = None if fb is None or fs is None else fb - fs
    net_m = None if fbm is None or fsm is None else fbm - fsm

    def dom(f: float | None) -> float | None:
        return None if vol is None or f is None or f > vol + 0.5 else max(vol - f, 0.0)

    return {"volume": vol, "has_flow": fb is not None or fs is not None, "adjusted": k != 1.0,
            "foreign_buy": fb, "foreign_sell": fs, "foreign_net": net,
            "foreign_net_value": None if r.get("fbv") is None or r.get("fsv") is None else r["fbv"] - r["fsv"],
            "foreign_buy_matched": fbm, "foreign_sell_matched": fsm, "foreign_net_matched": net_m,
            # put-through (and the odd lot): the totals minus the matched board
            "foreign_net_put_through": None if net is None or net_m is None else net - net_m,
            "domestic_buy": dom(fbm), "domestic_sell": dom(fsm)}


_FLOW_COLS = ("f.foreign_buy_volume AS fb, f.foreign_sell_volume AS fs, f.foreign_buy_value AS fbv, "
              "f.foreign_sell_value AS fsv, f.foreign_buy_volume_matched AS fbm, f.foreign_sell_volume_matched AS fsm, "
              "f.boards_split AS split")


def api_flow(q: dict[str, str], path: Path | None = None) -> Any:
    """Foreign and domestic buying / selling per session, newest first (src.web.server.flow_row).

    DNSE does not split domestic investors (individuals, institutions, proprietary desks) further."""
    ticker = (q.get("ticker") or "").upper()
    n = min(int(q.get("sessions", 20)), 60)
    scale = share_scale(ticker, path)
    # One row per price session: a session DNSE holds no foreign snapshot for stays in the list,
    # blank, instead of the list reaching back a month to fill N rows (ABB, tester R3-03).
    rows = _rows(f"SELECT p.trade_date, p.volume AS vol, p.close, {_FLOW_COLS} "
                 "FROM (SELECT * FROM prices_daily WHERE ticker = ? ORDER BY trade_date DESC LIMIT ?) p "
                 "LEFT JOIN flow_daily f ON f.scope = 'TICKER' AND f.scope_key = p.ticker AND f.trade_date = p.trade_date "
                 "ORDER BY p.trade_date DESC", (ticker, n), path)
    out = [{"date": r["trade_date"], "close": r["close"], **flow_row(r, scale(r["trade_date"]))} for r in rows]
    return {"ticker": ticker, "rows": out,
            "note": "Khối ngoại: DNSE. Mua / bán khớp = bảng khớp lệnh; thỏa thuận = phần còn lại của tổng ngày "
                    "(gồm lô lẻ); ngoại ròng = tổng, như số sàn công bố. Khối nội = khối lượng khớp − khối ngoại khớp. "
                    "Phiên để trống: DNSE không có số liệu khối ngoại (thường là không có giao dịch khối ngoại)."}


def mylist_write(body: dict[str, Any], path: Path | None = None) -> Any:
    """The dashboard's one write: add / remove a ticker in the user's picks. Nothing else.

    With the web copy set up (src/collectors/cloudflare_kv.py) the master list is on Cloudflare:
    it is read first (a ticker added on the web is not lost here) and written after; on a KV
    error the local list is put back and nothing changes. A ticker added is brought up to date
    in the background (`mylist-refresh`: its state, technicals, foreign flow, news)."""
    from src.collectors import cloudflare_kv as kv  # noqa: PLC0415
    from src.quant.user_watchlist import WatchlistError, add, as_items, entries, mirror, remove  # noqa: PLC0415

    action = body.get("action")
    if action not in ("add", "remove"):
        raise ValueError("action phải là add hoặc remove")
    ticker = str(body.get("ticker", "")).strip().upper()
    site = load_config().thresholds.raw["site"]
    account = site["local_account"]                   # this machine's list is that account's on the web
    web = _web_list_enabled(path)
    try:
        if web:
            mirror(kv.read_or_seed(account, as_items(path))["items"], path)
        before = {e["ticker"]: e for e in entries(path)}
        if action == "add":
            result: dict[str, Any] = {"ok": True, "added": add(ticker, body.get("note"), path,
                                                                max_items=int(site["mylist_max_per_account"]))}
        else:
            result = {"ok": True, "removed": remove(ticker, path)}
        if web:
            try:
                kv.write_list(account, as_items(path))
            except kv.KvError:
                mirror(list(before.values()), path)  # put the local list back as it was
                raise
    except WatchlistError as exc:
        raise ValueError(str(exc)) from exc
    except kv.KvError as exc:
        raise ValueError(f"{exc} — danh sách không đổi") from exc
    result["web"] = web
    if action == "add" and path is None:          # the real database only: never from a test
        result["refreshing"] = _spawn_refresh(ticker)
    return result


def _web_list_enabled(path: Path | None) -> bool:
    """The real database with the web list set up in .env; a test database never reaches Cloudflare."""
    from src.collectors import cloudflare_kv as kv  # noqa: PLC0415
    from src.env import load_dotenv  # noqa: PLC0415

    if path is not None:
        return False
    load_dotenv()
    return kv.configured()


def _spawn_refresh(ticker: str) -> bool:
    """`python -m src.main mylist-refresh <ticker>` in the background, logged to data/logs."""
    import subprocess  # noqa: PLC0415

    log_dir = PROJECT_ROOT / "data" / "logs"
    log_dir.mkdir(parents=True, exist_ok=True)
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(subprocess, "DETACHED_PROCESS", 0)
    try:
        with open(log_dir / f"mylist_{datetime.now(VN_TZ).date().isoformat()}.log", "ab") as out:
            subprocess.Popen([sys.executable, "-m", "src.main", "mylist-refresh", ticker], cwd=PROJECT_ROOT,
                             stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, creationflags=flags)
    except OSError:
        log.exception("mylist-refresh %s", ticker)
        return False
    return True


STOP_IN_TEXT = re.compile(r"stop tham chiếu ([\d.,]+)")


def _fill_thresholds(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Event texts stored before 2026-10-12 read "Giá cao hơn đáy 52 tuần ≥ X%" (tester R3-07)."""
    from src.quant.screener import criteria_labels  # noqa: PLC0415

    tt6 = criteria_labels(screen_settings())["TT6"]
    for r in rows:
        for k in ("what_changed", "why_it_matters"):
            if r.get(k) and "≥ X%" in r[k]:
                r[k] = r[k].replace("Giá cao hơn đáy 52 tuần ≥ X%", tt6)
    return rows


def api_alerts(q: dict[str, str], path: Path | None = None) -> Any:
    """Recent events. A stop breach whose stop is not the ticker's stop shown today (or the
    ticker has none: no forming base since 2026-10-11) is `stale_stop`: the stop of an
    earlier base, labelled so on the page."""
    limit = min(int(q.get("limit", 200)), 1000)
    rows = _fill_thresholds(_rows("SELECT a.ticker, a.alert_type, a.what_changed, a.why_it_matters, a.created_at, "
                                  "t.exchange FROM alerts a LEFT JOIN tickers t ON t.ticker = a.ticker "
                                  "ORDER BY a.what_changed DESC LIMIT ?", (limit,), path))
    tickers = sorted({r["ticker"] for r in rows if r["alert_type"] == "STOP_BREACHED"})
    stops: dict[str, Any] = {}
    if tickers:
        marks = ",".join("?" * len(tickers))
        for r in _rows(f"SELECT s.ticker, s.state_json FROM monitor_state s JOIN (SELECT ticker, MAX(trade_date) d "
                       f"FROM monitor_state WHERE ticker IN ({marks}) GROUP BY ticker) x "
                       f"ON x.ticker = s.ticker AND x.d = s.trade_date", tuple(tickers), path):
            stops[r["ticker"]] = (json.loads(r["state_json"] or "{}") or {}).get("stop")
    for r in rows:
        if r["alert_type"] != "STOP_BREACHED":
            continue
        r["current_stop"] = stops.get(r["ticker"])
        m = STOP_IN_TEXT.search(r["what_changed"] or "")
        was = int(re.sub(r"[.,]", "", m.group(1))) if m else None
        r["stale_stop"] = was is not None and (r["current_stop"] is None or was != round(r["current_stop"]))
    return rows


def api_checks(q: dict[str, str], path: Path | None = None) -> Any:
    """The latest invariant report (python -m src.main check), summarised for the page. Read-only."""
    from src.web.checks import latest_report, summary  # noqa: PLC0415

    return summary(latest_report())


def api_intraday(q: dict[str, str], path: Path | None = None) -> Any:
    """The session's intraday alerts (python -m src.main watch) and the watcher's status. Read-only.
    Without a date: today, or the latest session that has alerts."""
    from src.collectors.intraday_watch import intraday_settings, read_status  # noqa: PLC0415

    day = q.get("date") or datetime.now(VN_TZ).date().isoformat()
    sql = ("SELECT a.*, t.name FROM intraday_alerts a LEFT JOIN tickers t ON t.ticker = a.ticker "
           "WHERE a.session_date = ? ORDER BY a.traded_at DESC")
    rows = _rows(sql, (day,), path)
    if not rows and not q.get("date"):
        last = _rows("SELECT MAX(session_date) AS d FROM intraday_alerts", path=path)
        if last and last[0]["d"]:
            day = last[0]["d"]
            rows = _rows(sql, (day,), path)
    status = read_status(date.fromisoformat(day))
    if status:
        written = status.get("written_at")
        age = (datetime.now(VN_TZ) - datetime.fromisoformat(written)).total_seconds() if written else None
        status["running"] = not status.get("ended_at")
        status["stale"] = bool(status["running"] and (age is None or age > float(intraday_settings()["status_stale_seconds"])))
        status["skipped_n"] = len(status.get("skipped") or {})
        status["watched_n"] = len(status.get("watched") or [])
        status.pop("last", None)                        # last prices: not needed by the page
    return {"session_date": day, "alerts": rows, "status": status}


# The article a news card opens: CafeF's first (the user reads news there), else the earliest.
_NEWS_ARTICLE = ("FROM news_articles a WHERE a.event_id = e.event_id "
                 "ORDER BY a.source_id NOT LIKE 'cafef%', a.published_at LIMIT 1")
NEWS_ROWS_CAP = 10000  # a ticker-event row each (~900 a month, 2026-10-12): every row in the window reaches the search


def api_news(q: dict[str, str], path: Path | None = None) -> Any:
    """News of the monitored tickers, newest first, over `days` (default news.page_days)."""
    days = int(q.get("days") or load_config().thresholds.raw["news"].get("page_days", 30))
    since = (datetime.now(VN_TZ) - timedelta(days=days)).isoformat()
    return _rows("SELECT t.ticker, tk.name, t.role, e.event_id, e.event_type, e.headline, e.occurred_at, e.article_count, "
                 f"e.best_source_level, e.assertion_type, e.what_happened, e.direction, "
                 f"(SELECT a.url {_NEWS_ARTICLE}) AS url, (SELECT a.source_id {_NEWS_ARTICLE}) AS url_source "
                 "FROM news_events e JOIN event_tickers t ON t.event_id = e.event_id "
                 "LEFT JOIN tickers tk ON tk.ticker = t.ticker "
                 f"WHERE e.occurred_at >= ? ORDER BY e.occurred_at DESC LIMIT {NEWS_ROWS_CAP}", (since,), path)


ROUTES: dict[str, Callable[..., Any]] = {
    "/api/runs": api_runs,
    "/api/screen": api_screen,
    "/api/ticker": api_ticker,
    "/api/industries": api_industries,
    "/api/leaders": api_leaders,
    "/api/mylist": api_mylist,
    "/api/position": api_position,
    "/api/stocks": api_stocks,
    "/api/flow": api_flow,
    "/api/logos": api_logos,
    "/api/market": api_market,
    "/api/index": api_index,
    "/api/bars": api_bars,
    "/api/alerts": api_alerts,
    "/api/checks": api_checks,
    "/api/intraday": api_intraday,
    "/api/news": api_news,
}


WRITE_ROUTES: dict[str, Callable[..., Any]] = {"/api/mylist": mylist_write}
MAX_BODY_BYTES = 2048


def app_version(path: Path | None = None) -> str:
    """What an open page was built against: the page's own files and the latest screen run.
    A tab left open across an update kept its old script ("APPROACHING" shown raw, HII still in
    group B from the earlier run); the page compares this with the value it first saw."""
    stamp = max((f.stat().st_mtime_ns for f in STATIC.iterdir() if f.is_file()), default=0)
    try:
        with connect(path) as conn:
            row = conn.execute("SELECT run_id FROM screen_runs ORDER BY created_at DESC LIMIT 1").fetchone()
    except Exception:  # noqa: BLE001 - a version header must never break a response
        row = None
    return f"{stamp:x}-{row[0] if row else '-'}"


class Handler(BaseHTTPRequestHandler):
    db_path: Path | None = None

    def log_message(self, fmt: str, *args: Any) -> None:  # quiet console
        log.debug(fmt, *args)

    def _send(self, status: int, body: bytes, content_type: str, cache: str = "no-store",
              version: str | None = None) -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", cache)
        if version:
            self.send_header("X-App-Version", version)
        self.end_headers()
        self.wfile.write(body)

    def _same_origin(self) -> bool:
        """Only this dashboard may write: another site open in the browser must not.

        The Host check stops DNS-rebinding; the Origin check stops a page on
        another origin; requiring a JSON body forces a CORS preflight, which this
        server never answers, so a cross-site form cannot reach here either.
        """
        port = self.server.server_address[1]
        allowed = {f"127.0.0.1:{port}", f"localhost:{port}"}
        if self.headers.get("Host", "") not in allowed:
            return False
        origin = self.headers.get("Origin")
        if origin is not None and origin not in {f"http://{h}" for h in allowed}:
            return False
        return (self.headers.get("Content-Type") or "").split(";")[0].strip() == "application/json"

    def do_POST(self) -> None:  # noqa: N802
        route = WRITE_ROUTES.get(urlsplit(self.path).path)
        if route is None:
            self._send(404, b"not found", "text/plain")
            return
        if not self._same_origin():
            self._send(403, json.dumps({"error": "forbidden"}).encode(), "application/json")
            return
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_BODY_BYTES:
            self._send(400, json.dumps({"error": "body"}).encode(), "application/json")
            return
        try:
            body = json.loads(self.rfile.read(length).decode("utf-8"))
            payload = route(body if isinstance(body, dict) else {}, self.db_path)
            self._send(200, json.dumps(payload, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")
        except ValueError as exc:
            self._send(400, json.dumps({"error": str(exc)}, ensure_ascii=False).encode("utf-8"),
                       "application/json; charset=utf-8")
        except Exception as exc:  # noqa: BLE001
            log.exception("write error")
            self._send(500, json.dumps({"error": str(exc)}).encode(), "application/json")

    def do_GET(self) -> None:  # noqa: N802
        parts = urlsplit(self.path)
        route = ROUTES.get(parts.path)
        if route is not None:
            query = {k: v[0] for k, v in parse_qs(parts.query).items()}
            try:
                payload = _clean(route(query, self.db_path))
                body = json.dumps(payload, ensure_ascii=False, default=str, allow_nan=False).encode("utf-8")
                self._send(200, body, "application/json; charset=utf-8", version=app_version(self.db_path))
            except KeyError as exc:
                self._send(404, json.dumps({"error": str(exc)}).encode(), "application/json")
            except Exception as exc:  # noqa: BLE001 - a dashboard error must not kill the server
                log.exception("api error")
                self._send(500, json.dumps({"error": str(exc)}).encode(), "application/json")
            return
        if parts.path.startswith("/logo/"):
            # Read-only: a stored company logo, looked up by a validated 3-character code.
            target = logos.logo_file(parts.path[len("/logo/"):].upper(), logos.LOGO_DIR)
            if target is None:
                self._send(404, b"not found", "text/plain")
            else:
                self._send(200, target.read_bytes(), logos.CONTENT_TYPES[target.suffix[1:]], "max-age=86400")
            return
        if parts.path == "/site.json":
            # The page asks whether it is the static web copy (src/web/export.py writes one there); this is not.
            self._send(200, b'{"static": false}', "application/json; charset=utf-8")
            return
        name = "index.html" if parts.path in ("/", "") else parts.path.lstrip("/")
        target = (STATIC / name).resolve()
        if STATIC not in target.parents or not target.is_file():
            self._send(404, b"not found", "text/plain")
            return
        ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        if ctype.startswith("text/") or ctype.endswith("javascript"):
            ctype += "; charset=utf-8"
        self._send(200, target.read_bytes(), ctype)


class DashboardServer(ThreadingHTTPServer):
    # On Windows SO_REUSEADDR lets a second `start` bind the same port without an
    # error while the older server keeps answering with old code (seen 2026-10-04).
    # Without it the second bind fails, and `start` says the port is taken.
    allow_reuse_address = sys.platform != "win32"


def serve(port: int = 8765, open_browser: bool = True, db_path: Path | None = None) -> None:
    Handler.db_path = db_path
    try:
        server = DashboardServer(("127.0.0.1", port), Handler)
    except OSError as exc:
        raise SystemExit(f"Cổng {port} đang được dùng — dashboard có thể đã chạy rồi: mở http://127.0.0.1:{port}/ "
                         f"hoặc tắt cửa sổ đang chạy start, hay dùng --port {port + 1}. ({exc})") from exc
    url = f"http://127.0.0.1:{port}/"
    print(f"Dashboard: {url}  (Ctrl+C để dừng)")
    if open_browser:
        import webbrowser

        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
