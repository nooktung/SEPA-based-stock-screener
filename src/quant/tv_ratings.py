"""TradingView-style technical ratings (the screener's "Technicals" view).

Each moving average and each oscillator casts one vote -- positive, negative
or neutral -- by TradingView's published rules; a rating is
(positive - negative) / votes cast, in [-1, 1]. The overall rating is the mean
of the moving-average and oscillator ratings.

These are short-term readings, not SEPA rules, and untested on Vietnamese
data: they are shown, never scored. Labels are neutral by project rule --
"Tích cực mạnh" where TradingView says "Strong Buy", and so on. Settings:
`tv_ratings` in config/thresholds.yaml. A vote whose inputs are not yet
defined (too little history) is not cast, rather than counted as neutral.

Rules follow TradingView's help page "Technical Ratings" (checked 2026-10-07,
congthuc.md section 14): Ichimoku's four conditions, Stochastic by level, ADX
rising, Momentum rising, Bull / Bear power apart. Two earlier readings
(Momentum by sign, Stochastic crossing) stay available as `momentum_mode` /
`stoch_mode` for the benchmark against TradingView's screener
(src/quant/tv_benchmark.py).
"""

from __future__ import annotations

import math
from typing import Any, Mapping

import pandas as pd

from src.quant import indicators as ind

LABELS = {"STRONG_POS": "Tích cực mạnh", "POS": "Tích cực", "NEUTRAL": "Trung tính",
          "NEG": "Tiêu cực", "STRONG_NEG": "Tiêu cực mạnh"}
TV_WORDS = {"STRONG_POS": "Mua mạnh", "POS": "Mua", "NEUTRAL": "Trung tính", "NEG": "Bán", "STRONG_NEG": "Bán mạnh"}


def _ok(*values: float) -> bool:
    return all(v is not None and not (isinstance(v, float) and math.isnan(v)) for v in values)


def _last(s: pd.Series, back: int = 0) -> float:
    return float(s.iloc[-1 - back]) if len(s) > back else float("nan")


def bucket(score: float | None, cfg: Mapping[str, Any]) -> str | None:
    if score is None:
        return None
    strong, weak = float(cfg["strong"]), float(cfg["neutral_band"])
    if score > strong:
        return "STRONG_POS"
    if score > weak:
        return "POS"
    if score >= -weak:
        return "NEUTRAL"
    return "NEG" if score >= -strong else "STRONG_NEG"


def _score(votes: list[dict[str, Any]], cfg: Mapping[str, Any]) -> dict[str, Any]:
    cast = [v for v in votes if v["vote"] is not None]
    pos = sum(1 for v in cast if v["vote"] > 0)
    neg = sum(1 for v in cast if v["vote"] < 0)
    score = (pos - neg) / len(cast) if cast else None
    key = bucket(score, cfg)
    return {"score": None if score is None else round(score, 3), "key": key, "label": LABELS.get(key),
            "tv_word": TV_WORDS.get(key), "positive": pos, "negative": neg, "neutral": len(cast) - pos - neg,
            "votes": votes}


def ratings(frame: pd.DataFrame, thresholds: Mapping[str, Any],
            modes: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """`modes` overrides `momentum_mode`, `stoch_mode`, `ichimoku_shift` (the benchmark tries each)."""
    cfg = {**thresholds["tv_ratings"], **(modes or {})}
    h, l, c = (frame[k].astype("float64") for k in ("high", "low", "close"))
    v = frame["volume"].astype("float64")
    price = _last(c)

    # -- moving averages: positive when the average is below the close
    ma_votes = []

    def ma_vote(name: str, value: float) -> None:
        vote = None if not _ok(value, price) else (1 if value < price else -1 if value > price else 0)
        ma_votes.append({"name": name, "value": None if not _ok(value) else round(value, 2), "vote": vote})

    for n in cfg["ma_periods"]:
        ma_vote(f"SMA {n}", _last(ind.sma(c, int(n))))
        ma_vote(f"EMA {n}", _last(ind.ema(c, int(n))) if len(c) >= int(n) else float("nan"))
    # Ichimoku Cloud (9, 26, 52): TradingView's four conditions, not "line below price". The cloud is
    # the one drawn under today's bar: leading spans computed `ichimoku_shift` sessions earlier
    # (displacement 26 is plotted 25 bars ahead). Unshifted, span A always lies between the
    # conversion and base lines, so neither condition could ever hold.
    shift = int(cfg.get("ichimoku_shift", 25))
    tenkan, kijun = ind.donchian_mid(h, l, 9), ind.donchian_mid(h, l, 26)
    span_a, span_b = ((tenkan + kijun) / 2).shift(shift), ind.donchian_mid(h, l, 52).shift(shift)
    t0, k0, a0, b0 = _last(tenkan), _last(kijun), _last(span_a), _last(span_b)
    ichi = None if not _ok(t0, k0, a0, b0, price) else (
        1 if a0 > b0 and k0 > a0 and t0 > k0 and price > t0 else
        -1 if a0 < b0 and k0 < a0 and t0 < k0 and price < t0 else 0)
    ma_votes.append({"name": "Ichimoku Cloud (9, 26, 52)", "value": round(k0, 2) if _ok(k0) else None, "vote": ichi})
    ma_vote("VWMA 20", _last(ind.vwma(c, v, 20)))
    ma_vote("Hull MA 9", _last(ind.hull_ma(c, 9)))

    # -- oscillators, each by TradingView's own rule
    osc = []

    def add(name: str, value: float | None, vote: int | None) -> None:
        osc.append({"name": name, "value": None if not _ok(value) else round(float(value), 2), "vote": vote})

    # "Uptrend / downtrend" for Stoch RSI and Bull Bear Power: close vs SMA `trend_sma`.
    # TradingView's page does not define it [TRIỂN KHAI].
    trend_ma = _last(ind.sma(c, int(cfg.get("trend_sma", 50))))
    trend_up = _ok(trend_ma) and price > trend_ma
    trend_down = _ok(trend_ma) and price < trend_ma

    r = ind.rsi(c, 14); r0, r1 = _last(r), _last(r, 1)
    add("RSI (14)", r0, None if not _ok(r0, r1) else 1 if r0 < 30 and r0 > r1 else -1 if r0 > 70 and r0 < r1 else 0)

    k, d = ind.stoch(h, l, c, 14, 3, 3); k0, k1, d0, d1 = _last(k), _last(k, 1), _last(d), _last(d, 1)
    if str(cfg.get("stoch_mode", "level")) == "cross":
        # earlier reading: %K must cross %D
        up_cross = _ok(k0, k1, d0, d1) and k1 <= d1 and k0 > d0
        down_cross = _ok(k0, k1, d0, d1) and k1 >= d1 and k0 < d0
        stoch_vote = None if not _ok(k0, d0) else 1 if k0 < 20 and up_cross else -1 if k0 > 80 and down_cross else 0
    else:
        # TradingView: %K < 20, %D < 20 and %K > %D (sell: > 80, > 80, %K < %D)
        stoch_vote = None if not _ok(k0, d0) else (1 if k0 < 20 and d0 < 20 and k0 > d0 else
                                                   -1 if k0 > 80 and d0 > 80 and k0 < d0 else 0)
    add("Stoch %K (14, 3, 3)", k0, stoch_vote)

    cc = ind.cci(h, l, c, 20); c0, c1 = _last(cc), _last(cc, 1)
    add("CCI (20)", c0, None if not _ok(c0, c1) else 1 if c0 < -100 and c0 > c1 else -1 if c0 > 100 and c0 < c1 else 0)

    # TradingView: +DI > -DI, ADX > 20 and ADX rising (sell: +DI < -DI, ADX > 20, ADX falling)
    a, pdi, mdi = ind.adx(h, l, c, 14)
    a0, a1, p0, m0 = _last(a), _last(a, 1), _last(pdi), _last(mdi)
    add("ADX (14)", a0, None if not _ok(a0, a1, p0, m0) else
        1 if p0 > m0 and a0 > 20 and a0 > a1 else -1 if p0 < m0 and a0 > 20 and a0 < a1 else 0)

    ao = ind.awesome_oscillator(h, l); o0, o1, o2 = _last(ao), _last(ao, 1), _last(ao, 2)
    if _ok(o0, o1, o2):
        saucer_up = o0 > 0 and o1 > 0 and o2 > o1 < o0
        saucer_down = o0 < 0 and o1 < 0 and o2 < o1 > o0
        vote = 1 if saucer_up or (o1 <= 0 < o0) else -1 if saucer_down or (o1 >= 0 > o0) else 0
    else:
        vote = None
    add("Awesome Oscillator", o0, vote)

    mo = ind.momentum(c, 10); mo0, mo1 = _last(mo), _last(mo, 1)
    if str(cfg.get("momentum_mode", "rising")) == "sign":
        # earlier reading (matched 15 of 19 tickers on 2026-10-03): positive / negative
        mom_vote = None if not _ok(mo0) else 1 if mo0 > 0 else -1 if mo0 < 0 else 0
    else:
        # TradingView: current value above / below the previous one
        mom_vote = None if not _ok(mo0, mo1) else 1 if mo0 > mo1 else -1 if mo0 < mo1 else 0
    add("Momentum (10)", mo0, mom_vote)

    m_line, m_sig, _ = ind.macd(c, 12, 26, 9); ml, ms = _last(m_line), _last(m_sig)
    add("MACD (12, 26)", ml, None if not _ok(ml, ms) or len(c) < 35 else 1 if ml > ms else -1 if ml < ms else 0)

    sk, sd = ind.stoch_rsi(c, 3, 3, 14, 14); sk0, sd0 = _last(sk), _last(sd)
    add("Stoch RSI (3, 3, 14, 14)", sk0, None if not _ok(sk0, sd0) else
        1 if trend_down and sk0 < 20 and sd0 < 20 and sk0 > sd0 else
        -1 if trend_up and sk0 > 80 and sd0 > 80 and sk0 < sd0 else 0)

    w = ind.williams_r(h, l, c, 14); w0, w1 = _last(w), _last(w, 1)
    add("Williams %R (14)", w0, None if not _ok(w0, w1) else 1 if w0 < -80 and w0 > w1 else -1 if w0 > -20 and w0 < w1 else 0)

    # TradingView: buy = uptrend, Bear power < 0 and rising; sell = downtrend, Bull power > 0 and falling.
    # Bull power = high - EMA13, Bear power = low - EMA13 (Elder); the value shown is their sum.
    e13 = ind.ema(c, 13)
    bull, bear = h - e13, l - e13
    bu0, bu1, be0, be1 = _last(bull), _last(bull, 1), _last(bear), _last(bear, 1)
    add("Bull Bear Power", bu0 + be0 if _ok(bu0, be0) else None, None if not _ok(bu0, bu1, be0, be1) else
        1 if trend_up and be0 < 0 and be0 > be1 else -1 if trend_down and bu0 > 0 and bu0 < bu1 else 0)

    uo = _last(ind.ultimate_oscillator(h, l, c, 7, 14, 28))
    add("Ultimate Oscillator (7, 14, 28)", uo, None if not _ok(uo) else 1 if uo > 70 else -1 if uo < 30 else 0)

    ma_r, osc_r = _score(ma_votes, cfg), _score(osc, cfg)
    parts = [x["score"] for x in (ma_r, osc_r) if x["score"] is not None]
    overall = sum(parts) / len(parts) if parts else None
    key = bucket(overall, cfg)
    return {
        "overall": {"score": None if overall is None else round(overall, 3), "key": key, "label": LABELS.get(key),
                    "tv_word": TV_WORDS.get(key)},
        "ma": ma_r, "osc": osc_r,
        "values": {"rsi": _round(r0), "mom": _round(mo0), "ao": _round(o0), "cci": _round(c0),
                   "stoch_k": _round(k0), "stoch_d": _round(d0)},
    }


def _round(x: float) -> float | None:
    return round(x, 2) if _ok(x) else None
