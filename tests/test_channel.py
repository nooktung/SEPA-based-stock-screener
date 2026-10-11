from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.quant.channel import chart_levels, parallel_channel, trendline
from src.quant.screener import industry_leaders, screen_settings

TH = load_config().thresholds
CFG = TH["chart_levels"]["trendline"]


def _frame(close, spread=0.01):
    close = np.asarray(close, dtype="float64")
    idx = pd.bdate_range("2026-01-01", periods=len(close))
    return pd.DataFrame({"open": close, "high": close * (1 + spread), "low": close * (1 - spread),
                         "close": close, "volume": 1e6}, index=idx)


def _rising_waves(n=160, drop_at=None):
    x = np.arange(n)
    close = 10_000 + 20 * x + 600 * np.sin(x / 6.0)       # rising channel, swings every ~19 sessions
    if drop_at is not None:
        close[drop_at:] = close[drop_at - 1] * np.linspace(0.97, 0.85, n - drop_at)
    return _frame(close)


def test_support_line_joins_two_swing_lows_with_no_close_below_between():
    f = _rising_waves()
    line, g = trendline(f, "SUPPORT", CFG)
    (d1, p1), (d2, p2) = line.anchors
    assert d1 < d2 and p2 > p1 and line.slope_pct_month > 0 and line.touches >= 2
    i, j = f.index.get_loc(pd.Timestamp(d1)), f.index.get_loc(pd.Timestamp(d2))
    between = f["close"].to_numpy()[i:j + 1]
    assert (between >= np.array([g["at"](k) for k in range(i, j + 1)]) * (1 - CFG["tolerance_pct"] / 100) - 1e-6).all()
    assert line.broken_on is None and line.end[0] > f.index[-1].date().isoformat()   # unbroken: a ray forward


def test_a_close_through_the_line_marks_it_broken_and_stops_it_there():
    f = _rising_waves(drop_at=150)
    line, _ = trendline(f, "SUPPORT", CFG)
    assert line.broken_on is not None and line.end[0] == line.broken_on and line.value_today is None


def test_channel_is_parallel_and_holds_every_bar_of_its_span():
    f = _rising_waves()
    line, g = trendline(f, "SUPPORT", CFG)
    ch = parallel_channel(line, g)
    (u0, u1), (l0, l1) = [v for _, v in ch.upper], [v for _, v in ch.lower]
    assert u1 - l1 == pytest.approx(u0 - l0, abs=0.05)                  # parallel rails
    i, last = g["i"], g["last_bar"]
    upper = np.array([g["at"](k) for k in range(i, last + 1)]) + (u0 - l0)
    assert (f["high"].to_numpy()[i:last + 1] <= upper + 0.05).all()   # nothing above the upper rail
    assert 0 <= ch.position_pct <= 100


def test_resistance_line_joins_two_swing_highs():
    x = np.arange(160)
    f = _frame(20_000 - 15 * x + 600 * np.sin(x / 6.0))              # falling channel
    line, _ = trendline(f, "RESISTANCE", CFG)
    assert line.anchors[1][1] < line.anchors[0][1] and line.slope_pct_month < 0


def test_no_swings_no_line():
    assert trendline(_frame(np.full(80, 10_000.0)), "SUPPORT", CFG) is None


def test_chart_levels_keeps_only_the_nearest_zone_each_side():
    rng = np.random.default_rng(2)
    f = _frame(20_000 + np.cumsum(rng.normal(0, 200, 300)), spread=0.02)
    out = chart_levels(f, TH)
    sides = [z["side"] for z in out["zones"]]
    assert len(sides) == len(set(sides)) <= 2
    assert set(out["trendlines"]) <= {"SUPPORT", "RESISTANCE"}


def _cand(t, liq, grp=None):
    base = SimpleNamespace(kind="BASE", pivot=None, distance_to_pivot_pct=None)
    return SimpleNamespace(ticker=t, exchange="HOSE", group=grp, close=10_000.0, liquidity_vnd=liq, rs_rating=80.0,
                           passed=8, criteria={}, base=base, fundamentals={})


def test_leaders_are_the_most_traded_per_industry_strongest_industry_first():
    s = screen_settings()
    cands = [_cand("SSI", 900e9), _cand("VND", 500e9), _cand("VCK", 400e9), _cand("ORS", 20e9),
             _cand("HPG", 800e9, "A"), _cand("HSG", 100e9)]
    of = {"SSI": "4020", "VND": "4020", "VCK": "4020", "ORS": "4020", "HPG": "1510", "HSG": "1510"}
    stats = {"4020": SimpleNamespace(name="Dịch vụ tài chính", rs_percentile=30.0),
             "1510": SimpleNamespace(name="Nguyên vật liệu", rs_percentile=90.0)}
    out = industry_leaders(cands, [c for c in cands if c.group], stats, of, s)
    assert [d["ticker"] for d in out] == ["HPG", "HSG", "SSI", "VND", "VCK"]
    assert out[0]["grp"] == "A" and out[2]["grp"] is None and out[2]["rank"] == 1


# -- weekly / monthly charts (2026-10-06) ------------------------------------------------------

def test_projection_dates_step_by_the_timeframe():
    from src.quant.channel import future_sessions

    assert future_sessions(pd.Timestamp("2026-10-02"), 3) == ["2026-10-05", "2026-10-06", "2026-10-07"]
    assert future_sessions(pd.Timestamp("2026-09-28"), 2, "W") == ["2026-10-05", "2026-10-12"]
    assert future_sessions(pd.Timestamp("2026-09-30"), 2, "W") == ["2026-10-05", "2026-10-12"]   # mid-week label
    # Each month's first weekday: 01/11/2026 is a Sunday, 01/12 a Tuesday.
    assert future_sessions(pd.Timestamp("2026-10-01"), 2, "M") == ["2026-11-02", "2026-12-01"]


def test_weekly_candles_get_the_same_rules_with_their_own_counts():
    from src.quant import timeframes as tfm
    from src.quant.channel import trend_overlays

    daily = _rising_waves(n=900)                    # ~3.5 years of sessions: ~180 weekly candles
    bars = [{"trade_date": d.date().isoformat(), **{k: float(r[k]) for k in ("open", "high", "low", "close", "volume")}}
            for d, r in daily.iterrows()]
    weeks = tfm.resample(bars, "W")
    w = pd.DataFrame(weeks).set_index(pd.to_datetime([b["trade_date"] for b in weeks]))[["open", "high", "low", "close", "volume"]]
    cfg = TH["chart_levels"]["trendline_by_tf"]["W"]
    out = trend_overlays(w, cfg, "W")
    sup = out["trendlines"]["SUPPORT"]
    (d1, p1), (d2, p2) = sup["anchors"]
    assert pd.Timestamp(d1).weekday() == 0 and pd.Timestamp(d2).weekday() == 0     # weekly candles start on Monday
    i, j = w.index.get_loc(pd.Timestamp(d1)), w.index.get_loc(pd.Timestamp(d2))
    assert j - i >= cfg["min_span_sessions"]
    slope = (p2 - p1) / (j - i)
    line = p1 + slope * (np.arange(i, j + 1) - i)
    assert (w["close"].to_numpy()[i:j + 1] >= line * (1 - cfg["tolerance_pct"] / 100) - 1e-6).all()
    # unbroken: a ray `project_sessions` weeks past the last candle, on a Monday
    last = w.index[-1]
    assert sup["broken_on"] is None and sup["end"][0] == (last + pd.Timedelta(weeks=cfg["project_sessions"])).date().isoformat()
    assert sup["slope_pct_month"] == pytest.approx(slope * cfg["bars_per_month"] / p2 * 100, abs=0.01)


def test_the_nearest_support_is_the_nearest_zone_not_the_strongest():
    """DRI's "nearest" support sat 24% below while swing lows sat 7% below (tester M05)."""
    old = list(np.full(120, 12_800.0) + 300 * np.sin(np.arange(120) / 3))   # a much-tested floor
    climb = list(np.linspace(13_000, 17_000, 60))
    recent = [16_000, 15_600, 15_400, 15_700, 16_200, 16_600, 16_900, 16_500, 16_100, 15_900, 16_300, 16_800,
              17_100, 17_000, 16_900, 16_950, 17_000, 16_900, 16_950, 16_900]
    f = _frame(old + climb + recent, spread=0.01)
    out = chart_levels(f, TH, "HOSE")
    support = next(z for z in out["zones"] if z["side"] == "SUPPORT")
    assert -15 < support["distance_from_price"] <= -1.0, support


def test_zones_sit_on_the_tick_grid_and_away_from_the_price():
    """17,433 and 36,388 are not prices (M06); a resistance 0.03% away is no level (TLG)."""
    from src.quant.screener import round_to_tick
    rng = np.random.default_rng(7)
    f = _frame(20_000 + np.cumsum(rng.normal(0, 150, 300)), spread=0.015)
    out = chart_levels(f, TH, "UPCOM")
    for z in out["zones"]:
        assert abs(z["distance_from_price"]) >= 0.99
        for k in ("center", "zone_low", "zone_high"):
            assert z[k] == round_to_tick(z[k], "UPCOM", up=False)
    assert "lịch giao dịch DNSE" in out["note"]


def test_a_close_through_a_line_within_its_tolerance_is_reported():
    """HDB closed 22,400 over a resistance line at 22,228 without breaking it (tester M07)."""
    out = chart_levels(_rising_waves(), TH)
    for line in out["trendlines"].values():
        if line["broken_on"] is None:
            assert line["close_vs_line_pct"] is not None and line["tolerance_pct"] == CFG["tolerance_pct"]
