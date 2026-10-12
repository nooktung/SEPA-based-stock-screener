"""Position check: hold the user's own position against the SEPA rules.

The user enters an average cost (and optionally a quantity). This module places
that position among the price levels the system already measures -- the 7-8%
maximum loss, the reference stop, MA50 / MA200, the nearest support and
resistance, the base pivot -- and describes the situation the position is in,
with what the SEPA rules say about that situation, the last five sessions
(price, volume, foreign flow), the trend, fundamentals, market and news.

The text changes with the situation (a loss beyond the maximum, a loss inside
it, a small gain, a large gain), but it deliberately never says "buy more" or
"sell": the decision stays with the user. Thresholds: `position_check` in
config/thresholds.yaml.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import pandas as pd

from src.quant import indicators as ind

SELL_SIGNALS = {
    "STOP_BREACHED": "thủng stop tham chiếu",
    "BREAKOUT_FAILED": "breakout thất bại",
    "MA50_BREAK_HEAVY_VOLUME": "thủng MA50 với khối lượng lớn",
    "LARGEST_DOWN_DAY": "phiên giảm mạnh nhất 120 phiên",
    "TREND_TEMPLATE_BROKEN": "mất tiêu chí Trend Template",
    "CLIMAX_WARNING": "dấu hiệu đỉnh climax",
}
CRITERIA_MEANING = {
    "TT1": "giá dưới MA150 / MA200 (mất xu hướng dài hạn)",
    "TT2": "MA150 dưới MA200",
    "TT3": "MA200 không còn đi lên",
    "TT4": "MA50 dưới MA150 / MA200",
    "TT5": "giá dưới MA50 (mất hỗ trợ trung hạn)",
    "TT6": "giá chưa cách đáy 52 tuần đủ xa",
    "TT7": "giá cách đỉnh 52 tuần quá 25%",
    "TT8": "RS dưới 70 (yếu hơn thị trường)",
}


def _vnd(x: float) -> str:
    """24750 -> '24.750' (Vietnamese thousands separator)."""
    return f"{x:,.0f}".replace(",", ".")


def _pct(x: float, signed: bool = True) -> str:
    return (f"{x:+.1f}" if signed else f"{x:.1f}").replace(".", ",") + "%"


def _item(level: str, text: str, rule: str = "") -> dict[str, str]:
    return {"level": level, "text": text, "rule": rule}


def check_position(*, cost: float, qty: float | None, frame: pd.DataFrame, trade_date: str,
                   criteria: Mapping[str, Mapping[str, Any]] | None, setup: Mapping[str, Any] | None,
                   pivot: float | None, stop: float | None, zones: Sequence[Mapping[str, Any]],
                   alerts: Sequence[tuple[str, str]], flows: Sequence[Mapping[str, Any]],
                   fundamentals: Mapping[str, Any] | None, market: Mapping[str, Any], market_note: str,
                   news: Sequence[Mapping[str, Any]], liquidity_tier: str | None, locked_pct: float | None,
                   cfg: Mapping[str, Any], exchange: str | None = None) -> dict[str, Any]:
    close_s = frame["close"].astype("float64")
    close = float(close_s.iloc[-1])
    max_loss, warn_loss = float(cfg["max_loss_pct"]), float(cfg["warn_loss_pct"])
    pnl = (close / cost - 1) * 100.0
    max_loss_price = cost * (1 - max_loss / 100.0)
    if exchange:
        # On the tick grid, rounded up so the loss to it stays within max_loss (35,880 -> 35,900; B13).
        from src.quant.screener import round_to_tick  # noqa: PLC0415 - avoids an import cycle
        max_loss_price = round_to_tick(max_loss_price, exchange, up=True)
    big_gain = max_loss * float(cfg["breakeven_gain_multiple"])
    ma50 = float(ind.sma(close_s, 50).iloc[-1]) if len(close_s) >= 50 else None
    ma200 = float(ind.sma(close_s, 200).iloc[-1]) if len(close_s) >= 200 else None
    support = next((z for z in sorted(zones, key=lambda z: -z["center"]) if z.get("side") == "SUPPORT"), None)
    resist = next((z for z in sorted(zones, key=lambda z: z["center"]) if z.get("side") == "RESISTANCE"), None)

    # ---------------------------------------------------------------- situation
    if pnl <= -max_loss:
        situation, sit_level = "loss_beyond", "bad"
        headline = f"Đang lỗ {_pct(-pnl, False)} — đã vượt mức lỗ tối đa {max_loss:.0f}% theo Minervini."
    elif pnl < 0:
        situation, sit_level = "loss_within", "warn" if pnl <= -warn_loss else "info"
        headline = f"Đang lỗ {_pct(-pnl, False)} — còn {_pct(max_loss + pnl, False)} nữa là chạm mức lỗ tối đa {max_loss:.0f}%."
    elif pnl < big_gain:
        situation, sit_level = "gain_small", "good"
        headline = "Giá hiện tại bằng giá vốn (hòa vốn)." if pnl < 0.05 else f"Đang lãi {_pct(pnl, False)}."
    else:
        situation, sit_level = "gain_big", "good"
        headline = f"Đang lãi {_pct(pnl, False)} — đã vượt {big_gain:.0f}% (gấp {cfg['breakeven_gain_multiple']} lần mức lỗ tối đa)."

    # ---------------------------------------------------------------- price levels
    def lv(name: str, price: float | None, note: str = "") -> dict[str, Any] | None:
        if price is None or price != price:
            return None
        return {"name": name, "price": round(price, 0), "vs_cost_pct": round((price / cost - 1) * 100, 1),
                "vs_close_pct": round((price / close - 1) * 100, 1), "note": note}

    levels = [x for x in (
        lv("Giá hiện tại", close, f"phiên {trade_date[8:10]}/{trade_date[5:7]}/{trade_date[:4]}"),
        lv("Giá vốn của bạn", cost),
        lv(f"Mức lỗ tối đa {max_loss:.0f}%", max_loss_price, "quy tắc Minervini"),
        lv("Stop tham chiếu", stop, "đáy nhịp co hẹp cuối hoặc pivot − 8%"),
        lv("Pivot nền giá", pivot, "điểm phá nền"),
        lv("MA50", ma50, "hỗ trợ trung hạn"),
        lv("MA200", ma200, "xu hướng dài hạn"),
        lv("Hỗ trợ gần nhất", support["center"] if support else None,
           f"vùng {_vnd(support['zone_low'])}–{_vnd(support['zone_high'])}" if support else ""),
        lv("Kháng cự gần nhất", resist["center"] if resist else None,
           f"vùng {_vnd(resist['zone_low'])}–{_vnd(resist['zone_high'])}" if resist else ""),
    ) if x]
    levels.sort(key=lambda x: -x["price"])

    sections: list[dict[str, Any]] = []

    # ---------------------------------------------------------------- 1. loss / gain rules, by situation
    items = []
    money = f" ({'+' if close >= cost else '−'}{_vnd(abs(close - cost) * qty)} đ trên {_vnd(qty)} cp)" if qty else ""
    items.append(_item(sit_level, f"Giá {_vnd(close)} so với vốn {_vnd(cost)}: {_pct(pnl)}{money}."))
    if situation == "loss_beyond":
        recover = (cost / close - 1) * 100
        items.append(_item("bad", f"Mức lỗ tối đa {max_loss:.0f}% tương ứng giá {_vnd(max_loss_price)}; giá hiện thấp hơn mức đó "
                                  f"{_pct((close / max_loss_price - 1) * 100, False).lstrip('-')}. Để về hòa vốn cần tăng {_pct(recover)}.",
                           "[SÁCH] Minervini: cắt lỗ tối đa 7–8%, không gồng lỗ"))
    elif situation == "loss_within":
        items.append(_item(sit_level, f"Mức lỗ tối đa {max_loss:.0f}% ở giá {_vnd(max_loss_price)} "
                                      f"({_pct((max_loss_price / close - 1) * 100)} so với giá hiện tại).",
                           "[SÁCH] Minervini: cắt lỗ tối đa 7–8%"))
        if support:
            above = support["center"] > max_loss_price
            items.append(_item("info", f"Hỗ trợ gần nhất {_vnd(support['zone_low'])}–{_vnd(support['zone_high'])} "
                                       f"({_pct(support['distance_from_price'])}) nằm {'trên' if above else 'dưới'} mức lỗ tối đa."))
    elif situation == "gain_small":
        items.append(_item("info", f"Mức lỗ tối đa {max_loss:.0f}% ở giá {_vnd(max_loss_price)} "
                                   f"({_pct((max_loss_price / close - 1) * 100)} so với giá hiện tại) — lãi hiện tại chưa đủ lớn để dời stop lên hòa vốn.",
                           "[SÁCH] Minervini: không để một khoản lãi đáng kể biến thành lỗ"))
    else:
        items.append(_item("good", f"Theo Minervini, khi lãi đã gấp {cfg['breakeven_gain_multiple']} lần mức lỗ tối đa thì dời stop lên "
                                   f"tối thiểu hòa vốn ({_vnd(cost)}, {_pct((cost / close - 1) * 100)} so với giá hiện tại)."
                                   + (f" MA50 ở {_vnd(ma50)} ({_pct((ma50 / close - 1) * 100)})." if ma50 else ""),
                           "[SÁCH] bảo vệ lợi nhuận"))
    if resist:
        items.append(_item("info", f"Kháng cự gần nhất {_vnd(resist['zone_low'])}–{_vnd(resist['zone_high'])} ({_pct(resist['distance_from_price'])})"
                                   + (f"; pivot nền {_vnd(pivot)} ({_pct((pivot / close - 1) * 100)})." if pivot and pivot > close else ".")))
    sections.append({"title": "Lãi / lỗ và các mốc theo quy tắc", "items": items})

    # ---------------------------------------------------------------- 2. trend
    items = []
    crit = {k: v for k, v in (criteria or {}).items() if isinstance(v, Mapping)}
    passed = sum(1 for v in crit.values() if v.get("pass"))
    missing = [k for k, v in crit.items() if v.get("pass") is False]
    if crit:
        if not missing:
            items.append(_item("good", "Đạt 8/8 Trend Template — cổ phiếu đang ở Stage 2 (xu hướng tăng).", "[SÁCH] Trend Template"))
        else:
            items.append(_item("good" if passed == 8 else "warn" if passed >= 6 else "bad",
                               f"Đạt {passed}/8 Trend Template. Thiếu: " + "; ".join(CRITERIA_MEANING.get(k, k) for k in missing) + ".",
                               "[SÁCH] Trend Template"))
    if ma50 is not None:
        items.append(_item("good" if close >= ma50 and (ma200 is None or close >= ma200) else "warn",
                           f"Giá {'trên' if close >= ma50 else 'dưới'} MA50 ({_vnd(ma50)}, {_pct((close / ma50 - 1) * 100)})"
                           + (f"; {'trên' if close >= ma200 else 'dưới'} MA200 ({_vnd(ma200)}, {_pct((close / ma200 - 1) * 100)})." if ma200 else ".")))
    sells = [(d, t) for d, t in alerts if t in SELL_SIGNALS]
    n = int(cfg["signal_lookback_sessions"])
    if sells:
        items.append(_item("bad", f"Tín hiệu bán SEPA trong {n} phiên: " + "; ".join(f"{SELL_SIGNALS[t]} ngày {d}" for d, t in sells[:5]) + ".",
                           "[SÁCH] bán khi cổ phiếu yếu"))
    else:
        items.append(_item("good", f"Không có tín hiệu bán SEPA trong {n} phiên gần nhất."))
    sections.append({"title": "Xu hướng", "items": items})

    # ---------------------------------------------------------------- 3. last five sessions
    items = []
    if len(close_s) >= 6:
        chg5 = (close / float(close_s.iloc[-6]) - 1) * 100
        downs = int((close_s.diff().iloc[-5:] < 0).sum())
        vol = frame["volume"].astype("float64")
        vr = float(vol.iloc[-5:].mean() / vol.iloc[-55:-5].mean()) if len(vol) >= 55 and vol.iloc[-55:-5].mean() > 0 else None
        items.append(_item("good" if chg5 > 0 else "warn" if chg5 > -5 else "bad",
                           f"5 phiên: giá {_pct(chg5)}, {downs}/5 phiên giảm"
                           + (f", khối lượng trung bình bằng {vr:.1f}".replace(".", ",") + " lần 50 phiên trước đó." if vr else ".")))
    if flows:
        nets = [f.get("foreign_net") for f in flows[:5] if f.get("foreign_net") is not None]
        if nets:
            tot = sum(nets)
            buys = sum(1 for x in nets if x > 0)
            gap = f"; {len(nets)}/{len(flows[:5])} phiên có số liệu khối ngoại" if len(nets) < len(flows[:5]) else ""
            items.append(_item("good" if tot > 0 else "warn" if tot < 0 else "info",
                               f"Khối ngoại 5 phiên: ròng {'mua' if tot >= 0 else 'bán'} {_vnd(abs(tot))} cp "
                               f"({buys}/{len(nets)} phiên mua ròng{gap})."))
    if items:
        sections.append({"title": "Diễn biến gần đây", "items": items})

    # ---------------------------------------------------------------- 4. what SEPA requires before adding
    status = (setup or {}).get("status")
    unmet = []
    if pnl < 0:
        unmet.append("vị thế đang lỗ — SEPA không bình quân giá xuống")
    if market.get("status") == "CORRECTION":
        unmet.append("thị trường đang điều chỉnh")
    if status not in ("AT_PIVOT", "BREAKOUT"):
        unmet.append("giá chưa ở điểm mua mới (vùng pivot / vừa breakout khỏi nền)")
    if crit and passed < 8:
        unmet.append("chưa đạt đủ 8/8 Trend Template")
    rule = "[SÁCH] chỉ gia tăng vị thế đang lãi, tại điểm mua mới, khi thị trường cho phép"
    sections.append({"title": "Điều kiện gia tăng theo SEPA", "items": [
        _item("good", "Đủ các điều kiện kỹ thuật SEPA cho việc gia tăng.", rule) if not unmet else
        _item("bad" if pnl < 0 else "warn", "Chưa đủ: " + "; ".join(unmet) + ".", rule)]})

    # ---------------------------------------------------------------- 5. fundamentals, market, liquidity, news
    items = []
    f = fundamentals or {}
    grade = f.get("grade")
    if grade and grade != "NO_DATA":
        yoy = [v for v in (f.get("profit_yoy") or []) if v is not None]
        rev = [v for v in (f.get("revenue_yoy") or []) if v is not None]
        items.append(_item({"A+": "good", "A": "good", "B": "info", "WEAK": "warn", "FAIL": "bad"}.get(grade, "info"),
                           f"Cơ bản hạng {grade}"
                           + (f": LNST quý gần nhất {_pct(yoy[0])} so với cùng kỳ" if yoy else "")
                           + (f", doanh thu {_pct(rev[0])}" if rev else "") + "."
                           + (f" {'; '.join(f.get('reasons') or [])}." if f.get("reasons") else ""),
                           "CafeF, chưa đối chiếu BCTC gốc"))
    else:
        items.append(_item("info", "Chưa có hạng cơ bản" + (f": {'; '.join(f.get('reasons') or [])}." if f.get("reasons") else ".")))
    items.append(_item({"UPTREND": "good", "CORRECTION": "bad"}.get(market.get("status"), "warn"), market_note or "–",
                       "[TRANG GỐC IBD] FTD / ngày phân phối"))
    if liquidity_tier == "LOW" or locked_pct is not None:
        txt = []
        if liquidity_tier == "LOW":
            txt.append("thanh khoản thấp — khó bán lượng lớn")
        if locked_pct is not None:
            lock = f"nếu giảm sàn liên tiếp tới khi nhận hàng (T+2) có thể lỗ thêm ~{_pct(locked_pct, False)}"
            if qty:
                lock += f" (≈ {_vnd(close * qty * locked_pct / 100)} đ)"
            txt.append(lock)
        items.append(_item("warn" if liquidity_tier == "LOW" else "info", "Rủi ro thanh khoản: " + "; ".join(txt) + "."))
    if news:
        items.append(_item("info", f"{len(news)} tin {int(cfg['news_days'])} ngày qua, gần nhất: "
                                   + "; ".join(str(x.get("headline", ""))[:80] for x in news[:2]) + "."))
    sections.append({"title": "Cơ bản, thị trường, tin tức", "items": items})

    all_items = [i for s in sections for i in s["items"]]
    counts = {lv_: sum(1 for i in all_items if i["level"] == lv_) for lv_ in ("good", "warn", "bad", "info")}
    return {"pnl_pct": round(pnl, 2), "max_loss_price": round(max_loss_price, 0), "situation": situation,
            "headline": headline, "levels": levels, "sections": sections, "counts": counts,
            "note": "Bảng đối chiếu vị thế với quy tắc SEPA và dữ liệu hiện có — không phải lời khuyên mua, bán "
                    "hay giữ. Quyết định thuộc về bạn."}
