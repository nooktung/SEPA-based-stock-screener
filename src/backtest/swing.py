"""Short-term setups (held two weeks at most) on Vietnamese data.

Question answered: when a stock showed one of these setups at a close, bought
at the next open and sold under Vietnam's rules, did it do better than an
ordinary stock of the same liquidity on the same days -- in the sample, and
again in the newest 30% of the history it was not tuned on?

The user asked (2026-10-09) for stocks suited to trading T+ within two weeks,
including ones red today that bounce or hit the ceiling later. Nothing is
shown on the dashboard from here: this module only measures. A setup reaches
the daily list (phase 2) only after it clears the evidence gate below and the
user has read the report. Earlier evidence: a random stock held 1-5 sessions
loses money after costs (docs/PATTERNS.md), so the bar is real.

Setups ([TRIỂN KHAI]; every threshold in `swing` in config/thresholds.yaml):

    pullback_uptrend   RSI2      close > MA50 > MA200 and RSI(2) below `rsi2_max`
                       3DOWN     the same trend, `down_days` lower closes in a row, low within
                                 `near_ma_pct` of MA20
    oversold_bounce    RED       fell `drop_pct` in `drop_sessions`, RSI(14) below `rsi14_max`,
                                 and closed down again today ("red today")
                       REVERSAL  the same fall, today closed in the top part of its range
    momentum           CEILING   closed at the (estimated) ceiling, close = high ("tím")
                       SURGE     up >= `surge_pct` on volume >= `surge_volume_ratio` x the
                                 20-session average, not at the ceiling
                       HIGH20    closed above the previous 20 sessions' high on volume
    tight_base         NR7       in the uptrend above, today's range the narrowest of 7
                       BREAKOUT  closed above a `base_sessions` range no wider than
                                 `base_max_width_pct`, on volume

Vietnam's rules, as far as daily bars allow (approximations are flagged):

* entry at the next session's open; a signal whose next session opens at the
  estimated ceiling is skipped (a locked ceiling rarely fills) and counted;
* shares bought on day B can be sold from the afternoon of B+2, so the fixed
  exits are the close of B+3 / B+5 / B+10, and the stop / target exit starts
  checking on B+2 (on B+2 itself only the close is known to be reachable);
* a session locked at the floor (high = low, down the full band) cannot be
  sold: the exit moves to the next session that can be;
* costs: brokerage both ways, the sell tax, and slippage by liquidity tier
  (thin stocks pay more), deducted from every return;
* windows touching an impossible one-session move (an unadjusted corporate
  action, src/collectors/backfill.close_to_close_bounds) are left out.

No liquidity floor (user's choice): every result is split by the screen's own
liquidity tiers plus "below 3 bn", and compared with the baseline of the same
tier. Limits: today's listings only (survivorship flatters every number), no
cash dividends in DNSE prices, ceiling / floor estimated from the band and the
previous close (UPCoM's reference is the previous average, unknown here), and
signals cluster in time and across stocks, so the t-statistics overstate the
evidence.
"""

from __future__ import annotations

import json
import logging
import math
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from src.database import connect

log = logging.getLogger(__name__)

TIERS = ("HIGH", "GOOD", "LOW", "BELOW")
TIER_TEXT = {"HIGH": "Cao (≥ 10 tỷ)", "GOOD": "Khá (8–10 tỷ)", "LOW": "Thấp (3–8 tỷ)", "BELOW": "Dưới 3 tỷ"}
EXITS = ("T+3", "T+5", "T+10", "SL/TP")
BASELINE = "baseline"
SETUP_TEXT = {
    "pullback_uptrend": "Điều chỉnh trong xu hướng tăng",
    "oversold_bounce": "Bật từ quá bán",
    "momentum": "Đà tăng",
    "tight_base": "Nền ngắn",
    BASELINE: "Đối chứng (mã bất kỳ cùng hạng)",
}


# ---------------------------------------------------------------------------
# Indicators (numpy; the same definitions as src/quant/indicators.py)
# ---------------------------------------------------------------------------

def wilder_rsi(close: np.ndarray, period: int) -> np.ndarray:
    """Wilder's RSI, seeded like src.quant.indicators.rsi, as a plain loop over floats.

    indicators.rsi assigns through pandas .loc per bar, which is too slow for
    ~1,500 tickers x 11 years; tests check the two agree.
    """
    n = len(close)
    out = np.full(n, np.nan)
    if n <= period:
        return out
    delta = np.diff(close)
    gains, losses = np.clip(delta, 0, None), np.clip(-delta, 0, None)
    avg_g, avg_l = gains[:period].mean(), losses[:period].mean()

    def value(g: float, l: float) -> float:
        if l > 0:
            return 100.0 - 100.0 / (1.0 + g / l)
        return 100.0 if g > 0 else np.nan

    out[period] = value(avg_g, avg_l)
    for k in range(period, len(delta)):
        avg_g = (avg_g * (period - 1) + gains[k]) / period
        avg_l = (avg_l * (period - 1) + losses[k]) / period
        out[k + 1] = value(avg_g, avg_l)
    return out


def _sma(x: np.ndarray, n: int) -> np.ndarray:
    return pd.Series(x).rolling(n, min_periods=n).mean().to_numpy()


def _shift(x: np.ndarray, k: int) -> np.ndarray:
    out = np.full(len(x), np.nan)
    if k < len(x):
        out[k:] = x[:-k] if k else x
    return out


# ---------------------------------------------------------------------------
# Setups -- one function for the backtest and the daily list
# ---------------------------------------------------------------------------

def ceiling_close(close: np.ndarray, high: np.ndarray, band_pct: float, tolerance_pct: float) -> np.ndarray:
    """Closed at the estimated ceiling: up the band (less the tick rounding allowance) and at the high."""
    prev = _shift(close, 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        up = close / prev - 1.0
    return (up >= (band_pct - tolerance_pct) / 100.0) & (close >= high * 0.999)


def setup_flags(frame: pd.DataFrame, band_pct: float, cfg: Mapping[str, Any]) -> dict[str, np.ndarray]:
    """{"pattern:VARIANT": bool array}, each True on the session the setup is complete (known at its close)."""
    s = cfg["setups"]
    o, h, l, c, v = (frame[k].to_numpy(dtype="float64") for k in ("open", "high", "low", "close", "volume"))
    ma20, ma50, ma200 = _sma(c, 20), _sma(c, 50), _sma(c, 200)
    vol20 = _shift(_sma(v, 20), 1)                              # the 20 sessions before today
    rng = h - l
    with np.errstate(invalid="ignore", divide="ignore"):
        close_pos = np.where(rng > 0, (c - l) / rng, 1.0)
        change = c / _shift(c, 1) - 1.0
        vol_ratio = np.where(vol20 > 0, v / vol20, np.nan)
    down = np.nan_to_num(change) < 0
    uptrend = (c > ma50) & (ma50 > ma200)
    flags: dict[str, np.ndarray] = {}

    p = s["pullback_uptrend"]
    rsi2 = wilder_rsi(c, 2)
    streak = pd.Series(down).groupby((~pd.Series(down)).cumsum()).cumsum().to_numpy()
    flags["pullback_uptrend:RSI2"] = uptrend & (rsi2 < float(p["rsi2_max"]))
    flags["pullback_uptrend:3DOWN"] = (uptrend & (streak >= int(p["down_days"]))
                                       & (l <= ma20 * (1 + float(p["near_ma_pct"]) / 100.0)))

    b = s["oversold_bounce"]
    rsi14 = wilder_rsi(c, 14)
    n_drop = int(b["drop_sessions"])
    with np.errstate(invalid="ignore", divide="ignore"):
        fell = c / _shift(c, n_drop) - 1.0 <= -float(b["drop_pct"]) / 100.0
    oversold = fell & (rsi14 < float(b["rsi14_max"]))
    flags["oversold_bounce:RED"] = oversold & down
    flags["oversold_bounce:REVERSAL"] = oversold & (close_pos >= float(b["reversal_close_position"]))

    m = s["momentum"]
    at_ceiling = ceiling_close(c, h, band_pct, float(cfg["ceiling_tolerance_pct"]))
    prior_high20 = _shift(pd.Series(h).rolling(20, min_periods=20).max().to_numpy(), 1)
    flags["momentum:CEILING"] = at_ceiling
    flags["momentum:SURGE"] = (~at_ceiling & (change >= float(m["surge_pct"]) / 100.0)
                               & (vol_ratio >= float(m["surge_volume_ratio"])))
    flags["momentum:HIGH20"] = (c > prior_high20) & (vol_ratio >= float(m["high20_volume_ratio"]))

    t = s["tight_base"]
    nr = pd.Series(rng).rolling(7, min_periods=7).min().to_numpy()
    flags["tight_base:NR7"] = uptrend & (rng <= nr) & (rng > 0)
    nb = int(t["base_sessions"])
    base_hi = _shift(pd.Series(h).rolling(nb, min_periods=nb).max().to_numpy(), 1)
    base_lo = _shift(pd.Series(l).rolling(nb, min_periods=nb).min().to_numpy(), 1)
    with np.errstate(invalid="ignore", divide="ignore"):
        width = base_hi / base_lo - 1.0
    flags["tight_base:BREAKOUT"] = ((width <= float(t["base_max_width_pct"]) / 100.0) & (c > base_hi)
                                    & (vol_ratio >= float(t["breakout_volume_ratio"])))
    return {k: np.nan_to_num(f, nan=0).astype(bool) for k, f in flags.items()}


def liquidity_tiers(close: np.ndarray, volume: np.ndarray, cfg: Mapping[str, Any]) -> np.ndarray:
    """Tier of each session by the 20-session average traded value (close x volume, as the screen)."""
    value = _sma(close * volume, 20)
    t = cfg["tiers_vnd"]
    out = np.full(len(close), "BELOW", dtype=object)
    out[value >= float(t["LOW"])] = "LOW"
    out[value >= float(t["GOOD"])] = "GOOD"
    out[value >= float(t["HIGH"])] = "HIGH"
    out[np.isnan(value)] = None
    return out


def contaminated(close: np.ndarray, exchange: str, before: int, after: int) -> np.ndarray:
    """True where a window [i - before, i + after] touches an impossible one-session move."""
    from src.collectors.backfill import close_to_close_bounds  # noqa: PLC0415

    bounds = close_to_close_bounds(exchange)
    out = np.zeros(len(close), dtype=bool)
    if bounds is None:
        return out
    down, up = bounds
    with np.errstate(invalid="ignore", divide="ignore"):
        move = (close / _shift(close, 1) - 1.0) * 100.0
    for k in np.flatnonzero((move < -down) | (move > up)):
        out[max(0, k - after):k + before + 1] = True
    return out


# ---------------------------------------------------------------------------
# Trades under Vietnam's rules
# ---------------------------------------------------------------------------

@dataclass
class Bars:
    open: np.ndarray
    high: np.ndarray
    low: np.ndarray
    close: np.ndarray
    sellable: np.ndarray            # False on a session locked at the floor
    ceiling_open: np.ndarray        # True where the session opened at the estimated ceiling

    @classmethod
    def of(cls, frame: pd.DataFrame, band_pct: float, tolerance_pct: float) -> "Bars":
        o, h, l, c = (frame[k].to_numpy(dtype="float64") for k in ("open", "high", "low", "close"))
        prev = _shift(c, 1)
        with np.errstate(invalid="ignore", divide="ignore"):
            locked_floor = (h <= l) & (c / prev - 1.0 <= -(band_pct - tolerance_pct) / 100.0)
            ceiling_open = o / prev - 1.0 >= (band_pct - tolerance_pct) / 100.0
        return cls(o, h, l, c, ~np.nan_to_num(locked_floor, nan=0).astype(bool),
                   np.nan_to_num(ceiling_open, nan=0).astype(bool))


def _next_sellable(bars: Bars, d: int) -> int | None:
    while d < len(bars.close) and not bars.sellable[d]:
        d += 1
    return d if d < len(bars.close) else None


def fixed_exits(bars: Bars, entry: int, horizons: Sequence[int]) -> dict[str, float]:
    """Gross % return selling at the close `h` sessions after the buy (moved past floor-locked sessions)."""
    price = bars.open[entry]
    out = {}
    for hz in horizons:
        d = _next_sellable(bars, entry + int(hz))
        if d is not None:
            out[f"T+{hz}"] = (bars.close[d] / price - 1.0) * 100.0
    return out


def stop_target_exit(bars: Bars, entry: int, stop_pct: float, target_pct: float, max_hold: int,
                     first_sell: int) -> float | None:
    """Gross % return with a stop and a target, sellable from `first_sell` sessions after the buy.

    On the first sellable session only its close is known to be reachable (the
    shares arrive around midday): exit there at the target if the close is at
    or above it, at the close if the stop was touched since the buy. Later
    sessions: a gap through either level fills at the open, otherwise at the
    level; both in one session counts as the stop (the cautious reading).
    """
    price = bars.open[entry]
    stop, target = price * (1 - stop_pct / 100.0), price * (1 + target_pct / 100.0)
    n = len(bars.close)
    last = entry + max_hold
    for d in range(entry + first_sell, last + 1):
        if d >= n:
            return None
        if not bars.sellable[d]:
            continue
        if d == entry + first_sell:
            if bars.close[d] >= target:
                return (target / price - 1.0) * 100.0
            if bars.low[entry:d + 1].min() <= stop:
                return (bars.close[d] / price - 1.0) * 100.0
        else:
            o, h, l = bars.open[d], bars.high[d], bars.low[d]
            if o <= stop:
                return (o / price - 1.0) * 100.0
            if l <= stop:
                return (stop / price - 1.0) * 100.0
            if o >= target:
                return (o / price - 1.0) * 100.0
            if h >= target:
                return (target / price - 1.0) * 100.0
        if d == last:
            return (bars.close[d] / price - 1.0) * 100.0
    d = _next_sellable(bars, last + 1)
    return None if d is None else (bars.close[d] / price - 1.0) * 100.0


def cost_pct(tier: str, cfg: Mapping[str, Any]) -> float:
    c = cfg["costs"]
    return 2 * float(c["brokerage_fee_pct"]) + float(c["sell_tax_pct"]) + 2 * float(c["slippage_pct_by_tier"][tier])


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

RECORD_COLUMNS = ["ticker", "pattern", "variant", "tier", "date", "regime", "value", *EXITS]


def ticker_records(ticker: str, frame: pd.DataFrame, exchange: str, cfg: Mapping[str, Any],
                   regimes: pd.Series | None, baseline_step: int) -> tuple[list[tuple], int]:
    """One RECORD_COLUMNS tuple per trade of one ticker (value = that day's close x volume); and ceiling-open skips."""
    from src.market_calendar import price_limit_pct  # noqa: PLC0415

    band = float(price_limit_pct(exchange) or 7.0)
    tol = float(cfg["ceiling_tolerance_pct"])
    flags = setup_flags(frame, band, cfg)
    bars = Bars.of(frame, band, tol)
    vol = frame["volume"].to_numpy(dtype="float64")
    tiers = liquidity_tiers(bars.close, vol, cfg)
    bad = contaminated(bars.close, exchange, int(cfg["quality"]["before_sessions"]), int(cfg["quality"]["after_sessions"]))
    dates = frame.index
    n = len(dates)
    min_hist = int(cfg["quality"]["min_history_sessions"])
    horizons = [int(x) for x in cfg["horizons"]]
    st = cfg["stop_target"]
    regime_at = regimes.reindex(dates).to_numpy() if regimes is not None else np.full(n, None)
    usable = np.zeros(n, dtype=bool)
    usable[min_hist:n - 1] = True
    usable &= ~bad & (vol > 0) & np.append(vol[1:] > 0, False) & (tiers != None)  # noqa: E711
    cooldown = int(cfg["cooldown_sessions"])
    out: list[tuple] = []
    skipped = 0

    def trade(i: int, pattern: str, variant: str) -> None:
        nonlocal skipped
        entry = i + 1
        if bars.ceiling_open[entry]:
            skipped += pattern != BASELINE
            return
        tier = str(tiers[i])
        fx = fixed_exits(bars, entry, horizons)
        slt = stop_target_exit(bars, entry, float(st["stop_pct"]), float(st["target_pct"]), int(st["max_hold"]),
                               int(cfg["first_sell_session"]))
        cost = cost_pct(tier, cfg)
        reg = regime_at[i]
        out.append((ticker, pattern, variant, tier, dates[i].date(), None if reg is None or reg != reg else str(reg),
                    float(bars.close[i] * vol[i]),
                    *[fx[f"T+{hz}"] - cost if f"T+{hz}" in fx else np.nan for hz in horizons],
                    slt - cost if slt is not None else np.nan))

    for i in np.flatnonzero(usable)[::baseline_step]:
        trade(int(i), BASELINE, "SAMPLED")
    for key, f in flags.items():
        pattern, variant = key.split(":")
        last = -10**9
        for i in np.flatnonzero(f & usable):
            if i - last > cooldown:
                trade(int(i), pattern, variant)
                last = i
    return out, skipped


def _stats(x: np.ndarray) -> dict[str, Any]:
    x = x[~np.isnan(x)]
    n = len(x)
    if not n:
        return {"n": 0}
    sd = float(x.std(ddof=1)) if n > 1 else None
    return {"n": n, "mean": float(x.mean()), "median": float(np.median(x)), "win": float((x > 0).mean() * 100),
            "sd": sd, "t": float(x.mean() / (sd / math.sqrt(n))) if sd else None,
            "p10": float(np.percentile(x, 10)), "p90": float(np.percentile(x, 90))}


def _diff_t(a: dict[str, Any], b: dict[str, Any]) -> float | None:
    if not a.get("n") or not b.get("n") or a.get("sd") is None or b.get("sd") is None:
        return None
    se = math.sqrt(a["sd"] ** 2 / a["n"] + b["sd"] ** 2 / b["n"])
    return (a["mean"] - b["mean"]) / se if se > 0 else None


def robustness(group: pd.DataFrame, baseline: pd.DataFrame, tier: str, cfg: Mapping[str, Any]) -> dict[str, Any]:
    """Whether a passing setup's average describes a typical trade, and survives harsher assumptions.

    A mean lifted by a few large rebounds while most trades lose (median below
    zero) is a lottery, not a setup; the report shows both. Also: the mean
    without the 1% extremes, without the 10 tickers that contributed most,
    year by year, with the stress slippage of `costs.stress_slippage_pct_by_tier`,
    and how much traded on the signal day (can a position be built at all).
    """
    out: dict[str, Any] = {}
    for e in ("T+5", "T+10"):
        x = group[e].dropna()
        lo, hi = x.quantile(0.01), x.quantile(0.99)
        extra = 2 * (float(cfg["costs"]["stress_slippage_pct_by_tier"][tier])
                     - float(cfg["costs"]["slippage_pct_by_tier"][tier]))
        out[e] = {"mean": float(x.mean()), "median": float(x.median()),
                  "baseline_median": float(baseline[e].dropna().median()) if len(baseline) else None,
                  "trimmed_mean": float(x[(x >= lo) & (x <= hi)].mean()), "stress_mean": float(x.mean() - extra)}
    if "ticker" in group:
        top = group.groupby("ticker")["T+10"].sum().sort_values(ascending=False).head(10).index
        out["without_top10_tickers_t10"] = float(group[~group["ticker"].isin(top)]["T+10"].mean())
        out["tickers"] = int(group["ticker"].nunique())
    years = pd.to_datetime(group["date"]).dt.year
    out["by_year_t5"] = {int(y): [round(float(g["T+5"].mean()), 2), int(len(g))] for y, g in group.groupby(years)}
    out["negative_years_t5"] = sum(1 for m, _ in out["by_year_t5"].values() if m < 0)
    if "value" in group:
        out["signal_day_value_median"] = float(group["value"].median())
        out["signal_day_value_p25"] = float(group["value"].quantile(0.25))
    return out


@dataclass
class SwingBacktest:
    rows: list[dict[str, Any]]                # one per pattern / variant / tier
    baseline: dict[str, dict[str, Any]]       # tier -> exit -> stats (whole period)
    tickers: int
    first: date | None
    last: date | None
    cut: date | None                          # first date of the out-of-sample period
    skipped_ceiling_open: int
    config: dict[str, Any] = field(default_factory=dict)

    @property
    def passing(self) -> list[dict[str, Any]]:
        return [r for r in self.rows if r["passes"]]


def evaluate(records: pd.DataFrame, cfg: Mapping[str, Any]) -> tuple[list[dict], dict, date | None]:
    """Stats per pattern / variant / tier against the same tier's baseline, in and out of sample."""
    gate = cfg["gate"]
    base = records[records["pattern"] == BASELINE]
    cut = (pd.Series(sorted(base["date"])).quantile(1 - float(gate["out_of_sample_fraction"]),
                                                     interpolation="lower") if len(base) else None)
    period = {"all": records, "in": records[records["date"] < cut] if cut else records,
              "out": records[records["date"] >= cut] if cut else records.iloc[0:0]}

    def stats_for(frame: pd.DataFrame) -> dict[str, dict[str, Any]]:
        return {e: _stats(frame[e].to_numpy(dtype="float64")) for e in EXITS}

    base_stats = {p: {tier: stats_for(df[(df["pattern"] == BASELINE) & (df["tier"] == tier)]) for tier in TIERS}
                  for p, df in period.items()}
    rows = []
    for (pattern, variant, tier), group in records[records["pattern"] != BASELINE].groupby(["pattern", "variant", "tier"]):
        r = {"pattern": pattern, "variant": variant, "tier": tier, "exits": {}, "passes": False, "passing_exits": []}
        ins, outs = group[group["date"] < cut] if cut else group, group[group["date"] >= cut] if cut else group.iloc[0:0]
        for e in EXITS:
            a, ai, ao = (_stats(g[e].to_numpy(dtype="float64")) for g in (group, ins, outs))
            b, bi, bo = base_stats["all"][tier][e], base_stats["in"][tier][e], base_stats["out"][tier][e]
            cell = {"all": a, "in": ai, "out": ao,
                    "edge": (a["mean"] - b["mean"]) if a.get("n") and b.get("n") else None,
                    "edge_in": (ai["mean"] - bi["mean"]) if ai.get("n") and bi.get("n") else None,
                    "edge_out": (ao["mean"] - bo["mean"]) if ao.get("n") and bo.get("n") else None,
                    "t_edge_in": _diff_t(ai, bi)}
            ok = (ai.get("n", 0) >= int(gate["min_signals_in_sample"])
                  and (cell["edge_in"] or -1) >= float(gate["min_edge_pct"])
                  and (cell["t_edge_in"] or 0) >= float(gate["min_t"])
                  and ao.get("n", 0) >= int(gate["min_signals_out_of_sample"])
                  and ao.get("mean", -1) > 0 and (cell["edge_out"] or -1) > 0)
            cell["passes"] = bool(ok)
            r["exits"][e] = cell
            if ok:
                r["passing_exits"].append(e)
        r["passes"] = bool(r["passing_exits"])
        # by market regime, on the T+5 exit
        r["by_regime"] = {str(k): _stats(g["T+5"].to_numpy(dtype="float64")) for k, g in group.groupby(group["regime"].fillna("UNKNOWN"))}
        if r["passes"]:
            r["robustness"] = robustness(group, records[(records["pattern"] == BASELINE) & (records["tier"] == tier)],
                                         tier, cfg)
        rows.append(r)
    return rows, base_stats, (cut.date() if hasattr(cut, "date") else cut)


def run_swing_backtest(path: Path | None = None, limit: int | None = None, tickers: Sequence[str] | None = None,
                       thresholds: Mapping[str, Any] | None = None,
                       progress: Callable[[int, int], None] | None = None) -> SwingBacktest:
    from src.backtest.sepa import load_frames, regime_by_date  # noqa: PLC0415
    from src.config import load_config  # noqa: PLC0415
    from src.quant.screener import screen_settings  # noqa: PLC0415

    th = thresholds or load_config().thresholds.raw
    cfg = th["swing"]
    settings = screen_settings(th)
    frames, exchanges = load_frames(path, limit, tickers)
    with connect(path) as conn:
        rows = conn.execute("SELECT trade_date, close, volume, high, matched_volume FROM index_daily WHERE symbol = ? "
                            "ORDER BY trade_date", (settings["market"]["index"],)).fetchall()
    index = pd.DataFrame(rows, columns=["trade_date", "close", "volume", "high", "matched_volume"])
    index = index.set_index(pd.to_datetime(index["trade_date"])).drop(columns="trade_date").astype("float64")
    regimes = regime_by_date(index, settings) if len(index) else None

    records: list[tuple] = []
    skipped = 0
    names = list(frames)
    for k, t in enumerate(names):
        recs, sk = ticker_records(t, frames[t], exchanges[t], cfg, regimes, int(cfg["baseline_step"]))
        records += recs
        skipped += sk
        if progress:
            progress(k + 1, len(names))
    df = pd.DataFrame.from_records(records, columns=RECORD_COLUMNS)
    result_rows, base_stats, cut = evaluate(df, cfg) if len(df) else ([], {}, None)
    return SwingBacktest(result_rows, base_stats.get("all", {}), len(frames),
                         min(df["date"]) if len(df) else None, max(df["date"]) if len(df) else None, cut, skipped,
                         dict(cfg))


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

def _vnd_m(value: float) -> str:
    """Millions of VND with Vietnamese thousands dots: 35747e6 -> "35.747"."""
    return f"{value / 1e6:,.0f}".replace(",", ".")


def _cell(s: Mapping[str, Any] | None) -> str:
    if not s or not s.get("n"):
        return "–"
    star = "*" if s.get("t") is not None and abs(s["t"]) >= 2 else ""
    return f"{s['mean']:+.2f}% ({s['win']:.0f}%){star}"


def render_report(bt: SwingBacktest) -> str:
    cfg = bt.config
    st = cfg["stop_target"]
    g = cfg["gate"]
    lines = [
        "# Backtest mẫu hình đánh ngắn hạn (≤ 2 tuần) trên dữ liệu Việt Nam", "",
        f"{bt.tickers} mã, tín hiệu từ {bt.first} tới {bt.last}. Ngoài mẫu (không dùng để chọn) = từ {bt.cut}.", "",
        "**Cách đo:** mua giá mở cửa phiên sau tín hiệu; bán sớm nhất chiều T+2. "
        f"Giữ cố định: bán giá đóng cửa T+3 / T+5 / T+10. SL/TP: cắt lỗ −{st['stop_pct']}%, chốt lời "
        f"+{st['target_pct']}%, tối đa {st['max_hold']} phiên. Đã trừ phí môi giới 2 chiều, thuế bán và trượt giá theo "
        "hạng thanh khoản. Bỏ tín hiệu nếu phiên sau mở cửa ở giá trần (ước lượng): "
        f"{bt.skipped_ceiling_open} lần.", "",
        "**Giới hạn:** chỉ các mã đang niêm yết (thiên lệch sống sót làm số đẹp hơn); giá không cộng cổ tức tiền; "
        "giá trần / sàn ước theo biên độ và giá đóng cửa trước; tín hiệu dồn cục theo thời gian nên mức ý nghĩa thực "
        "thấp hơn số in ra. Kết quả quá khứ không bảo đảm tương lai.", "",
        "Mỗi ô: lợi nhuận trung bình sau phí (tỷ lệ thắng); `*` = |t| ≥ 2.", "",
        "## Đối chứng: mua một mã bất kỳ cùng hạng thanh khoản", "",
        "| Hạng | N | " + " | ".join(EXITS) + " |", "|---|---|" + "---|" * len(EXITS),
    ]
    for tier in TIERS:
        b = bt.baseline.get(tier, {})
        lines.append(f"| {TIER_TEXT[tier]} | {b.get('T+5', {}).get('n', 0)} | " + " | ".join(_cell(b.get(e)) for e in EXITS) + " |")
    lines += ["", "## Các mẫu", "",
              f"**Vượt cửa** khi, ở ít nhất một cách thoát: trong mẫu N ≥ {g['min_signals_in_sample']}, hơn đối chứng "
              f"≥ {g['min_edge_pct']} điểm % với t ≥ {g['min_t']}; ngoài mẫu N ≥ {g['min_signals_out_of_sample']}, "
              "trung bình dương và vẫn hơn đối chứng.", "",
              "| Mẫu | Biến thể | Hạng | N | " + " | ".join(EXITS) + " | Hơn đối chứng T+5 | Ngoài mẫu T+5 (hơn ĐC) | Vượt cửa |",
              "|---|---|---|---|" + "---|" * (len(EXITS) + 3)]
    for r in sorted(bt.rows, key=lambda r: (r["pattern"], r["variant"], TIERS.index(r["tier"]))):
        ex = r["exits"]
        t5 = ex["T+5"]
        edge = "–" if t5["edge"] is None else f"{t5['edge']:+.2f}"
        oos = "–" if not t5["out"].get("n") else f"{t5['out']['mean']:+.2f}% ({t5['edge_out'] or 0:+.2f})"
        verdict = "✓ " + ", ".join(r["passing_exits"]) if r["passes"] else "✗"
        lines.append(
            f"| {SETUP_TEXT.get(r['pattern'], r['pattern'])} | {r['variant']} | {TIER_TEXT[r['tier']]} | "
            f"{t5['all'].get('n', 0)} | " + " | ".join(_cell(ex[e]["all"]) for e in EXITS)
            + f" | {edge} | {oos} | {verdict} |")
    passing = bt.passing
    lines += ["", "## Kết luận", ""]
    if not passing:
        lines.append("**Không mẫu nào vượt cửa bằng chứng.** Chưa có cơ sở để đưa danh sách ngắn hạn lên dashboard.")
    else:
        lines.append(f"**{len(passing)} mẫu × hạng vượt cửa** — chỉ những mẫu này được xét đưa lên danh sách hằng ngày "
                     "(chờ bạn đọc báo cáo):")
        for r in passing:
            reg = ", ".join(f"{k}: {_cell(v)} (N {v.get('n', 0)})" for k, v in sorted(r["by_regime"].items()))
            lines.append(f"- {SETUP_TEXT[r['pattern']]} {r['variant']}, {TIER_TEXT[r['tier']]}: vượt ở "
                         f"{', '.join(r['passing_exits'])}. Theo thị trường (T+5): {reg}")
        lines += ["", "## Độ vững của các mẫu vượt cửa", "",
                  "Trung bình dương mà **trung vị âm** nghĩa là phần lớn lệnh lỗ, vài lệnh hồi mạnh kéo trung bình lên.",
                  ""]
        for r in passing:
            rb = r.get("robustness") or {}
            t5, t10 = rb.get("T+5", {}), rb.get("T+10", {})
            years = ", ".join(f"{y}: {m:+.2f}% ({n})" for y, (m, n) in sorted(rb.get("by_year_t5", {}).items()))
            lines += [
                f"**{SETUP_TEXT[r['pattern']]} {r['variant']}, {TIER_TEXT[r['tier']]}** ({rb.get('tickers', '?')} mã)",
                f"- T+5: TB {t5.get('mean', 0):+.2f}%, trung vị {t5.get('median', 0):+.2f}% (đối chứng "
                f"{t5.get('baseline_median') or 0:+.2f}%), bỏ 1% cực trị {t5.get('trimmed_mean', 0):+.2f}%, trượt giá "
                f"cao hơn {t5.get('stress_mean', 0):+.2f}%",
                f"- T+10: TB {t10.get('mean', 0):+.2f}%, trung vị {t10.get('median', 0):+.2f}%, bỏ 1% cực trị "
                f"{t10.get('trimmed_mean', 0):+.2f}%, trượt giá cao hơn {t10.get('stress_mean', 0):+.2f}%, bỏ 10 mã "
                f"đóng góp nhiều nhất {rb.get('without_top10_tickers_t10', 0):+.2f}%",
                f"- Theo năm (T+5): {years} — {rb.get('negative_years_t5', 0)} năm âm",
                f"- Giá trị giao dịch phiên tín hiệu: trung vị {_vnd_m(rb.get('signal_day_value_median', 0))} triệu đ, "
                f"25% tín hiệu dưới {_vnd_m(rb.get('signal_day_value_p25', 0))} triệu đ", ""]
    return "\n".join(lines) + "\n"


def save_json(bt: SwingBacktest, path: Path) -> None:
    payload = {"tickers": bt.tickers, "first": str(bt.first), "last": str(bt.last), "cut": str(bt.cut),
               "skipped_ceiling_open": bt.skipped_ceiling_open, "baseline": bt.baseline, "rows": bt.rows,
               "config": bt.config}
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
