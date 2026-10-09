import numpy as np
import pandas as pd

from src.config import load_config
from src.quant.position_check import check_position

CFG = load_config().thresholds["position_check"]


def frame(last: float, n: int = 260) -> pd.DataFrame:
    """A steady rise ending at `last` (so MA50 / MA200 sit below the price)."""
    closes = np.linspace(last * 0.6, last, n)
    idx = pd.bdate_range(end="2026-10-01", periods=n)
    return pd.DataFrame({"open": closes, "high": closes * 1.01, "low": closes * 0.99, "close": closes,
                         "volume": np.full(n, 100_000.0)}, index=idx)


def run(close=100_000, **kw):
    base = dict(cost=100_000, qty=None, frame=frame(close), trade_date="2026-10-01",
                criteria={f"TT{i}": {"pass": True} for i in range(1, 9)}, setup={"status": "WATCH"},
                pivot=None, stop=None, zones=[], alerts=[], flows=[],
                fundamentals={"grade": "A", "profit_yoy": [40.0]}, market={"status": "UPTREND"},
                market_note="", news=[], liquidity_tier="HIGH", locked_pct=19.6, cfg=CFG)
    base.update(kw)
    r = check_position(**base)
    return r, {s["title"]: s["items"] for s in r["sections"]}


def text(items):
    return " ".join(i["text"] for i in items)


def test_a_loss_beyond_the_maximum_is_flagged_and_never_averaged_down():
    r, s = run(close=90_000, qty=1000)
    assert r["pnl_pct"] == -10.0 and r["situation"] == "loss_beyond"
    pnl = s["Lãi / lỗ và các mốc theo quy tắc"]
    assert pnl[0]["level"] == "bad" and "−10.000.000 đ" in pnl[0]["text"]
    assert "92.000" in text(pnl) and "hòa vốn cần tăng +11,1%" in text(pnl)
    add = s["Điều kiện gia tăng theo SEPA"][0]
    assert add["level"] == "bad" and "bình quân giá xuống" in add["text"]


def test_a_small_loss_says_how_far_the_maximum_loss_is():
    r, s = run(close=97_000)
    assert r["situation"] == "loss_within"
    assert "còn 5,0% nữa là chạm mức lỗ tối đa 8%" in r["headline"]
    assert "92.000" in text(s["Lãi / lỗ và các mốc theo quy tắc"])


def test_a_winning_position_at_a_new_buy_point_meets_the_add_conditions():
    r, s = run(close=110_000, setup={"status": "BREAKOUT"})
    assert r["situation"] == "gain_small"
    assert s["Điều kiện gia tăng theo SEPA"][0]["level"] == "good"


def test_a_big_gain_reminds_to_protect_it():
    r, s = run(close=120_000)
    assert r["situation"] == "gain_big" and "hòa vốn" in text(s["Lãi / lỗ và các mốc theo quy tắc"])


def test_price_levels_are_placed_against_cost_and_price():
    zones = [{"center": 104_000, "zone_low": 103_500, "zone_high": 104_500, "side": "RESISTANCE", "distance_from_price": 4.0},
             {"center": 96_000, "zone_low": 95_500, "zone_high": 96_500, "side": "SUPPORT", "distance_from_price": -4.0}]
    r, _ = run(close=100_000, cost=95_000, pivot=105_000, stop=93_000, zones=zones)
    names = [lv["name"] for lv in r["levels"]]
    assert names[0] == "Pivot nền giá" and names.index("Giá hiện tại") < names.index("Giá vốn của bạn")
    prices = [lv["price"] for lv in r["levels"]]
    assert prices == sorted(prices, reverse=True)
    cost = next(lv for lv in r["levels"] if lv["name"] == "Giá vốn của bạn")
    assert cost["vs_close_pct"] == -5.0
    assert {"MA50", "MA200", "Hỗ trợ gần nhất", "Kháng cự gần nhất", "Stop tham chiếu"} <= set(names)


def test_recent_sessions_and_foreign_flow_are_described():
    flows = [{"foreign_net": 5_000}, {"foreign_net": -1_000}, {"foreign_net": 2_000}]
    _, s = run(flows=flows)
    recent = text(s["Diễn biến gần đây"])
    assert "5 phiên" in recent and "ròng mua 6.000 cp (2/3 phiên mua ròng)" in recent


def test_sell_signals_and_a_correction_are_reported():
    _, s = run(alerts=[("2026-10-01", "STOP_BREACHED"), ("2026-09-30", "SETUP_READY")],
               market={"status": "CORRECTION"}, market_note="Thị trường điều chỉnh")
    trend = s["Xu hướng"]
    assert any(i["level"] == "bad" and "thủng stop" in i["text"] for i in trend)
    assert "SETUP" not in text(trend)
    assert "điều chỉnh" in s["Điều kiện gia tăng theo SEPA"][0]["text"]


def test_no_verdict_is_ever_given():
    r, s = run(close=70_000, alerts=[("2026-10-01", "STOP_BREACHED")], fundamentals={"grade": "FAIL"})
    words = (" ".join(text(v) for v in s.values()) + r["headline"]).lower()
    assert not any(w in words for w in ("nên bán", "nên mua", "khuyến nghị bán", "khuyến nghị mua"))
    assert "không phải lời khuyên" in r["note"]


def test_cost_equal_to_price_reads_as_breakeven():
    r, _ = run(close=100_000, cost=100_000)
    assert r["headline"] == "Giá hiện tại bằng giá vốn (hòa vốn)." and r["pnl_pct"] == 0
