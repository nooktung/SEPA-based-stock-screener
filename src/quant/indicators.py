"""Technical indicators. Deterministic, unit-tested, no model involved.

Every function here takes a series and returns a series of the same length,
NaN-padded at the front where there is not yet enough history. Nothing is
back-filled or seeded from a shorter window: an RSI that claims a value on the
third bar of a 14-period calculation is a fabricated number, and downstream it
looks exactly like a real one.

Two smoothing conventions appear below and they are not interchangeable:

* **Wilder smoothing** (RSI, ATR) seeds with a simple mean of the first
  `period` values, then carries `avg = (avg*(period-1) + new) / period`. This
  is what Wilder defined and what every charting package shows. An EMA with
  span=period gives visibly different numbers.
* **Standard EMA** (MACD) uses `alpha = 2/(span+1)` seeded on the first value,
  which is what MACD was defined with.

Implemented against `adj_close` by preference. On an unadjusted series a bonus
issue puts a step in the input, and every average, band and momentum reading
inherits it.
"""

from __future__ import annotations

import numpy as np
import pandas as pd


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(window=period, min_periods=period).mean()


def ema(series: pd.Series, span: int) -> pd.Series:
    """Standard EMA, alpha = 2/(span+1), seeded on the first observation."""
    return series.ewm(span=span, adjust=False).mean()


def wilder_average(series: pd.Series, period: int) -> pd.Series:
    """Wilder's smoothing. Simple mean of the first `period`, then recursive.

    Written as an explicit recursion rather than an `ewm` call: the seeding
    differs from any ewm variant, and the loop is the definition rather than an
    approximation of it. A few hundred bars per ticker makes the cost
    irrelevant next to being provably right.
    """
    out = pd.Series(np.nan, index=series.index, dtype="float64")
    clean = series.dropna()
    if len(clean) < period:
        return out

    average = float(clean.iloc[:period].mean())
    # Keep the identical recursive arithmetic; assign the result only once.
    # Repeated .loc writes dominated runtime when exporting all chart histories.
    smoothed = np.empty(len(clean) - period + 1, dtype="float64")
    smoothed[0] = average
    for offset, value in enumerate(clean.iloc[period:].to_numpy(), 1):
        average = (average * (period - 1) + float(value)) / period
        smoothed[offset] = average
    out.loc[clean.index[period - 1:]] = smoothed
    return out


def rsi(close: pd.Series, period: int = 14) -> pd.Series:
    """Wilder's RSI. 100 when every move was up, 0 when every move was down."""
    delta = close.diff()
    gains = delta.clip(lower=0.0)
    losses = (-delta).clip(lower=0.0)

    avg_gain = wilder_average(gains, period)
    avg_loss = wilder_average(losses, period)

    out = pd.Series(np.nan, index=close.index, dtype="float64")
    both_flat = (avg_gain == 0) & (avg_loss == 0)
    only_gains = (avg_loss == 0) & (avg_gain > 0)
    normal = avg_loss > 0

    rs = avg_gain[normal] / avg_loss[normal]
    out.loc[normal] = 100.0 - (100.0 / (1.0 + rs))
    out.loc[only_gains] = 100.0
    # A perfectly flat window has no up or down pressure at all. That is
    # undefined, not neutral, so it stays NaN rather than becoming 50.
    out.loc[both_flat] = np.nan
    return out


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    """Greatest of today's range, and each extreme against yesterday's close.

    The previous close matters: on a gap the intraday range alone understates
    the move that actually happened.
    """
    previous_close = close.shift(1)
    spans = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()],
        axis=1,
    )
    return spans.max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    return wilder_average(true_range(high, low, close), period)


def macd(
    close: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd line, signal line, histogram)."""
    macd_line = ema(close, fast) - ema(close, slow)
    signal_line = ema(macd_line, signal)
    return macd_line, signal_line, macd_line - signal_line


def bollinger(
    close: pd.Series, period: int = 20, std_multiple: float = 2.0
) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (upper, middle, lower).

    Population standard deviation (ddof=0), which is what Bollinger specified
    and what charting packages draw. The sample deviation would widen every
    band slightly and make historical percentiles incomparable.
    """
    middle = sma(close, period)
    deviation = close.rolling(window=period, min_periods=period).std(ddof=0)
    return middle + std_multiple * deviation, middle, middle - std_multiple * deviation


def percent_b(close: pd.Series, upper: pd.Series, lower: pd.Series) -> pd.Series:
    """Where price sits in the band: 0 at the lower, 1 at the upper."""
    width = upper - lower
    return ((close - lower) / width).where(width > 0)


def rate_of_change(series: pd.Series, periods: int) -> pd.Series:
    """Percentage change over `periods` sessions.

    `fill_method=None` is load-bearing. pandas otherwise forward-fills gaps
    before differencing, which would silently measure a move against a stale
    price -- a missing session would produce a real-looking return of zero
    instead of an honest NaN.
    """
    return series.pct_change(periods=periods, fill_method=None) * 100.0


def realized_volatility(close: pd.Series, period: int, annualise: bool = True) -> pd.Series:
    """Standard deviation of daily log returns, in percent.

    Annualised over 250 sessions -- the Vietnamese exchanges trade roughly that
    many days a year once holidays are removed.
    """
    log_returns = np.log(close / close.shift(1))
    daily = log_returns.rolling(window=period, min_periods=period).std(ddof=0)
    return daily * (np.sqrt(250.0) if annualise else 1.0) * 100.0


def rolling_percentile(series: pd.Series, window: int) -> pd.Series:
    """Percentile rank of the latest value within its trailing window."""
    return series.rolling(window=window, min_periods=window).rank(pct=True) * 100.0


def relative_strength(close: pd.Series, benchmark: pd.Series, periods: int) -> pd.Series:
    """Excess return over the benchmark, in percentage points.

    Subtracted rather than divided so the number reads directly as "beat the
    index by 4.2 points over 20 sessions". Both series must already be aligned
    on trading date; sessions the benchmark is missing produce NaN rather than
    a comparison against a stale index level.
    """
    return rate_of_change(close, periods) - rate_of_change(benchmark, periods)


def slope_pct(series: pd.Series, periods: int) -> pd.Series:
    """Change in a series over `periods`, as a percentage of its earlier value.

    Applied to a moving average this answers "is the trend line itself rising",
    which is a different question from "is price above it".
    """
    earlier = series.shift(periods)
    return ((series - earlier) / earlier.abs() * 100.0).where(earlier != 0)


def rolling_high(series: pd.Series, window: int, exclude_current: bool = False) -> pd.Series:
    """Highest value in the trailing window.

    `exclude_current` shifts the window back one session, which is what a
    breakout test needs: comparing today's close against a range that already
    includes today would make every new high trivially a breakout.
    """
    source = series.shift(1) if exclude_current else series
    return source.rolling(window=window, min_periods=window).max()


def rolling_low(series: pd.Series, window: int, exclude_current: bool = False) -> pd.Series:
    source = series.shift(1) if exclude_current else series
    return source.rolling(window=window, min_periods=window).min()


def gap_pct(open_: pd.Series, close: pd.Series) -> pd.Series:
    """Opening gap against the previous close, in percent."""
    previous_close = close.shift(1)
    return ((open_ - previous_close) / previous_close * 100.0).where(previous_close > 0)


def distance_pct(series: pd.Series, reference: pd.Series) -> pd.Series:
    """How far `series` sits above (+) or below (-) `reference`, in percent."""
    return ((series - reference) / reference * 100.0).where(reference != 0)


# ---------------------------------------------------------------------------
# Oscillators and averages for the TradingView-style technical ratings
# (src/quant/tv_ratings.py). Formulas follow TradingView's published defaults.
# ---------------------------------------------------------------------------

def wma(series: pd.Series, period: int) -> pd.Series:
    """Linearly weighted moving average (newest bar weighs `period`)."""
    weights = np.arange(1, period + 1, dtype="float64")
    return series.rolling(period, min_periods=period).apply(lambda x: float(np.dot(x, weights) / weights.sum()), raw=True)


def hull_ma(series: pd.Series, period: int = 9) -> pd.Series:
    """HMA = WMA(2*WMA(n/2) - WMA(n), sqrt(n))."""
    half, root = max(int(period / 2), 1), max(int(np.sqrt(period)), 1)
    return wma(2 * wma(series, half) - wma(series, period), root)


def vwma(close: pd.Series, volume: pd.Series, period: int = 20) -> pd.Series:
    return sma(close * volume, period) / sma(volume, period)


def donchian_mid(high: pd.Series, low: pd.Series, period: int) -> pd.Series:
    """(highest high + lowest low) / 2 -- Ichimoku's conversion (9) and base (26) lines."""
    return (high.rolling(period, min_periods=period).max() + low.rolling(period, min_periods=period).min()) / 2


def stoch(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14,
          smooth_k: int = 3, smooth_d: int = 3) -> tuple[pd.Series, pd.Series]:
    """Slow stochastic (%K, %D): raw %K smoothed by `smooth_k`, %D = SMA(%K, smooth_d)."""
    hh = high.rolling(period, min_periods=period).max()
    ll = low.rolling(period, min_periods=period).min()
    raw = 100 * (close - ll) / (hh - ll).replace(0, np.nan)
    k = sma(raw, smooth_k)
    return k, sma(k, smooth_d)


def cci(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 20) -> pd.Series:
    """Commodity Channel Index on the typical price (H+L+C)/3."""
    tp = (high + low + close) / 3
    mean = sma(tp, period)
    mad = tp.rolling(period, min_periods=period).apply(lambda x: float(np.mean(np.abs(x - x.mean()))), raw=True)
    return (tp - mean) / (0.015 * mad.replace(0, np.nan))


def awesome_oscillator(high: pd.Series, low: pd.Series) -> pd.Series:
    """SMA5 - SMA34 of the median price (H+L)/2."""
    mid = (high + low) / 2
    return sma(mid, 5) - sma(mid, 34)


def momentum(close: pd.Series, period: int = 10) -> pd.Series:
    return close - close.shift(period)


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> tuple[pd.Series, pd.Series, pd.Series]:
    """Wilder's ADX with +DI and -DI."""
    up, down = high.diff(), -low.diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=high.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=high.index)
    tr = wilder_average(true_range(high, low, close).iloc[1:], period).reindex(high.index)
    plus_di = 100 * wilder_average(plus_dm.iloc[1:], period).reindex(high.index) / tr
    minus_di = 100 * wilder_average(minus_dm.iloc[1:], period).reindex(high.index) / tr
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    return wilder_average(dx, period), plus_di, minus_di


def williams_r(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    hh = high.rolling(period, min_periods=period).max()
    ll = low.rolling(period, min_periods=period).min()
    return -100 * (hh - close) / (hh - ll).replace(0, np.nan)


def stoch_rsi(close: pd.Series, smooth_k: int = 3, smooth_d: int = 3, rsi_period: int = 14,
              stoch_period: int = 14) -> tuple[pd.Series, pd.Series]:
    r = rsi(close, rsi_period)
    return stoch(r, r, r, stoch_period, smooth_k, smooth_d)


def bull_bear_power(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 13) -> pd.Series:
    """Elder's bull power + bear power: (H - EMA) + (L - EMA)."""
    e = ema(close, period)
    return (high - e) + (low - e)


def ultimate_oscillator(high: pd.Series, low: pd.Series, close: pd.Series,
                        fast: int = 7, mid: int = 14, slow: int = 28) -> pd.Series:
    prev = close.shift(1)
    bp = close - pd.concat([low, prev], axis=1).min(axis=1)
    tr = pd.concat([high, prev], axis=1).max(axis=1) - pd.concat([low, prev], axis=1).min(axis=1)

    def avg(n: int) -> pd.Series:
        return bp.rolling(n, min_periods=n).sum() / tr.rolling(n, min_periods=n).sum().replace(0, np.nan)

    return 100 * (4 * avg(fast) + 2 * avg(mid) + avg(slow)) / 7
