import numpy as np
import pandas as pd
import pytest

from src.backtest.sepa import build_panel, generate_sepa_signals, regime_by_date
from src.config import load_config
from src.quant.screener import screen_settings

S = screen_settings()
MON = dict(load_config().thresholds.raw["monitor"])


def _frame(closes, volume=5_000_000, end="2026-09-25"):
    idx = pd.bdate_range(end=end, periods=len(closes))
    c = np.asarray(closes, dtype="float64")
    return pd.DataFrame({"open": c, "high": c * 1.004, "low": c * 0.996, "close": c,
                         "volume": np.full(len(c), float(volume))}, index=idx)


def _universe(n=12, bars=420):
    rng = np.random.default_rng(3)
    frames = {}
    for i in range(n):
        drift = (i - n / 2) * 0.0008                   # half trend up, half down
        noise = rng.normal(0, 0.012, bars)
        closes = 20_000 * np.exp(np.cumsum(drift + noise))
        frames[f"T{i:02d}"] = _frame(closes)
    # one clean leader: flat year, then a steady advance -> should enter 8/8
    frames["LDR"] = _frame(list(np.full(260, 20_000.0)) + list(np.linspace(20_000, 36_000, bars - 260)))
    return frames, {t: "HOSE" for t in frames}


def test_panel_liquidity_and_rs_are_per_day():
    frames, ex = _universe()
    panel = build_panel(frames, ex, S)
    assert panel.liquid.iloc[-1].all()                  # 5M shares a day, long history
    assert not panel.liquid.iloc[100].any()             # < 252 sessions of history
    assert panel.rs.iloc[-1].between(1, 99).all()


def test_a_clean_leader_produces_a_trend_template_entry_with_its_tier():
    frames, ex = _universe()
    signals = generate_sepa_signals(build_panel(frames, ex, S), S, MON, step=5)
    entries = [s for s in signals if s.pattern == "tt_entry" and s.ticker == "LDR"]
    assert entries and entries[0].status in {"STRONG_LEADER", "LEADER", "QUALIFIED"}
    assert any(s.pattern == "baseline_liquid" for s in signals)


def test_signals_do_not_change_when_future_bars_are_added():
    """The look-ahead rule: a signal on day d must be the same with or without later data."""
    frames, ex = _universe()
    cut = frames["LDR"].index[-40]
    truncated = {t: f.loc[:cut] for t, f in frames.items()}

    def keyset(fr):
        sigs = generate_sepa_signals(build_panel(fr, ex, S), S, MON, step=5)
        return {(s.ticker, s.pattern, s.status, s.signal_date) for s in sigs
                if pd.Timestamp(s.signal_date) <= cut}

    assert keyset(truncated) == keyset(frames)


def test_regime_series_matches_the_screen_definition():
    idx = _frame(list(np.linspace(1000, 1400, 260)))
    r = regime_by_date(idx, S)
    assert r.iloc[-1] == "UPTREND" and r.iloc[:199].isna().all()
    down = _frame(list(np.linspace(1400, 1000, 260)))
    assert regime_by_date(down, S).iloc[-1] == "CORRECTION"


# -- the stop exit (2026-10-11) -------------------------------------------------------

from src.backtest.engine import BacktestConfig, Signal  # noqa: E402
from src.backtest.sepa import evaluate_with_stop, stop_exits  # noqa: E402
from src.backtest.swing import Bars  # noqa: E402


def _bars(opens, closes, lows=None, band=7.0):
    o, c = np.asarray(opens, dtype="float64"), np.asarray(closes, dtype="float64")
    lo = np.asarray(lows, dtype="float64") if lows is not None else np.minimum(o, c) * 0.995
    hi = np.maximum(o, c) * 1.005
    f = pd.DataFrame({"open": o, "high": hi, "low": lo, "close": c, "volume": 1e6},
                     index=pd.bdate_range(end="2026-09-25", periods=len(c)))
    return f, Bars.of(f, band, 0.6)


def test_a_close_at_the_stop_sells_at_the_next_open():
    #          buy   +1     +2     +3 (close 91 < stop 92)  +4 open 90
    _, bars = _bars([100, 101, 100, 99, 90, 95, 96], [101, 100, 99, 91, 94, 96, 97])
    out = stop_exits(bars, 0, 92.0, (5,), 2)
    assert out[5] == (pytest.approx(-10.0), True)                  # out at the open after the stop close


def test_a_stop_before_t_plus_2_sells_at_the_first_sellable_close():
    """Shares bought on B arrive on the afternoon of B+2: only that close is reachable."""
    _, bars = _bars([100, 95, 93, 94, 95], [90, 93, 94, 95, 96])     # the buy day itself closes under 92
    assert stop_exits(bars, 0, 92.0, (3,), 2)[3] == (pytest.approx(-6.0), True)


def test_without_a_stop_the_trade_runs_to_the_horizon():
    _, bars = _bars([100, 101, 102, 103, 104, 105], [101, 102, 103, 104, 105, 106])
    assert stop_exits(bars, 0, 92.0, (3, 5), 2) == {3: (pytest.approx(4.0), False), 5: (pytest.approx(6.0), False)}


def test_a_floor_locked_session_cannot_be_sold():
    #  close 91 under the stop, then a session locked at the floor (high = low, -7%): sold the session after
    closes = [101, 100, 91, 84.63, 86, 87]
    opens = [100, 101, 99, 84.63, 85, 86]
    lows = [99, 99, 90, 84.63, 84, 85]
    f, _ = _bars(opens, closes, lows)
    bars = Bars.of(f.assign(high=np.where(np.arange(len(f)) == 3, f["low"], f["high"])), 7.0, 0.6)
    assert stop_exits(bars, 0, 92.0, (5,), 2)[5] == (pytest.approx(-15.0), True)


def test_the_stop_exit_uses_the_breakouts_base_stop_or_the_8_percent_rule():
    f, bars = _bars([100] * 3 + [100, 97, 96, 95, 95, 95], [100] * 3 + [98, 96, 95, 95, 95, 95])
    cfg = BacktestConfig((5,), "next_open", "close", 0.15, 0.10, 0.10, 30, 200, 0.3)
    day = f.index[2].date()
    base_stop = Signal("AAA", "breakout_VCP", "VALID", day, None, {"stop": 97.5})
    o, stopped = evaluate_with_stop(base_stop, f, bars, cfg, 8.0, 2)
    assert stopped[5] and o.returns[5] < 0                          # 96 closes under the base stop 97.5
    plain = Signal("AAA", "tt_entry", "LEADER", day, None, {})
    o, stopped = evaluate_with_stop(plain, f, bars, cfg, 8.0, 2)
    assert not stopped[5]                                             # 95 is above 100 - 8%
