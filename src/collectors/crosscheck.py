"""Cross-check DNSE (the primary series) against SSI FastConnect Data snapshots.

Report only -- nothing in the DNSE tables is changed. Sections:

* closes: the same session's close; a DNSE close that matches SSI's *adjusted*
  close instead is a later corporate action already adjusted by DNSE, not an error.
* volumes: DNSE volume against SSI's matched volume, and against matched +
  put-through, to tell which definition each side uses.
* foreign flow: shares bought / sold by foreign investors.
* index volume: DNSE's VN-Index volume per session classified as matched only,
  matched + put-through or neither (audit 2026-10-04 found DNSE mixing the two),
  with the HOSE stocks' summed volume as a third reference, and the distribution
  days each definition would count.
* corporate actions: a reference price away from the previous close (the prior
  VWAP on UPCoM) marks an ex-date; DNSE's stored previous close tells whether
  its history already reflects it (cash dividends usually are not).

Tolerances: `crosscheck` in config/thresholds.yaml.
"""

from __future__ import annotations

from collections import Counter, defaultdict
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

from src.config import load_config
from src.database import connect


def _pct(a: float | None, b: float | None) -> float | None:
    if a is None or b is None or b == 0:
        return None
    return (a / b - 1) * 100.0


def _within(a: float | None, b: float | None, tol_pct: float) -> bool:
    if a is None or b is None:
        return False
    if b == 0:
        return a == 0
    return abs(a / b - 1) * 100.0 <= tol_pct


def crosscheck(days: int = 10, end: date | None = None, path: Path | None = None,
               cfg: Mapping[str, Any] | None = None, market_cfg: Mapping[str, Any] | None = None) -> dict[str, Any]:
    th = load_config().thresholds
    cfg = cfg or th["crosscheck"]
    market_cfg = market_cfg or th["sepa_screen"]["market"]
    with connect(path) as conn:
        last = conn.execute("SELECT MAX(trade_date) FROM ssi_stock_daily").fetchone()[0]
        if end is None and last is None:
            return {"ok": False, "note": "Chưa có dữ liệu SSI. Chạy: python -m src.main ssi"}
        end_s = (end.isoformat() if end else last)
        since_s = (date.fromisoformat(end_s) - timedelta(days=days)).isoformat()
        stock = [dict(r) for r in conn.execute(
            "SELECT s.trade_date, s.ticker, s.market, s.close AS ssi_close, s.close_adjusted, s.match_volume, s.deal_volume, "
            "p.close AS dnse_close, p.volume AS dnse_volume FROM ssi_stock_daily s JOIN prices_daily p "
            "ON p.ticker = s.ticker AND p.trade_date = s.trade_date WHERE s.trade_date BETWEEN ? AND ?", (since_s, end_s))]
        flows = [dict(r) for r in conn.execute(
            "SELECT s.trade_date, s.ticker, s.foreign_buy_volume AS ssi_buy, s.foreign_sell_volume AS ssi_sell, "
            "f.foreign_buy_volume AS dnse_buy, f.foreign_sell_volume AS dnse_sell FROM ssi_stock_daily s JOIN flow_daily f "
            "ON f.scope = 'TICKER' AND f.scope_key = s.ticker AND f.trade_date = s.trade_date "
            "WHERE s.trade_date BETWEEN ? AND ? AND f.foreign_buy_volume IS NOT NULL", (since_s, end_s))]
        index = [dict(r) for r in conn.execute(
            "SELECT s.trade_date, s.match_volume, s.deal_volume, s.total_volume, s.advances, s.declines, "
            "i.volume AS dnse_volume, i.matched_volume AS dnse_matched, i.close AS dnse_close "
            "FROM ssi_index_daily s JOIN index_daily i "
            "ON i.symbol = s.index_id AND i.trade_date = s.trade_date WHERE s.index_id = 'VNINDEX' "
            "AND s.trade_date BETWEEN ? AND ? ORDER BY s.trade_date", (since_s, end_s))]
        hose_sum = dict(conn.execute(
            "SELECT p.trade_date, SUM(p.volume) FROM prices_daily p JOIN tickers t ON t.ticker = p.ticker "
            "WHERE t.exchange = 'HOSE' AND p.trade_date BETWEEN ? AND ? GROUP BY p.trade_date", (since_s, end_s)).fetchall())
        refs = [dict(r) for r in conn.execute(
            "SELECT trade_date, ticker, market, ref_price, close, avg_price FROM ssi_stock_daily "
            "WHERE trade_date BETWEEN ? AND ? ORDER BY ticker, trade_date",
            ((date.fromisoformat(since_s) - timedelta(days=10)).isoformat(), end_s))]
        dnse_close = {(r[0], r[1]): r[2] for r in conn.execute(
            "SELECT ticker, trade_date, close FROM prices_daily WHERE trade_date BETWEEN ? AND ?",
            ((date.fromisoformat(since_s) - timedelta(days=10)).isoformat(), end_s))}
    n_list = int(cfg["max_listed"])

    # -- closes and volumes
    closes, volumes = Counter(), Counter()
    close_bad, vol_bad = [], []
    for r in stock:
        if r["ssi_close"]:
            if _within(r["dnse_close"], r["ssi_close"], cfg["close_tolerance_pct"]):
                closes["khớp"] += 1
            elif _within(r["dnse_close"], r["close_adjusted"], cfg["close_tolerance_pct"]):
                closes["khớp giá đã điều chỉnh của SSI"] += 1
            else:
                closes["lệch"] += 1
                close_bad.append({"ticker": r["ticker"], "date": r["trade_date"], "dnse": r["dnse_close"],
                                  "ssi": r["ssi_close"], "ssi_adjusted": r["close_adjusted"],
                                  "diff_pct": round(_pct(r["dnse_close"], r["ssi_close"]) or 0, 2)})
        if r["match_volume"] is not None and r["dnse_volume"] is not None and (r["match_volume"] or r["dnse_volume"]):
            if _within(r["dnse_volume"], r["match_volume"], cfg["volume_tolerance_pct"]):
                volumes["khớp lệnh"] += 1
            elif _within(r["dnse_volume"], (r["match_volume"] or 0) + (r["deal_volume"] or 0), cfg["volume_tolerance_pct"]):
                volumes["khớp lệnh + thỏa thuận"] += 1
            else:
                volumes["lệch"] += 1
                vol_bad.append({"ticker": r["ticker"], "date": r["trade_date"], "dnse": r["dnse_volume"],
                                "ssi_match": r["match_volume"], "ssi_deal": r["deal_volume"]})
    close_bad.sort(key=lambda x: -abs(x["diff_pct"]))

    # -- foreign flow
    foreign, foreign_bad = Counter(), []
    for r in flows:
        ok = all(_within(a, b, cfg["foreign_tolerance_pct"]) for a, b in ((r["dnse_buy"], r["ssi_buy"]), (r["dnse_sell"], r["ssi_sell"])))
        foreign["khớp" if ok else "lệch"] += 1
        if not ok:
            foreign_bad.append(r)

    # -- index volume definitions and the distribution days each would give
    ratio = float(cfg["index_put_through_ratio"])
    tol = float(cfg["volume_tolerance_pct"])
    sessions = []
    for r in index:
        if _within(r["dnse_volume"], r["match_volume"], tol):
            kind = "khớp lệnh"
        elif _within(r["dnse_volume"], r["total_volume"], tol):
            kind = "khớp lệnh + thỏa thuận"
        elif r["match_volume"] and r["dnse_volume"] and r["dnse_volume"] / r["match_volume"] > ratio:
            kind = "cao hơn khớp lệnh (chưa rõ phần cộng thêm)"
        else:
            kind = "khác"
        sessions.append({**r, "dnse_is": kind, "hose_stocks_sum": hose_sum.get(r["trade_date"]),
                         "dnse_over_match": round(r["dnse_volume"] / r["match_volume"], 3) if r["match_volume"] else None})
    drop = float(market_cfg["distribution_min_drop_pct"])

    def dist(key: str) -> list[str]:
        out = []
        for a, b in zip(sessions, sessions[1:]):
            if a["dnse_close"] and b["dnse_close"] and a.get(key) is not None and b.get(key) is not None:
                if (b["dnse_close"] / a["dnse_close"] - 1) * 100 <= -drop and b[key] > a[key]:
                    out.append(b["trade_date"])
        return out

    index_section = {
        "sessions": len(sessions),
        "dnse_volume_is": dict(Counter(s["dnse_is"] for s in sessions)),
        # DNSE market-index matched volume against SSI's (index_session.py, 2026-10-08)
        "dnse_matched_vs_ssi": dict(Counter(
            "khớp" if _within(s["dnse_matched"], s["match_volume"], tol) else "lệch"
            for s in sessions if s.get("dnse_matched") is not None and s.get("match_volume") is not None)),
        "distribution_days": {"theo DNSE (OHLC)": dist("dnse_volume"), "theo DNSE khớp lệnh": dist("dnse_matched"),
                              "theo SSI khớp lệnh": dist("match_volume"),
                              "theo SSI khớp + thỏa thuận": dist("total_volume"),
                              "theo tổng cổ phiếu HOSE": dist("hose_stocks_sum")},
        "rows": sessions[-n_list:],
    }

    # -- corporate actions: reference price vs the previous session
    by_ticker: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in refs:
        by_ticker[r["ticker"]].append(r)
    actions = []
    for t, rows in by_ticker.items():
        for prev, cur in zip(rows, rows[1:]):
            if cur["trade_date"] < since_s:
                continue
            base = prev["avg_price"] if (cur["market"] or "").upper() == "UPCOM" else prev["close"]
            if not cur["ref_price"] or not base:
                continue
            move = _pct(cur["ref_price"], base)
            if move is not None and abs(move) > float(cfg["reference_tolerance_pct"]):
                dnse_prev = dnse_close.get((t, prev["trade_date"]))
                actions.append({
                    "ticker": t, "date": cur["trade_date"], "ref_price": cur["ref_price"], "previous": base,
                    "change_pct": round(move, 2), "dnse_previous_close": dnse_prev,
                    "dnse_history": ("đã điều chỉnh" if _within(dnse_prev, cur["ref_price"], cfg["reference_tolerance_pct"])
                                     else "chưa điều chỉnh — nên tải lại chuỗi DNSE" if dnse_prev else "chưa có giá DNSE"),
                })
    actions.sort(key=lambda a: (a["date"], a["ticker"]))

    return {
        "ok": True, "from": since_s, "to": end_s,
        "stocks": {"compared": len(stock), "closes": dict(closes), "volumes": dict(volumes),
                   "close_mismatches": close_bad[:n_list], "volume_mismatches": vol_bad[:n_list]},
        "foreign": {"compared": len(flows), "result": dict(foreign), "mismatches": foreign_bad[:n_list]},
        "index": index_section,
        "corporate_actions": actions,
    }
