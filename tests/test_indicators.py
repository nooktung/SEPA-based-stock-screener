"""Indicator maths.

Where an indicator has a definition (Wilder's RSI and ATR, MACD, Bollinger),
the test computes the expected value from that definition with a plain loop and
compares. A vectorised implementation checked against an independent naive one
is the only way to catch a subtly wrong smoothing that still produces
plausible-looking numbers.
"""

import numpy as np
import pandas as pd
import pytest

from src.quant import indicators as ind


def series(values) -> pd.Series:
    index = pd.date_range("2026-01-01", periods=len(values), freq="B")
    return pd.Series([float(v) for v in values], index=index)


def walk(n=120, start=20_000.0, step=120.0, seed=7):
    """Deterministic pseudo-random walk, so failures are reproducible."""
    rng = np.random.default_rng(seed)
    moves = rng.normal(loc=0.0, scale=step, size=n)
    return series(np.abs(start + np.cumsum(moves)))


# -- moving averages ---------------------------------------------------------

def test_sma_is_the_mean_of_the_window():
    assert ind.sma(series([1, 2, 3, 4, 5]), 3).iloc[-1] == pytest.approx(4.0)


def test_sma_is_empty_until_the_window_is_full():
    result = ind.sma(series([1, 2, 3, 4, 5]), 3)
    assert result.iloc[:2].isna().all()
    assert result.notna().sum() == 3


def test_ema_seeds_on_the_first_value_and_tracks_a_constant():
    constant = series([50.0] * 10)
    assert ind.ema(constant, 5).eq(50.0).all()


def test_ema_matches_the_recursive_definition():
    values = [10.0, 12.0, 11.0, 15.0, 14.0]
    span = 3
    alpha = 2.0 / (span + 1)
    expected = values[0]
    for value in values[1:]:
        expected = alpha * value + (1 - alpha) * expected
    assert ind.ema(series(values), span).iloc[-1] == pytest.approx(expected)


# -- Wilder smoothing --------------------------------------------------------

def test_wilder_seeds_with_a_simple_mean_then_recurses():
    values = [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]
    period = 3
    expected = sum(values[:period]) / period          # 2.0
    for value in values[period:]:
        expected = (expected * (period - 1) + value) / period

    result = ind.wilder_average(series(values), period)
    assert result.iloc[period - 1] == pytest.approx(2.0)
    assert result.iloc[-1] == pytest.approx(expected)


def test_wilder_differs_from_an_ema_of_the_same_span():
    """The two are often confused; using an EMA for RSI shifts every reading."""
    prices = walk(60)
    assert ind.wilder_average(prices, 14).iloc[-1] != pytest.approx(
        ind.ema(prices, 14).iloc[-1], rel=1e-6
    )


def test_wilder_is_empty_without_enough_history():
    assert ind.wilder_average(series([1, 2]), 14).isna().all()


# -- RSI ---------------------------------------------------------------------

def naive_rsi(values, period=14):
    """Wilder's RSI written out longhand, straight from the definition."""
    gains, losses = [], []
    for earlier, later in zip(values, values[1:]):
        change = later - earlier
        gains.append(max(change, 0.0))
        losses.append(max(-change, 0.0))

    avg_gain = sum(gains[:period]) / period
    avg_loss = sum(losses[:period]) / period
    for gain, loss in zip(gains[period:], losses[period:]):
        avg_gain = (avg_gain * (period - 1) + gain) / period
        avg_loss = (avg_loss * (period - 1) + loss) / period

    if avg_loss == 0:
        return 100.0
    rs = avg_gain / avg_loss
    return 100.0 - 100.0 / (1.0 + rs)


def test_rsi_matches_a_longhand_wilder_calculation():
    values = list(walk(80).values)
    assert ind.rsi(series(values), 14).iloc[-1] == pytest.approx(naive_rsi(values, 14))


def test_rsi_is_100_when_every_session_rose():
    rising = series(range(100, 140))
    assert ind.rsi(rising, 14).iloc[-1] == pytest.approx(100.0)


def test_rsi_is_zero_when_every_session_fell():
    falling = series(range(140, 100, -1))
    assert ind.rsi(falling, 14).iloc[-1] == pytest.approx(0.0)


def test_rsi_of_a_flat_series_is_undefined_not_fifty():
    # No up pressure and no down pressure is not the same as balanced pressure.
    assert pd.isna(ind.rsi(series([100.0] * 40), 14).iloc[-1])


def test_rsi_stays_inside_its_bounds():
    values = ind.rsi(walk(200), 14).dropna()
    assert values.between(0, 100).all()


def test_rsi_reports_nothing_before_its_period_is_filled():
    result = ind.rsi(walk(20), 14)
    assert result.iloc[:14].isna().all()


# -- ATR ---------------------------------------------------------------------

def test_true_range_uses_the_previous_close_on_a_gap():
    high = series([10.0, 20.0])
    low = series([9.0, 19.0])
    close = series([9.5, 19.5])
    # The second bar's intraday range is only 1.0, but price travelled from
    # yesterday's 9.5 close to today's 20.0 high. True range is that 10.5.
    assert ind.true_range(high, low, close).iloc[-1] == pytest.approx(10.5)


def test_first_true_range_has_no_previous_close():
    high, low, close = series([10.0, 11.0]), series([9.0, 10.0]), series([9.5, 10.5])
    assert ind.true_range(high, low, close).iloc[0] == pytest.approx(1.0)


def test_atr_is_the_wilder_average_of_true_range():
    prices = walk(60)
    high, low, close = prices * 1.01, prices * 0.99, prices
    expected = ind.wilder_average(ind.true_range(high, low, close), 14)
    assert ind.atr(high, low, close, 14).iloc[-1] == pytest.approx(expected.iloc[-1])


def test_atr_of_a_flat_market_is_zero():
    flat = series([100.0] * 30)
    assert ind.atr(flat, flat, flat, 14).iloc[-1] == pytest.approx(0.0)


# -- MACD --------------------------------------------------------------------

def test_macd_of_a_constant_series_is_zero():
    constant = series([100.0] * 60)
    macd_line, signal_line, histogram = ind.macd(constant, 12, 26, 9)
    assert macd_line.iloc[-1] == pytest.approx(0.0)
    assert histogram.iloc[-1] == pytest.approx(0.0)


def test_histogram_is_macd_minus_signal():
    macd_line, signal_line, histogram = ind.macd(walk(120), 12, 26, 9)
    assert histogram.iloc[-1] == pytest.approx(macd_line.iloc[-1] - signal_line.iloc[-1])


def test_macd_is_positive_in_an_uptrend():
    rising = series([100 + i * 2 for i in range(80)])
    assert ind.macd(rising, 12, 26, 9)[0].iloc[-1] > 0


# -- Bollinger ---------------------------------------------------------------

def test_bollinger_uses_population_deviation():
    prices = series(range(1, 21))          # 1..20, mean 10.5
    upper, middle, lower = ind.bollinger(prices, 20, 2.0)
    expected_std = float(np.std(np.arange(1, 21)))   # ddof=0
    assert middle.iloc[-1] == pytest.approx(10.5)
    assert upper.iloc[-1] == pytest.approx(10.5 + 2 * expected_std)
    assert lower.iloc[-1] == pytest.approx(10.5 - 2 * expected_std)


def test_bands_collapse_onto_price_when_nothing_moves():
    flat = series([100.0] * 30)
    upper, middle, lower = ind.bollinger(flat, 20, 2.0)
    assert upper.iloc[-1] == lower.iloc[-1] == pytest.approx(100.0)


def test_percent_b_places_price_within_the_band():
    close = series([15.0])
    assert ind.percent_b(close, series([20.0]), series([10.0])).iloc[-1] == pytest.approx(0.5)


def test_percent_b_is_undefined_on_a_zero_width_band():
    assert pd.isna(ind.percent_b(series([10.0]), series([10.0]), series([10.0])).iloc[-1])


# -- returns, volatility, ranking --------------------------------------------

def test_rate_of_change_is_a_percentage():
    assert ind.rate_of_change(series([100.0, 110.0]), 1).iloc[-1] == pytest.approx(10.0)


def test_realized_volatility_of_a_flat_series_is_zero():
    assert ind.realized_volatility(series([100.0] * 40), 20).iloc[-1] == pytest.approx(0.0)


def test_realized_volatility_grows_with_dispersion():
    calm = series([100 + (i % 2) * 0.5 for i in range(60)])
    wild = series([100 + (i % 2) * 8.0 for i in range(60)])
    assert ind.realized_volatility(wild, 20).iloc[-1] > ind.realized_volatility(calm, 20).iloc[-1]


def test_rolling_percentile_ranks_within_the_window():
    rising = series(range(1, 31))
    assert ind.rolling_percentile(rising, 20).iloc[-1] == pytest.approx(100.0)


# -- comparisons -------------------------------------------------------------

def test_relative_strength_is_excess_return_in_points():
    ticker = series([100.0, 110.0])        # +10%
    benchmark = series([100.0, 104.0])     # +4%
    assert ind.relative_strength(ticker, benchmark, 1).iloc[-1] == pytest.approx(6.0)


def test_relative_strength_is_missing_where_the_benchmark_is():
    ticker = series([100.0, 110.0])
    benchmark = pd.Series([100.0, np.nan], index=ticker.index)
    assert pd.isna(ind.relative_strength(ticker, benchmark, 1).iloc[-1])


def test_slope_measures_the_line_not_the_price():
    rising_line = series([100.0, 102.0, 104.0, 106.0])
    assert ind.slope_pct(rising_line, 3).iloc[-1] == pytest.approx(6.0)


def test_rolling_high_can_exclude_today():
    prices = series([10.0, 12.0, 11.0, 15.0])
    assert ind.rolling_high(prices, 3).iloc[-1] == pytest.approx(15.0)
    # A breakout test must compare against a range that does not contain today.
    assert ind.rolling_high(prices, 3, exclude_current=True).iloc[-1] == pytest.approx(12.0)


def test_rolling_low_can_exclude_today():
    prices = series([10.0, 12.0, 11.0, 8.0])
    assert ind.rolling_low(prices, 3, exclude_current=True).iloc[-1] == pytest.approx(10.0)


def test_gap_is_measured_against_the_previous_close():
    open_ = series([0.0, 105.0])
    close = series([100.0, 106.0])
    assert ind.gap_pct(open_, close).iloc[-1] == pytest.approx(5.0)


def test_distance_pct_signs_correctly():
    assert ind.distance_pct(series([110.0]), series([100.0])).iloc[-1] == pytest.approx(10.0)
    assert ind.distance_pct(series([90.0]), series([100.0])).iloc[-1] == pytest.approx(-10.0)
