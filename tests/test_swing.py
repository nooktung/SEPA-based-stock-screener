"""Short-term setup backtest: setups, Vietnam's trading rules, and the evidence gate."""
from datetime import date, timedelta

import numpy as np
import pandas as pd
import pytest

from src.backtest.swing import (
    BASELINE,
    Bars,
    contaminated,
    evaluate,
    fixed_exits,
    setup_flags,
    stop_target_exit,
    wilder_rsi,
)
from src.config import load_config
from src.quant import indicators as ind

CFG = load_config().thresholds["swing"]


def frame(closes, opens=None, highs=None, lows=None, volumes=None):
    c = np.asarray(closes, dtype="float64")
    o = np.asarray(opens if opens is not None else c, dtype="float64")
    h = np.asarray(highs if highs is not None else np.maximum(o, c) * 1.01, dtype="float64")
    lo = np.asarray(lows if lows is not None else np.minimum(o, c) * 0.99, dtype="float64")
    v = np.asarray(volumes if volumes is not None else [1e6] * len(c), dtype="float64")
    idx = pd.bdate_range("2020-01-01", periods=len(c))
    return pd.DataFrame({"open": o, "high": h, "low": lo, "close": c, "volume": v}, index=idx)


def test_fast_rsi_matches_the_project_indicator():
    rng = np.random.default_rng(3)
    c = pd.Series(100 * np.cumprod(1 + rng.normal(0, 0.02, 300)))
    for period in (2, 14):
        a, b = wilder_rsi(c.to_numpy(), period), ind.rsi(c, period).to_numpy()
        assert np.allclose(a[~np.isnan(b)], b[~np.isnan(b)]) and np.isnan(a).sum() == np.isnan(b).sum()


def test_a_dip_in_an_uptrend_and_an_oversold_red_day_are_found():
    up = list(np.linspace(100, 160, 260))
    dip = up + [159, 158.5, 158]                               # three lower closes, near MA20, above MA50
    f = setup_flags(frame(dip), 7.0, CFG)
    assert f["pullback_uptrend:3DOWN"][-1] and f["pullback_uptrend:RSI2"][-1]
    assert not f["oversold_bounce:RED"][-1]                    # -4% in 3 sessions is not a 10% fall
    crash = up + [150, 140, 132, 125, 120]                     # -25% in 5 sessions, red again today
    g = setup_flags(frame(crash), 7.0, CFG)
    assert g["oversold_bounce:RED"][-1] and not g["oversold_bounce:REVERSAL"][-1]


def test_a_ceiling_close_and_a_volume_surge_are_told_apart():
    base = list(np.linspace(100, 110, 250))
    c = base + [110 * 1.069]                                   # +6.9% on HOSE (band 7%): the ceiling
    f = setup_flags(frame(c, highs=list(np.array(c) * 1.01)[:-1] + [c[-1]],
                          volumes=[1e6] * 250 + [3e6]), 7.0, CFG)
    assert f["momentum:CEILING"][-1] and not f["momentum:SURGE"][-1]
    s = base + [110 * 1.055]                                   # +5.5% on 3x volume, below the ceiling
    g = setup_flags(frame(s, volumes=[1e6] * 250 + [3e6]), 7.0, CFG)
    assert g["momentum:SURGE"][-1] and not g["momentum:CEILING"][-1]


def test_fixed_exits_count_sessions_after_the_buy_and_skip_a_floor_lock():
    c = [100, 100, 100, 100, 93, 95, 96, 97]                   # session 4 locked at the floor
    o = [100, 100, 100, 100, 93, 95, 96, 97]
    f = frame(c, opens=o, highs=[101, 101, 101, 101, 93, 96, 97, 98], lows=[99, 99, 99, 99, 93, 94, 95, 96])
    bars = Bars.of(f, 7.0, 0.6)
    assert not bars.sellable[4]
    out = fixed_exits(bars, 1, [3, 5])                         # bought at the open of session 1
    assert out["T+3"] == pytest.approx((95 / 100 - 1) * 100)   # T+3 = session 4 is locked -> session 5
    assert out["T+5"] == pytest.approx((96 / 100 - 1) * 100)


def test_a_stop_hit_before_t2_is_only_sold_on_the_afternoon_of_t2():
    # buy at 100 on session 1; session 2 trades down to 90 (stop 95), session 3 closes at 97
    f = frame([100, 99, 92, 97, 98, 99], opens=[100, 100, 98, 93, 97, 98],
              highs=[101, 101, 99, 98, 99, 100], lows=[99, 98, 90, 92, 96, 97])
    bars = Bars.of(f, 7.0, 0.6)
    r = stop_target_exit(bars, 1, stop_pct=5, target_pct=8, max_hold=4, first_sell=2)
    assert r == pytest.approx((97 / 100 - 1) * 100)            # session 3 = B+2: sold at its close


def test_a_target_after_t2_fills_at_the_target_and_a_gap_at_the_open():
    f = frame([100, 101, 102, 103, 109, 120], opens=[100, 100, 101, 102, 104, 115],
              highs=[101, 102, 103, 104, 110, 121], lows=[99, 99, 100, 101, 103, 114])
    bars = Bars.of(f, 7.0, 0.6)
    assert stop_target_exit(bars, 1, 5, 8, 6, 2) == pytest.approx(8.0)       # session 4 high 110 >= 108
    g = frame([100, 101, 102, 103, 115], opens=[100, 100, 101, 102, 112], highs=[101, 102, 103, 104, 116],
              lows=[99, 99, 100, 101, 111])
    assert stop_target_exit(Bars.of(g, 7.0, 0.6), 1, 5, 8, 6, 2) == pytest.approx(12.0)   # gap to 112


def test_an_entry_at_a_ceiling_open_is_flagged():
    f = frame([100, 100, 106.9], opens=[100, 100, 106.9])
    assert Bars.of(f, 7.0, 0.6).ceiling_open.tolist() == [False, False, True]


def test_windows_around_an_impossible_move_are_left_out():
    c = np.array([100.0] * 50 + [50.0] + [50.0] * 50)          # -50% on HOSE: an unadjusted split
    bad = contaminated(c, "HOSE", before=30, after=15)
    assert bad[50] and bad[35] and bad[80] and not bad[34] and not bad[81]


def records(pattern_edge: float, n: int = 600, seed: int = 1) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    days = [date(2016, 1, 4) + timedelta(days=k) for k in range(n)]
    rows = []
    for d in days:
        for _ in range(3):
            rows.append((BASELINE, "SAMPLED", "HIGH", d, "UPTREND", *rng.normal(-0.5, 4, 4)))
        rows.append(("momentum", "CEILING", "HIGH", d, "UPTREND", *rng.normal(-0.5 + pattern_edge, 4, 4)))
    return pd.DataFrame.from_records(rows, columns=["pattern", "variant", "tier", "date", "regime",
                                                    "T+3", "T+5", "T+10", "SL/TP"])


def test_only_a_clear_and_lasting_edge_clears_the_gate():
    strong = evaluate(records(pattern_edge=2.0), CFG)[0][0]
    assert strong["passes"] and "T+5" in strong["passing_exits"]
    weak = evaluate(records(pattern_edge=0.2), CFG)[0][0]
    assert not weak["passes"]


def test_a_passing_setup_reports_its_median_and_years_not_just_its_mean():
    strong = evaluate(records(pattern_edge=2.0), CFG)[0][0]
    rb = strong["robustness"]
    assert rb["T+5"]["median"] == pytest.approx(1.5, abs=0.6) and rb["T+5"]["baseline_median"] < 0
    assert rb["T+5"]["stress_mean"] < rb["T+5"]["mean"]        # harsher slippage costs something
    assert set(rb["by_year_t5"]) == {2016, 2017}               # 600 days from 2016-01-04
