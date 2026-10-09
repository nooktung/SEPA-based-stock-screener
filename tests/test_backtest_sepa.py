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
