import numpy as np
import pandas as pd
import pytest

from src.config import load_config
from src.quant import indicators as ind
from src.quant.candles import latest_pattern
from src.quant.tv_ratings import bucket, ratings

TH = load_config().thresholds
CANDLES = TH["candles"]


def _frame(close, spread=0.01, volume=1e6):
    close = np.asarray(close, dtype="float64")
    idx = pd.bdate_range("2025-01-01", periods=len(close))
    return pd.DataFrame({"open": close, "high": close * (1 + spread), "low": close * (1 - spread),
                         "close": close, "volume": volume}, index=idx)


# ---------------------------------------------------------------- indicators

def test_stoch_is_100_at_the_top_of_the_range_and_0_at_the_bottom():
    f = _frame(np.linspace(100, 200, 40), spread=0.0)
    k, d = ind.stoch(f.high, f.low, f.close)
    assert k.iloc[-1] == pytest.approx(100.0) and d.iloc[-1] == pytest.approx(100.0)
    f = _frame(np.linspace(200, 100, 40), spread=0.0)
    assert ind.stoch(f.high, f.low, f.close)[0].iloc[-1] == pytest.approx(0.0)


def test_awesome_oscillator_is_sma5_minus_sma34_of_the_median():
    f = _frame(np.linspace(100, 160, 60))
    mid = (f.high + f.low) / 2
    assert ind.awesome_oscillator(f.high, f.low).iloc[-1] == pytest.approx(mid.iloc[-5:].mean() - mid.iloc[-34:].mean())


def test_momentum_cci_and_williams_signs():
    up = _frame(np.linspace(100, 160, 60))
    assert ind.momentum(up.close, 10).iloc[-1] == pytest.approx(up.close.iloc[-1] - up.close.iloc[-11])
    assert ind.cci(up.high, up.low, up.close).iloc[-1] > 0
    assert -20 <= ind.williams_r(up.high, up.low, up.close).iloc[-1] <= 0


def test_hull_and_vwma_track_a_straight_line():
    f = _frame(np.linspace(100, 200, 60), spread=0.0)
    assert ind.hull_ma(f.close, 9).iloc[-1] == pytest.approx(f.close.iloc[-1], rel=1e-6)
    assert ind.vwma(f.close, f.volume, 20).iloc[-1] == pytest.approx(f.close.iloc[-20:].mean())


def test_adx_reads_a_steady_trend_as_directional():
    f = _frame(np.linspace(100, 200, 120))
    a, plus_di, minus_di = ind.adx(f.high, f.low, f.close)
    assert a.iloc[-1] > 20 and plus_di.iloc[-1] > minus_di.iloc[-1]


# ---------------------------------------------------------------- ratings

def test_rating_bands():
    cfg = TH["tv_ratings"]
    assert [bucket(x, cfg) for x in (0.8, 0.3, 0.0, -0.3, -0.8)] == ["STRONG_POS", "POS", "NEUTRAL", "NEG", "STRONG_NEG"]
    assert bucket(None, cfg) is None


def test_a_steady_uptrend_rates_its_averages_strongly_positive():
    r = ratings(_frame(np.linspace(100, 300, 260)), TH)
    # Hull MA lies exactly on a straight line, so that one vote is neutral.
    assert r["ma"]["key"] == "STRONG_POS" and r["ma"]["positive"] >= 14 and r["ma"]["negative"] == 0
    assert r["overall"]["key"] in ("POS", "STRONG_POS")
    assert set(r["values"]) == {"rsi", "mom", "ao", "cci", "stoch_k", "stoch_d"}


def test_votes_without_history_are_not_cast():
    r = ratings(_frame(np.linspace(100, 120, 40)), TH)
    names = {v["name"]: v["vote"] for v in r["ma"]["votes"]}
    assert names["SMA 200"] is None and names["SMA 10"] == 1
    assert r["ma"]["positive"] + r["ma"]["negative"] + r["ma"]["neutral"] < 15


def test_labels_are_neutral_wording():
    r = ratings(_frame(np.linspace(300, 100, 260)), TH)
    assert r["ma"]["label"] == "Tiêu cực mạnh" and r["ma"]["tv_word"] == "Bán mạnh"


# ---------------------------------------------------------------- candles

def _with_last(bars, base_trend):
    """A 40-bar series with a prior trend, then the given (o, h, l, c) bars."""
    closes = np.linspace(100, 80, 40) if base_trend == "down" else np.linspace(80, 100, 40)
    f = _frame(closes)
    f["open"] = f["close"].shift(1).fillna(f["close"])
    extra = pd.DataFrame(bars, columns=["open", "high", "low", "close"],
                         index=pd.bdate_range(f.index[-1] + pd.Timedelta(days=1), periods=len(bars)))
    extra["volume"] = 1e6
    return pd.concat([f, extra])


def test_bullish_engulfing_after_a_decline():
    f = _with_last([(80.5, 80.8, 79.0, 79.2), (79.0, 81.5, 78.8, 81.2)], "down")
    assert latest_pattern(f, CANDLES, 7)["key"] == "BULLISH_ENGULFING"


def test_hammer_after_a_decline_and_hanging_man_after_a_rise():
    hammer = (79.6, 79.9, 77.0, 79.8)          # long lower shadow, body at the top
    assert latest_pattern(_with_last([hammer], "down"), CANDLES, 7)["key"] == "HAMMER"
    hanging = (100.2, 100.5, 97.4, 100.4)
    assert latest_pattern(_with_last([hanging], "up"), CANDLES, 7)["key"] == "HANGING_MAN"


def test_doji_and_a_locked_session_is_not_classified():
    assert latest_pattern(_with_last([(90.0, 91.0, 89.0, 90.02)], "up"), CANDLES, 7)["key"].endswith("DOJI")
    locked = (107.0, 107.0, 107.0, 107.0)         # one price all day
    assert latest_pattern(_with_last([locked], "up"), CANDLES, 7) is None


# ---------------------------------------------------------------- TradingView page rules (2026-10-07)

def _vote(r, name):
    return next(v["vote"] for g in ("ma", "osc") for v in r[g]["votes"] if v["name"].startswith(name))


def test_ichimoku_uses_the_four_conditions_on_the_displaced_cloud():
    up, down = _frame(np.linspace(100, 300, 260)), _frame(np.linspace(300, 100, 260))
    assert _vote(ratings(up, TH), "Ichimoku") == 1 and _vote(ratings(down, TH), "Ichimoku") == -1
    # Unshifted, span A always sits between the conversion and base lines: the rule can never fire.
    assert _vote(ratings(up, TH, {"ichimoku_shift": 0}), "Ichimoku") == 0


def test_momentum_rising_versus_sign():
    f = _frame([100] * 30 + list(range(101, 111)) + [110])     # momentum 10 -> 9: positive but falling
    assert _vote(ratings(f, TH), "Momentum") == -1
    assert _vote(ratings(f, TH, {"momentum_mode": "sign"}), "Momentum") == 1


def test_stochastic_by_level_needs_no_cross():
    f = _frame(list(np.linspace(200, 100, 80)) + [100.3, 100.6])
    k, d = ind.stoch(f.high, f.low, f.close)
    assert k.iloc[-1] < 20 and d.iloc[-1] < 20 and k.iloc[-1] > d.iloc[-1] and k.iloc[-2] > d.iloc[-2]   # crossed earlier
    assert _vote(ratings(f, TH), "Stoch %K") == 1
    assert _vote(ratings(f, TH, {"stoch_mode": "cross"}), "Stoch %K") == 0


def test_adx_needs_a_rising_adx_not_a_cross():
    x = np.arange(80)
    up = _frame(list(100 + 3 * np.sin(x / 2.0)) + list(np.linspace(100, 120, 10)))
    a, plus_di, minus_di = ind.adx(up.high, up.low, up.close)
    assert plus_di.iloc[-1] > minus_di.iloc[-1] and a.iloc[-1] > max(20, a.iloc[-2])
    assert _vote(ratings(up, TH), "ADX") == 1
    # A strengthening downtrend (ADX rising) is neutral on TradingView's page: selling needs ADX falling.
    down = _frame(list(100 + 3 * np.sin(x / 2.0)) + list(np.linspace(100, 80, 10)))
    assert _vote(ratings(down, TH), "ADX") == 0


def test_bull_bear_power_reads_bear_power_in_an_uptrend():
    f = _frame(list(np.linspace(100, 200, 120)) + [196, 192, 190, 191])   # pullback under EMA13, recovering
    e = ind.ema(f.close, 13)
    bear = f.low - e
    assert bear.iloc[-1] < 0 and bear.iloc[-1] > bear.iloc[-2] and f.close.iloc[-1] > f.close.iloc[-50:].mean()
    assert _vote(ratings(f, TH), "Bull Bear") == 1
