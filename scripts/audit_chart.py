"""Audit of the dashboard chart: indicator lines and drawn overlays.

Three independent rounds, each written to data/audit/chart_round<N>.json:

1. Recompute. MA20/50/150/200, RSI 14 (Wilder) and MACD 12/26/9 are recomputed
   from the same bars with plain Python loops written from the published
   definitions (skill reference 04) -- not with src/quant/indicators.py -- and
   compared point by point with what /api/ticker and /api/index send the
   browser. Also recomputed from the full stored history, to show that the
   520-session window the chart loads does not move any visible value.
2. Second source. Bars from SSI FastConnect Data when its keys are in .env
   (official), otherwise VNDirect's public chart feed, compared session by
   session with the stored DNSE bars (close and volume), and the indicators
   recomputed on them, so a wrong input series would show even where the
   arithmetic is right.
3. Overlays. Every drawn line is checked against the rule that defines it:
   zones (width, side, distance, touch count), trendlines (anchors are swing
   lows / highs, no close through the line between anchors, break date,
   today's value, projection), the channel (parallel rails holding every bar,
   position in the band), the base (top, low, middle, depth), pivot / stop.

    python scripts/audit_chart.py --round 1 [--tickers PHP ABB ...]
    python scripts/audit_chart.py --round 3 --tf W      # weekly / monthly chart (2026-10-06)

`--tf W|M` (round 3 only) audits the weekly or monthly chart: every candle is
re-grouped by hand from the stored daily bars (ISO week / calendar month) and
compared, then the trendlines and channel drawn on those candles are checked
against the same rules with the timeframe's own settings
(`chart_levels.trendline_by_tf`), including the projected end date.

Research tool: it reads the database and public pages, and writes only the
JSON report.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from src.config import load_config  # noqa: E402
from src.database import connect  # noqa: E402
from src.web import server  # noqa: E402

DEFAULT = ["VNINDEX", "PHP", "ABB", "GMD", "PVT", "SSI", "VIC", "HID", "TRC", "PVS", "SHS", "DRI", "HHP", "MSR"]
OUT = ROOT / "data" / "audit"
VISIBLE = 150            # sessions the chart shows on open


# ---------------------------------------------------------------- definitions (plain Python)
def sma(xs, n):
    return [sum(xs[i - n + 1:i + 1]) / n if i >= n - 1 else None for i in range(len(xs))]


def ema(xs, n, seed="first"):
    """alpha = 2/(n+1). seed "first": EMA_0 = x_0 (Pine reference code, pandas adjust=False);
    seed "sma": EMA_{n-1} = SMA(n) (the other reading of TradingView, skill D15)."""
    a = 2.0 / (n + 1)
    out = [None] * len(xs)
    start = [i for i, x in enumerate(xs) if x is not None]
    if not start:
        return out
    s = start[0]
    if seed == "first":
        e = xs[s]
        out[s] = e
        rng = range(s + 1, len(xs))
    else:
        if len(xs) - s < n:
            return out
        e = sum(xs[s:s + n]) / n
        out[s + n - 1] = e
        rng = range(s + n, len(xs))
    for i in rng:
        e = a * xs[i] + (1 - a) * e
        out[i] = e
    return out


def rsi(xs, n=14):
    """Wilder: average gain / loss = SMA of the first n changes, then (prev*(n-1) + x)/n."""
    out = [None] * len(xs)
    if len(xs) <= n:
        return out
    ch = [xs[i] - xs[i - 1] for i in range(1, len(xs))]
    g = [max(c, 0.0) for c in ch]
    lo = [max(-c, 0.0) for c in ch]
    ag, al = sum(g[:n]) / n, sum(lo[:n]) / n

    def value(ag, al):
        if al == 0:
            return 100.0 if ag > 0 else None
        return 100.0 - 100.0 / (1.0 + ag / al)

    out[n] = value(ag, al)
    for k in range(n, len(ch)):
        ag = (ag * (n - 1) + g[k]) / n
        al = (al * (n - 1) + lo[k]) / n
        out[k + 1] = value(ag, al)
    return out


def macd(xs, seed="first"):
    f, s = ema(xs, 12, seed), ema(xs, 26, seed)
    line = [a - b if a is not None and b is not None else None for a, b in zip(f, s)]
    sig = ema(line, 9, seed)
    hist = [a - b if a is not None and b is not None else None for a, b in zip(line, sig)]
    return line, sig, hist


def compare(api_points, mine, times, label, tol, visible_from):
    """api_points: [{time, value}]; mine: list aligned with times. Returns a dict of findings."""
    at = {p["time"]: p["value"] for p in api_points}
    first_api = api_points[0]["time"] if api_points else None
    first_mine = next((times[i] for i, v in enumerate(mine) if v is not None), None)
    diffs, diffs_visible, missing = [], [], 0
    for i, t in enumerate(times):
        if mine[i] is None:
            continue
        if t not in at:
            missing += 1
            continue
        d = abs(at[t] - mine[i])
        diffs.append(d)
        if i >= visible_from:
            diffs_visible.append(d)
    worst = max(diffs) if diffs else None
    return {"series": label, "points_api": len(api_points), "points_mine": sum(v is not None for v in mine),
            "first_api": first_api, "first_mine": first_mine, "missing_in_api": missing,
            "max_abs_diff": worst, "max_abs_diff_visible": max(diffs_visible) if diffs_visible else None,
            "ok": worst is not None and worst <= tol and first_api == first_mine and missing == 0}


def api_payload(t):
    return server.api_index({"symbol": t}) if t in server.INDEX_NAMES else server.api_ticker({"ticker": t})


def full_history(t):
    table, col = ("index_daily", "symbol") if t in server.INDEX_NAMES else ("prices_daily", "ticker")
    with connect() as conn:
        return [dict(r) for r in conn.execute(
            f"SELECT trade_date, open, high, low, close, volume FROM {table} WHERE {col} = ? ORDER BY trade_date", (t,))]


# ---------------------------------------------------------------- round 1
def round1(tickers):
    out = {}
    for t in tickers:
        d = api_payload(t)
        bars = d["bars"]
        times = [b["trade_date"] for b in bars]
        close = [float(b["close"]) for b in bars]
        vis = max(0, len(bars) - VISIBLE)
        price_tol = 0.006 if t in server.INDEX_NAMES else 0.006   # API rounds to 2 decimals
        rows = []
        for n in (20, 50, 150, 200):
            rows.append(compare(d["ma"].get(f"MA{n}", []), sma(close, n), times, f"MA{n}", price_tol, vis))
        rows.append(compare(d["osc"]["RSI"], rsi(close), times, "RSI14", 0.006, vis))
        line, sig, hist = macd(close)
        for key, mine in (("MACD", line), ("SIGNAL", sig), ("HIST", hist)):
            rows.append(compare(d["osc"][key], mine, times, key, 0.0006, vis))
        # The chart loads 520 sessions; the same lines from the full history, over the visible part.
        full = full_history(t)
        fc = [float(b["close"]) for b in full]
        ft = [b["trade_date"] for b in full]
        k0 = ft.index(times[0])
        window_effect = {}
        for label, a, b in (("RSI14", rsi(fc), rsi(close)), ("MACD", macd(fc)[0], line), ("SIGNAL", macd(fc)[1], sig)):
            dv = [abs(a[k0 + i] - b[i]) for i in range(vis, len(close)) if a[k0 + i] is not None and b[i] is not None]
            window_effect[label] = max(dv) if dv else None
        # EMA seeding (skill D15): first-value vs SMA seed, over the visible part.
        alt_line = macd(close, seed="sma")[0]
        seed_effect = max(abs(a - b) for a, b in zip(line[vis:], alt_line[vis:]) if a is not None and b is not None)
        last = {"close": close[-1], **{f"MA{n}": sma(close, n)[-1] for n in (20, 50, 150, 200)},
                "RSI14": rsi(close)[-1], "MACD": line[-1], "SIGNAL": sig[-1], "HIST": hist[-1]}
        out[t] = {"bars": len(bars), "last_date": times[-1], "checks": rows, "window_effect_visible": window_effect,
                  "macd_seed_effect_visible": seed_effect, "last": last,
                  "ok": all(r["ok"] for r in rows)}
    return out


# ---------------------------------------------------------------- round 2
def vndirect(symbol, days=900):
    import requests  # noqa: PLC0415

    to = int(time.time())
    r = requests.get("https://dchart-api.vndirect.com.vn/dchart/history",
                     params={"resolution": "D", "symbol": symbol, "from": to - days * 86400, "to": to},
                     headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}, timeout=25)
    j = r.json()
    if j.get("s") != "ok":
        return []
    return [{"trade_date": datetime.fromtimestamp(ts, timezone.utc).date().isoformat(),
             "open": o, "high": h, "low": lo, "close": c, "volume": v}
            for ts, o, h, lo, c, v in zip(j["t"], j["o"], j["h"], j["l"], j["c"], j["v"])]


def second_source():
    """SSI FastConnect Data (official) when its keys are in .env, else VNDirect's public chart feed.
    Returns (name, fetch) where fetch(symbol, is_index) -> (bars, scale to VND / points)."""
    try:
        from src.collectors.providers.ssi_fcdata_provider import ssi_provider  # noqa: PLC0415
        from src.env import load_dotenv  # noqa: PLC0415

        load_dotenv()
        provider = ssi_provider()
    except Exception:  # noqa: BLE001 -- no SSI key: fall back to VNDirect
        return "VNDirect", lambda t, index: (vndirect(t), 1.0 if index else 1000.0)

    def fetch(t, index):
        end = date.today()
        bars = provider.daily_ohlc(t, end - timedelta(days=900), end, index=index)
        return [{"trade_date": b.trade_date.isoformat(), "open": b.open, "high": b.high, "low": b.low,
                 "close": b.close, "volume": b.volume} for b in bars], 1.0
    return "SSI", fetch


def round2(tickers):
    out = {}
    name, fetch = second_source()
    for t in tickers:
        index = t in server.INDEX_NAMES
        ours = {b["trade_date"]: b for b in full_history(t)}
        theirs, scale = fetch(t, index)        # VNDirect quotes stocks in thousands of VND; SSI in VND
        time.sleep(1.5)
        common = [b for b in theirs if b["trade_date"] in ours][-260:]
        if not common:
            out[t] = {"source": name, "error": f"{name} trả về rỗng"}
            continue
        rows = []
        for b in common:
            o = ours[b["trade_date"]]
            rows.append({"date": b["trade_date"], "ours": o["close"], "theirs": round(b["close"] * scale, 2),
                         "ratio": o["close"] / (b["close"] * scale) if b["close"] else None,
                         "vol_ours": o["volume"], "vol_theirs": b["volume"]})
        exact = [r for r in rows if r["ratio"] is not None and abs(r["ratio"] - 1) < 0.0006]
        off = [r for r in rows if r not in exact]
        # A constant ratio over a run of sessions is an adjustment difference, not noise.
        segments = []
        for r in off:
            q = round(r["ratio"], 4) if r["ratio"] else None
            if segments and segments[-1]["ratio"] == q:
                segments[-1]["to"] = r["date"]
                segments[-1]["n"] += 1
            else:
                segments.append({"ratio": q, "from": r["date"], "to": r["date"], "n": 1})
        vol_rel = [abs(r["vol_ours"] / r["vol_theirs"] - 1) for r in rows if r["vol_theirs"]]
        # Indicators from their closes vs ours, at the last common session.
        tc = [b["close"] * scale for b in theirs]
        tdates = [b["trade_date"] for b in theirs]
        last_common = common[-1]["trade_date"]
        k = tdates.index(last_common)
        ours_list = full_history(t)
        oc = [float(b["close"]) for b in ours_list]
        ko = [b["trade_date"] for b in ours_list].index(last_common)
        ind_cmp = {}
        for label, f in (("MA20", lambda x: sma(x, 20)), ("MA50", lambda x: sma(x, 50)), ("MA150", lambda x: sma(x, 150)),
                         ("MA200", lambda x: sma(x, 200)), ("RSI14", rsi), ("MACD", lambda x: macd(x)[0])):
            a, b = f(oc)[ko], f(tc)[k]
            ind_cmp[label] = {"ours": a, "other": b,
                              "diff_pct": (a / b - 1) * 100 if a is not None and b not in (None, 0) and label.startswith("MA") else None,
                              "diff": a - b if a is not None and b is not None else None}
        # Distribution days on each source's volume (index only): does the volume source change the count?
        dd = None
        if index:
            m = load_config().thresholds["sepa_screen"]["market"]
            window, drop = int(m["distribution_window"]), float(m["distribution_min_drop_pct"])

            def dist(bars):
                return {bars[i]["trade_date"] for i in range(1, len(bars))
                        if (bars[i]["close"] / bars[i - 1]["close"] - 1) * 100 <= -drop and bars[i]["volume"] > bars[i - 1]["volume"]}
            ob = [ours[r["date"]] for r in rows]
            tb = [b for b in theirs if b["trade_date"] in ours]
            last_dates = [r["date"] for r in rows][-window:]
            a, b = dist(ob) & set(last_dates), dist(tb) & set(last_dates)
            dd = {"window": window, "dnse": sorted(a), "other": sorted(b),
                  "only_dnse": sorted(a - b), "only_other": sorted(b - a)}
        out[t] = {"source": name, "sessions_compared": len(rows), "close_exact": len(exact), "close_differs": len(off),
                  "differ_segments": segments[:8], "volume_median_rel_diff_pct": round(sorted(vol_rel)[len(vol_rel) // 2] * 100, 2) if vol_rel else None,
                  "volume_max_rel_diff_pct": round(max(vol_rel) * 100, 1) if vol_rel else None,
                  "last_common": last_common, "indicators_at_last": ind_cmp, "distribution_days": dd,
                  "last_rows": rows[-3:]}
    return out


# ---------------------------------------------------------------- round 3
def regroup(daily, tf):
    """Weekly (ISO week) or monthly candles from daily bars, plain Python."""
    groups = {}
    for b in daily:
        y, w, _ = date.fromisoformat(b["trade_date"]).isocalendar()
        groups.setdefault((y, w) if tf == "W" else b["trade_date"][:7], []).append(b)
    out = []
    for k in sorted(groups):
        g = groups[k]
        hs = [x["high"] for x in g if x["high"] is not None]
        ls = [x["low"] for x in g if x["low"] is not None]
        out.append({"trade_date": g[0]["trade_date"], "open": g[0]["open"], "high": max(hs) if hs else None,
                    "low": min(ls) if ls else None, "close": g[-1]["close"], "volume": sum(x["volume"] or 0 for x in g)})
    return out


def projected_date(last, n, tf):
    d = date.fromisoformat(last)
    if tf == "W":
        return (d - timedelta(days=d.weekday()) + timedelta(weeks=n)).isoformat()
    if tf == "M":
        y, m = d.year + (d.month - 1 + n) // 12, (d.month - 1 + n) % 12 + 1
        d = date(y, m, 1)
    else:
        for _ in range(n):
            d += timedelta(days=1)
            while d.weekday() >= 5:
                d += timedelta(days=1)
        return d.isoformat()
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d.isoformat()


def round3(tickers, tf="D"):
    th = load_config().thresholds
    tl = th["chart_levels"]["trendline"] if tf == "D" else th["chart_levels"]["trendline_by_tf"][tf]
    zc = th["patterns"]["zones"] if "zones" in th.get("patterns", {}) else None
    unit = {"D": "phiên", "W": "tuần", "M": "tháng"}[tf]
    out = {}
    for t in tickers:
        if tf == "D":
            d = api_payload(t)
        else:
            d = server.api_bars({"index" if t in server.INDEX_NAMES else "ticker": t, "tf": tf})
        bars = d["bars"]
        close = [float(b["close"]) for b in bars]
        hi = [float(b["high"]) for b in bars]
        lo = [float(b["low"]) for b in bars]
        dates = [b["trade_date"] for b in bars]
        last = close[-1]
        f = []                                  # findings: (check, ok, detail)

        def check(name, ok, detail=""):
            f.append({"check": name, "ok": bool(ok), "detail": detail})

        if tf != "D":
            mine = regroup(full_history(t), tf)
            bad = [m["trade_date"] for m, b in zip(mine, bars)
                   if m["trade_date"] != b["trade_date"]
                   or any(m[k] is not None and abs(m[k] - b[k]) > 1e-6 for k in ("open", "high", "low", "close", "volume"))]
            check(f"Nến {unit} = gộp tay từ nến ngày (mở, cao, thấp, đóng, khối lượng)",
                  len(mine) == len(bars) and not bad, f"{len(mine)} vs {len(bars)} nến; lệch: {', '.join(bad[:5])}")

        lv = d.get("levels") or {}
        for z in lv.get("zones") or []:
            side = z["side"]
            check(f"Vùng {side}: thấp ≤ giữa ≤ cao", z["zone_low"] < z["center"] < z["zone_high"])
            half = (z["zone_high"] - z["zone_low"]) / 2 / z["center"] * 100
            check(f"Vùng {side}: rộng ±0,75% quanh tâm", abs(half - 0.75) < 0.01, f"±{half:.3f}%")
            check(f"Vùng {side}: đúng phía so với giá", (z["center"] > last) == (side == "RESISTANCE"),
                  f"tâm {z['center']} / giá {last}")
            dist = (z["center"] / last - 1) * 100
            check(f"Vùng {side}: % cách giá", abs(dist - z["distance_from_price"]) < 0.01, f"{dist:.2f} vs {z['distance_from_price']}")
            tol = z["center"] * (zc["touch_tolerance_pct"] if zc else 0) / 100
            n_cross = sum(1 for a, b in zip(lo, hi) if a <= z["zone_high"] + tol and b >= z["zone_low"] - tol)
            check(f"Vùng {side}: số 'chạm' = số phiên giá đi qua vùng", n_cross == z["touch_count"],
                  f"đếm lại {n_cross}, API {z['touch_count']}")
            # how many swing reversals actually sit in the zone (what a reader expects "touches" to mean)
            s = int(tl["pivot_strength"])
            rev = 0
            for i in range(s, len(bars) - s):
                win_lo, win_hi = lo[i - s:i + s + 1], hi[i - s:i + s + 1]
                is_low = lo[i] == min(win_lo) and win_lo.count(lo[i]) == 1
                is_high = hi[i] == max(win_hi) and win_hi.count(hi[i]) == 1
                if (is_low and z["zone_low"] <= lo[i] <= z["zone_high"]) or (is_high and z["zone_low"] <= hi[i] <= z["zone_high"]):
                    rev += 1
            z["_swing_reversals_in_zone"] = rev
        tail = int(tl["lookback_sessions"])
        f0 = max(0, len(bars) - tail)
        tol = float(tl["tolerance_pct"]) / 100
        lines = lv.get("trendlines") or {}
        for kind, line in lines.items():
            (d1, p1), (d2, p2) = line["anchors"]
            i, j = dates.index(d1), dates.index(d2)
            src = lo if kind == "SUPPORT" else hi
            check(f"Trendline {kind}: điểm neo = {'đáy' if kind == 'SUPPORT' else 'đỉnh'} của nến",
                  abs(src[i] - p1) < 0.006 and abs(src[j] - p2) < 0.006, f"{d1} {p1} / {d2} {p2}")
            s = int(tl["pivot_strength"])
            def is_swing(k):
                w = src[max(f0, k - s):k + s + 1]
                return (src[k] == (min(w) if kind == "SUPPORT" else max(w))) and w.count(src[k]) == 1 and k - s >= f0 and k + s < len(bars)
            check(f"Trendline {kind}: hai điểm neo là đỉnh/đáy swing (±{s} nến)", is_swing(i) and is_swing(j))
            check(f"Trendline {kind}: cách nhau ≥ {tl['min_span_sessions']} {unit}", j - i >= int(tl["min_span_sessions"]), f"{j - i} {unit}")
            slope = (p2 - p1) / (j - i)
            at = lambda x: p1 + slope * (x - i)  # noqa: E731
            through = [dates[k] for k in range(i, j + 1)
                       if (close[k] < at(k) * (1 - tol) if kind == "SUPPORT" else close[k] > at(k) * (1 + tol))]
            check(f"Trendline {kind}: không giá đóng cửa nào xuyên qua giữa 2 điểm neo", not through, ", ".join(through[:5]))
            brk = next((k for k in range(j + 1, len(bars))
                        if (close[k] < at(k) * (1 - tol) if kind == "SUPPORT" else close[k] > at(k) * (1 + tol))), None)
            check(f"Trendline {kind}: ngày phá đúng", (dates[brk] if brk is not None else None) == line["broken_on"],
                  f"tính lại {dates[brk] if brk is not None else '—'}, API {line['broken_on']}")
            if brk is None:
                check(f"Trendline {kind}: giá trị hôm nay", abs(at(len(bars) - 1) - line["value_today"]) < 0.01,
                      f"{at(len(bars) - 1):.2f} vs {line['value_today']}")
                end_x = len(bars) - 1 + int(tl["project_sessions"])
                check(f"Trendline {kind}: tia kéo dài {tl['project_sessions']} {unit}", abs(at(end_x) - line["end"][1]) < 0.01,
                      f"{at(end_x):.2f} vs {line['end'][1]} ngày {line['end'][0]}")
                want = projected_date(dates[-1], int(tl["project_sessions"]), tf)
                check(f"Trendline {kind}: ngày cuối tia", line["end"][0] == want, f"tính lại {want}, API {line['end'][0]}")
            else:
                check(f"Trendline {kind}: dừng ở ngày phá", line["end"][0] == dates[brk] and abs(at(brk) - line["end"][1]) < 0.01)
        ch = lv.get("channel")
        if ch:
            base_line = lines[ch["base"]]
            (d1, p1), (d2, p2) = base_line["anchors"]
            i = dates.index(d1)
            slope = (p2 - p1) / (dates.index(d2) - i)
            other = [ln for k, ln in lines.items() if k != ch["base"]]
            check("Kênh: dựng trên trendline có điểm neo gần nhất",
                  all(base_line["anchors"][1][0] >= o["anchors"][1][0] for o in other))
            (u0d, u0), (u1d, u1) = ch["upper"]
            (l0d, l0), (l1d, l1) = ch["lower"]
            end_x = (dates.index(u1d) if u1d in dates else len(bars) - 1 + int(tl["project_sessions"]))
            su, sl = (u1 - u0) / (end_x - i), (l1 - l0) / (end_x - i)
            check("Kênh: hai cạnh song song (cùng độ dốc với trendline)", abs(su - sl) < 0.01 and abs(su - slope) < 0.01,
                  f"{su:.4f} / {sl:.4f} / {slope:.4f}")
            last_bar = dates.index(ch["broken_on"]) if ch["broken_on"] else len(bars) - 1
            up = lambda x: u0 + su * (x - i)  # noqa: E731
            dn = lambda x: l0 + sl * (x - i)  # noqa: E731
            # The opposite rail holds every bar by construction; on the trendline's own side the rule
            # is the trendline's: no close more than tolerance_pct through it (wicks may poke).
            sup = ch["base"] == "SUPPORT"
            opp = [dates[k] for k in range(i, last_bar + 1) if (hi[k] > up(k) + 0.02 if sup else lo[k] < dn(k) - 0.02)]
            check(f"Kênh: cạnh song song ({'trên' if sup else 'dưới'}) chứa mọi nến", not opp, ", ".join(opp[:5]))
            beyond = (lambda k: close[k] < dn(k) * (1 - tol) - 0.02) if sup else (lambda k: close[k] > up(k) * (1 + tol) + 0.02)
            held_to = last_bar - 1 if ch["broken_on"] else last_bar          # the break session closes beyond by definition
            closes = [dates[k] for k in range(i, held_to + 1) if beyond(k)]
            check(f"Kênh: không giá đóng cửa nào vượt cạnh trendline quá {tl['tolerance_pct']}% trước ngày phá", not closes, ", ".join(closes[:5]))
            if ch["broken_on"]:
                check("Kênh: phiên phá đóng cửa ngoài kênh", beyond(last_bar), ch["broken_on"])
            wicks = [dates[k] for k in range(i, last_bar + 1) if (lo[k] < dn(k) - 0.02 if sup else hi[k] > up(k) + 0.02)]
            f.append({"check": "Kênh: râu nến chọc qua cạnh trendline (được phép)", "ok": True,
                      "detail": f"{len(wicks)} phiên" + (f": {', '.join(wicks[:5])}" if wicks else "")})
            if ch["position_pct"] is not None:
                pos = (last - dn(len(bars) - 1)) / (up(len(bars) - 1) - dn(len(bars) - 1)) * 100
                check("Kênh: vị trí giá trong kênh", abs(pos - ch["position_pct"]) < 0.2, f"{pos:.1f} vs {ch['position_pct']}")
        b = d.get("base") or {}
        if b.get("top") and b.get("low") is not None and b.get("start_date"):
            s = dates.index(b["start_date"])
            check("Nền: đỉnh nền = giá cao nhất từ ngày bắt đầu", abs(max(hi[s:]) - b["top"]) < 0.01 and abs(hi[s] - b["top"]) < 0.01,
                  f"max {max(hi[s:])}, nến đầu {hi[s]}, API {b['top']}")
            k = dates.index(b["low_date"])
            check("Nền: đáy nền = giá thấp nhất sau đỉnh", abs(min(lo[s:]) - b["low"]) < 0.01 and abs(lo[k] - b["low"]) < 0.01,
                  f"min {min(lo[s:])}, API {b['low']} ngày {b['low_date']}")
            check("Nền: giữa nền", abs((b["top"] + b["low"]) / 2 - b["mid"]) < 0.01)
            check("Nền: độ sâu", abs((1 - b["low"] / b["top"]) * 100 - b["depth_pct"]) < 0.01,
                  f"{(1 - b['low'] / b['top']) * 100:.2f} vs {b['depth_pct']}")
        st = d.get("state") or {}
        if st.get("trade_date") == dates[-1]:      # a state from an older session (an unmonitored ticker) is not comparable
            same = lambda x, y: x == y or (x is not None and y is not None and abs(x - y) < 0.01)  # noqa: E731 -- float noise
            check("Pivot/stop: biểu đồ = trạng thái theo dõi cùng phiên", same(b.get("pivot"), st.get("pivot")) and same(b.get("stop"), st.get("stop")),
                  f"biểu đồ {b.get('pivot')}/{b.get('stop')}, theo dõi {st.get('pivot')}/{st.get('stop')} ({st.get('trade_date')})")
        elif b.get("pivot"):
            f.append({"check": "Pivot/stop: mã không theo dõi phiên này — biểu đồ dùng nền tính mới", "ok": True,
                      "detail": f"biểu đồ {b.get('pivot')}/{b.get('stop')}; trạng thái lưu {st.get('trade_date')}"})
        if b.get("pivot"):
            if b.get("stop"):
                check("Pivot/stop: stop dưới pivot", b["stop"] < b["pivot"])
                dist = (last / b["pivot"] - 1) * 100
                check("Pivot: % cách pivot", abs(dist - b["distance_to_pivot_pct"]) < 0.01, f"{dist:.2f} vs {b['distance_to_pivot_pct']}")
        if t in server.INDEX_NAMES and tf == "D":
            m = d["market"]
            check("Chỉ số: số ngày phân phối = số dấu PP vẽ trên biểu đồ", m["distribution_days"] == len(d["distribution_dates"]),
                  f"{m['distribution_days']} vs {len(d['distribution_dates'])}")
        out[t] = {"findings": f, "zones": lv.get("zones"), "ok": all(x["ok"] for x in f),
                  "failed": [x for x in f if not x["ok"]]}
    return out


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--round", type=int, required=True, choices=(1, 2, 3))
    p.add_argument("--tickers", nargs="*", default=DEFAULT)
    p.add_argument("--tag", default="")
    p.add_argument("--tf", default="D", choices=("D", "W", "M"), help="round 3: daily, weekly or monthly chart")
    o = p.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")
    tickers = [t.upper() for t in o.tickers]
    res = round3(tickers, o.tf) if o.round == 3 else {1: round1, 2: round2}[o.round](tickers)
    OUT.mkdir(parents=True, exist_ok=True)
    path = OUT / f"chart_round{o.round}{'' if o.tf == 'D' else '_' + o.tf}{o.tag}.json"
    path.write_text(json.dumps({"run_at": datetime.now(timezone.utc).isoformat(), "round": o.round, "results": res},
                               ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    print(path)


if __name__ == "__main__":
    main()
