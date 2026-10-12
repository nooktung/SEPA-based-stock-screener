"""RS line: close / index close, scaled to 100, new highs and the line leading the price."""

from src.config import load_config
from src.quant import rs_line as rsl


def _bars(closes, start=1):
    return [{"trade_date": f"2026-01-{i + start:02d}", "close": c} for i, c in enumerate(closes)]


def test_the_line_is_the_ratio_scaled_to_100_and_skips_missing_index_sessions():
    bars = _bars([10.0, 11.0, 12.0, 12.0])
    index = {"2026-01-01": 100.0, "2026-01-02": 100.0, "2026-01-04": 120.0}      # 03/01 missing: no point
    out = rsl.rs_line(bars, index, lookback=2, trend=1)
    assert [p["time"] for p in out["points"]] == ["2026-01-01", "2026-01-02", "2026-01-04"]
    assert [p["value"] for p in out["points"]] == [100.0, 110.0, 100.0]


def test_a_new_high_of_the_line_while_the_price_is_below_its_high_is_a_lead():
    # The stock holds 10 while the index falls: the line climbs, the price does not.
    bars = _bars([12.0, 11.0, 10.0, 10.0, 10.0, 10.0])
    index = {b["trade_date"]: v for b, v in zip(bars, (100.0, 100.0, 100.0, 95.0, 90.0, 85.0))}
    out = rsl.rs_line(bars, index, lookback=3, trend=2)
    assert out["new_highs"] == ["2026-01-05", "2026-01-06"]
    assert out["leads"] == ["2026-01-05"]          # 06/01: 10 is the price's own 3-session high, no lead
    s = out["summary"]
    assert s["at_new_high"] and s["leads_on"] == "2026-01-05" and s["from_high_pct"] == 0.0
    assert s["change_pct"] == round((95 / 85 - 1) * 100, 2)


def test_weekly_bars_use_the_index_on_the_weeks_last_session():
    bars = [{"trade_date": "2026-01-05", "period_end": "2026-01-09", "close": 20.0},
            {"trade_date": "2026-01-12", "period_end": "2026-01-16", "close": 22.0}]
    index = {"2026-01-05": 1.0, "2026-01-09": 100.0, "2026-01-12": 1.0, "2026-01-16": 100.0}
    out = rsl.rs_line(bars, index, lookback=1, trend=1, date_key="period_end")
    assert [p["value"] for p in out["points"]] == [100.0, 110.0]


def test_too_few_points_give_no_line():
    assert rsl.rs_line(_bars([10.0]), {"2026-01-01": 100.0}, 250, 63) is None


def test_the_periods_come_from_the_config():
    th = load_config().thresholds.raw
    assert rsl.settings(th, "D") == (250, 63) and rsl.settings(th, "W") == (52, 13)
