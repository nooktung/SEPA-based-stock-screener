"""SEPA screen: whole universe -> a focus list of at most 60 research candidates.

Implements the rules in `.claude/skills/sepa-vn-screening/` (Minervini's Trend
Template, an IBD-style RS rating, base/VCP detection) with every threshold in
`config/thresholds.yaml` under `sepa_screen`. Each stage records why a ticker
left the funnel, and each member carries the measured value of every
criterion, not only pass/fail.

What this is not: a buy list, or a forecast. The Trend Template is a necessary
condition for a Stage 2 leader, not a signal; the base detector turns
qualitative chart reading into measurable rules that are implementation
choices ([TRIỂN KHAI]) and have not yet been backtested on Vietnamese data.

Pure functions take DataFrames indexed by date with open/high/low/close/volume
columns. `run_screen` is the only part that touches the database.
"""

from __future__ import annotations

import json
import math
import uuid
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import numpy as np
import pandas as pd

from src.config import load_config
from src.database import connect, init_db
from src.quant import indicators as ind
from src.quant.setup_status import FORMING_BASES, RS_TIER_ORDER, assess, rs_tier
from src.quant.vnfmt import dmy as _dmy
from src.quant.vnfmt import num as _n
from src.quant.vnfmt import pct as _pct

# Version of the base rules stored with every base read (monitor state, screen member), so a
# number computed by the earlier rules can be told apart (tester report 2, 2026-10-11):
# 1 (absent) = zigzag legs, pivot at the high since the last 3% low, stop rounded down;
# 2 = major contractions, pivot at the last contraction's (or tight area's) high, stop risk <= 8%,
# left-side base start, opening spikes ignored, no stop without a forming base.
BASE_RULES = 2

GROUP_A, GROUP_B, GROUP_C = "A", "B", "C"
CRITERIA = ("TT1", "TT2", "TT3", "TT4", "TT5", "TT6", "TT7", "TT8")
CRITERIA_TEXT = {
    "TT1": "Giá > MA150 và MA200",
    "TT2": "MA150 > MA200",
    "TT3": "MA200 đi lên ≥ 1 tháng",
    "TT4": "MA50 > MA150 và MA200",
    "TT5": "Giá > MA50",
    "TT6": "Giá cao hơn đáy 52 tuần ≥ X%",
    "TT7": "Giá cách đỉnh 52 tuần ≤ 25%",
    "TT8": "RS Rating ≥ 70",
}


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

def screen_settings(thresholds: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw = thresholds if thresholds is not None else load_config().thresholds.raw
    return dict(raw["sepa_screen"])


# ---------------------------------------------------------------------------
# Tick sizes and T+2 lock (skill 05 §6.1-6.2)
# ---------------------------------------------------------------------------

def tick_size(price_vnd: float, exchange: str) -> float:
    """HOSE: 10 below 10,000; 50 up to 49,950; 100 above. HNX/UPCoM: 100."""
    if exchange.upper() == "HOSE":
        if price_vnd < 10_000:
            return 10.0
        if price_vnd < 50_000:
            return 50.0
        return 100.0
    return 100.0


def round_to_tick(price: float, exchange: str, up: bool) -> float:
    """`price` on the exchange's tick grid: up for a pivot (a buy point is never below the
    level it clears), down for a stop. Adjusted histories put old highs and lows between
    ticks (HDB after its 30% stock dividend: 23,080), which no order can be placed at."""
    tick = tick_size(price, exchange)
    steps = price / tick
    n = math.ceil(steps - 1e-9) if up else math.floor(steps + 1e-9)
    return float(n * tick)


def locked_worst_case_pct(band_pct: float, from_ceiling: bool = False) -> float:
    """Loss if every session limits down until the first possible T+2 sale.

    Three limit-down moves can compound before the afternoon of T+2: the rest
    of day T, day T+1 and T+2 morning. Not a bound -- a stock locked at the
    floor with no bids cannot be sold at all.
    """
    b = band_pct / 100.0
    remaining = (1 - b) ** 3
    if from_ceiling:
        remaining /= 1 + b
    return round((1 - remaining) * 100.0, 2)


# ---------------------------------------------------------------------------
# RS rating
# ---------------------------------------------------------------------------

def rs_raw_score(close: pd.Series, variant: str, periods: Sequence[int]) -> float | None:
    """Weighted price performance; None without a full year of history."""
    p3, p6, p9, p12 = periods
    if len(close) <= p12:
        return None
    last = float(close.iloc[-1])

    def ret(n: int) -> float:
        return last / float(close.iloc[-1 - n]) - 1.0

    if variant.upper() == "A":
        # Overlapping periods: 0.4*P3 + 0.2*P6 + 0.2*P9 + 0.2*P12 [THỨ CẤP]
        return 0.4 * ret(p3) + 0.2 * ret(p6) + 0.2 * ret(p9) + 0.2 * ret(p12)
    # Variant B, separate quarters, latest double-weighted: 2*Q1 + Q2 + Q3 + Q4 [THỨ CẤP]
    c = [float(close.iloc[-1 - n]) for n in (0, p3, p6, p9, p12)]
    q = [c[i] / c[i + 1] - 1.0 for i in range(4)]
    return 2 * q[0] + q[1] + q[2] + q[3]


def rs_ratings(raw_scores: Mapping[str, float]) -> dict[str, float]:
    """Percentile rank 1..99 across the tickers given (the liquid universe)."""
    if not raw_scores:
        return {}
    s = pd.Series(raw_scores, dtype="float64")
    pct = s.rank(pct=True, method="average") * 100.0
    return {t: float(min(99.0, max(1.0, round(v)))) for t, v in pct.items()}


# ---------------------------------------------------------------------------
# Liquidity
# ---------------------------------------------------------------------------

LIQUIDITY_TIER_TEXT = {"HIGH": "Cao", "GOOD": "Khá", "LOW": "Thấp"}


def liquidity_tier(value_vnd: float | None, settings: Mapping[str, Any]) -> str | None:
    """HIGH / GOOD / LOW for a name that cleared the floor; a label, never a gate."""
    tiers = settings["liquidity"].get("tiers")
    if value_vnd is None or not tiers:
        return None
    if value_vnd >= float(tiers["high_vnd"]):
        return "HIGH"
    return "GOOD" if value_vnd >= float(tiers["good_vnd"]) else "LOW"


def liquidity(frame: pd.DataFrame, settings: Mapping[str, Any]) -> tuple[float | None, float]:
    """(estimated average traded value in VND, share of sessions that traded)."""
    liq = settings["liquidity"]
    tail = frame.tail(int(liq["window"]))
    value = (tail["close"] * tail["volume"]).mean() if len(tail) else None
    active = frame.tail(int(liq["active_window"]))
    ratio = float((active["volume"] > 0).mean()) if len(active) else 0.0
    return (None if value is None or math.isnan(value) else float(value)), ratio


# ---------------------------------------------------------------------------
# Trend Template
# ---------------------------------------------------------------------------

@dataclass
class Criterion:
    code: str
    passed: bool
    value: float | None
    threshold: str
    assessable: bool = True        # False: not enough history to measure (a new listing)
    label: str | None = None       # the text with the configured threshold (TT6: "... >= 30%")

    def as_dict(self) -> dict[str, Any]:
        finite = self.value is not None and math.isfinite(self.value)
        return {"pass": self.passed if self.assessable else None,
                "value": round(self.value, 2) if finite else None,
                "threshold": self.threshold, "text": self.label or CRITERIA_TEXT[self.code]}


def criteria_labels(settings: Mapping[str, Any]) -> dict[str, str]:
    """Criterion texts with the thresholds in force ("≥ X%" was left unfilled -- tester R3-07)."""
    tt = settings["trend_template"]
    return {**CRITERIA_TEXT,
            "TT6": f"Giá cao hơn đáy 52 tuần ≥ {_n(float(tt['above_52w_low_pct']))}%",
            "TT7": f"Giá cách đỉnh 52 tuần ≤ {_n(float(tt['within_52w_high_pct']))}%",
            "TT8": f"RS Rating ≥ {_n(float(tt['min_rs_rating']))}"}


def trend_template(frame: pd.DataFrame, rs_rating: float | None, settings: Mapping[str, Any]) -> dict[str, Criterion]:
    tt = settings["trend_template"]
    close = frame["close"].astype("float64")
    last = float(close.iloc[-1])
    ma50, ma150, ma200 = (float(ind.sma(close, n).iloc[-1]) for n in (50, 150, 200))
    ma200_series = ind.sma(close, 200)
    n_rise = int(tt["ma200_rising_sessions"])
    ma200_then = float(ma200_series.iloc[-1 - n_rise]) if len(ma200_series) > n_rise else float("nan")
    window = frame.tail(int(settings["lookback_52w_sessions"]))
    high_52w = float(window["high"].max())
    low_52w = float(window["low"].min())
    above_low_pct = (last / low_52w - 1) * 100.0 if low_52w else None
    below_high_pct = (1 - last / high_52w) * 100.0 if high_52w else None
    ma200_rise_pct = (ma200 / ma200_then - 1) * 100.0 if ma200_then == ma200_then and ma200_then else None
    min_rs = float(tt["min_rs_rating"])
    labels = criteria_labels(settings)

    out = {
        "TT1": Criterion("TT1", last > ma150 and last > ma200, (last / max(ma150, ma200) - 1) * 100.0, "> 0% trên MA150/MA200"),
        "TT2": Criterion("TT2", ma150 > ma200, (ma150 / ma200 - 1) * 100.0, "> 0%"),
        "TT3": Criterion("TT3", ma200_rise_pct is not None and ma200_rise_pct > 0, ma200_rise_pct,
                         f"MA200 hôm nay > MA200 {n_rise} phiên trước"),
        "TT4": Criterion("TT4", ma50 > ma150 and ma50 > ma200, (ma50 / max(ma150, ma200) - 1) * 100.0, "> 0%"),
        "TT5": Criterion("TT5", last > ma50, (last / ma50 - 1) * 100.0, "> 0%"),
        "TT6": Criterion("TT6", above_low_pct is not None and above_low_pct >= float(tt["above_52w_low_pct"]),
                         above_low_pct, f">= {tt['above_52w_low_pct']}%"),
        "TT7": Criterion("TT7", below_high_pct is not None and below_high_pct <= float(tt["within_52w_high_pct"]),
                         below_high_pct, f"<= {tt['within_52w_high_pct']}% dưới đỉnh"),
        "TT8": Criterion("TT8", rs_rating is not None and rs_rating >= min_rs, rs_rating, f">= {min_rs:.0f}"),
    }
    for code, c in out.items():
        c.label = labels[code]
    return out


def ma200_rising_preferred(frame: pd.DataFrame, settings: Mapping[str, Any]) -> bool | None:
    n = int(settings["trend_template"]["ma200_rising_preferred_sessions"])
    ma200 = ind.sma(frame["close"].astype("float64"), 200)
    if len(ma200.dropna()) <= n:
        return None
    return bool(ma200.iloc[-1] > ma200.iloc[-1 - n])


# ---------------------------------------------------------------------------
# Base / VCP detection [TRIỂN KHAI]
# ---------------------------------------------------------------------------

def zigzag(frame: pd.DataFrame, min_swing_pct: float) -> list[tuple[str, int, float]]:
    """Alternating swing points (kind, position, price) reversing by >= min_swing_pct.

    Uses highs for peaks and lows for troughs. The final point is provisional:
    it is the extreme so far, not a confirmed swing.
    """
    highs = frame["high"].astype("float64").to_numpy()
    lows = frame["low"].astype("float64").to_numpy()
    if len(highs) == 0:
        return []
    threshold = min_swing_pct / 100.0
    points: list[tuple[str, int, float]] = []
    mode = "HIGH"
    ext_i, ext_p = 0, highs[0]
    for i in range(1, len(highs)):
        if mode == "HIGH":
            if highs[i] >= ext_p:
                ext_i, ext_p = i, highs[i]
            elif lows[i] <= ext_p * (1 - threshold):
                points.append(("HIGH", ext_i, float(ext_p)))
                mode, ext_i, ext_p = "LOW", i, lows[i]
        else:
            if lows[i] <= ext_p:
                ext_i, ext_p = i, lows[i]
            elif highs[i] >= ext_p * (1 + threshold):
                points.append(("LOW", ext_i, float(ext_p)))
                mode, ext_i, ext_p = "HIGH", i, highs[i]
    points.append((mode, ext_i, float(ext_p)))
    return points


@dataclass
class BaseRead:
    kind: str = "NONE"   # VCP | FLAT_BASE | CUP_HANDLE | CUP | BASE | NEAR_HIGH | SHARP_PULLBACK | EXTENDED | TOO_DEEP | BROKEN | NONE
    sessions: int = 0
    depth_pct: float | None = None
    contractions: list[float] = field(default_factory=list)
    pivot: float | None = None
    stop: float | None = None
    distance_to_pivot_pct: float | None = None   # negative = below pivot
    volume_dryup: bool | None = None
    tightness_pct: float | None = None           # last-contraction depth
    quality: float = 0.0                         # 0..100, for ranking only
    notes: list[str] = field(default_factory=list)
    # Geometry, for drawing and explaining the base (dates as ISO strings).
    start_date: str | None = None                # the left-side high the base is measured from
    top: float | None = None                     # the base's highest (effective) high ...
    top_date: str | None = None                  # ... where the contractions are measured from
    low_date: str | None = None
    low: float | None = None
    # Cup geometry (when a cup was evaluated) and the rule-by-rule result.
    right_lip_date: str | None = None
    right_lip: float | None = None
    handle_low_date: str | None = None
    handle_low: float | None = None
    cup_checks: list[dict[str, Any]] = field(default_factory=list)
    # Set by tracked_base: the base is held from the session a close broke out of it
    # ("BREAKOUT") or broke its stop ("STOP"), so its pivot and stop stay where they were.
    held_since: str | None = None
    held_by: str | None = None
    # The major contractions, for drawing: {high_date, high, low_date, low, depth, tight}; the last
    # one is the final tight area when `tight` is set (its depth is in `contractions` too).
    legs: list[dict[str, Any]] = field(default_factory=list)
    spike_dates: list[str] = field(default_factory=list)   # opening spikes measured at their close
    rules: int = BASE_RULES

    @property
    def forming(self) -> bool:
        return self.kind in FORMING_BASES

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        for k in ("depth_pct", "pivot", "stop", "distance_to_pivot_pct", "tightness_pct"):
            if d[k] is not None:
                d[k] = round(d[k], 2)
        d["contractions"] = [round(x, 2) for x in self.contractions]
        d["quality"] = round(self.quality, 1)
        return d


@dataclass
class CupRead:
    ok: bool                         # every cup-shape rule passed
    handle: bool                     # a valid handle has formed
    checks: list[dict[str, Any]]     # {"rule", "ok", "text"} in display order
    right_lip: float | None = None
    right_lip_pos: int | None = None
    handle_low: float | None = None
    handle_low_pos: int | None = None


def evaluate_cup(frame: pd.DataFrame, top_index: int, base: pd.DataFrame, settings: Mapping[str, Any]) -> CupRead:
    """Test a base against measurable forms of O'Neil's cup with handle.

    `frame` is the full history, `top_index` the base's highest high in it and
    `base` the bars from that high to now. Depth, length and the upper-half
    recovery are the textbook numbers; "U not V", "a real prior uptrend" and
    the handle are implementation choices, reported rule by rule.
    """
    b = settings["base"]
    cup, h = b["cup"], b["handle"]
    highs, lows = base["high"].astype("float64"), base["low"].astype("float64")
    closes, vols = base["close"].astype("float64"), base["volume"].astype("float64")
    top = float(highs.iloc[0])
    low_pos = int(np.argmin(lows.to_numpy()))
    low = float(lows.iloc[low_pos])
    depth = (1 - low / top) * 100.0
    mid = low + (top - low) / 2
    last = float(closes.iloc[-1])
    checks: list[dict[str, Any]] = []

    def add(rule: str, ok: bool, text: str) -> None:
        checks.append({"rule": rule, "ok": bool(ok), "text": text})

    add("depth", float(cup["min_depth_pct"]) <= depth <= float(cup["max_depth_pct"]),
        f"Độ sâu {_pct(depth)} (cần {cup['min_depth_pct']}–{cup['max_depth_pct']}%, O'Neil)")
    add("length", len(base) >= int(cup["min_sessions"]),
        f"Dài {len(base)} phiên ≈ {len(base) / 5:.0f} tuần (cần ≥ {cup['min_sessions']} phiên ≈ 7 tuần)")
    zone = low + (top - low) * float(cup["bottom_zone_pct"]) / 100.0
    # Left side = sessions until price FIRST reaches the bottom zone. Measuring
    # to the absolute low instead lets a crash followed by a slight drift lower
    # pass as a gentle slope.
    reached = np.flatnonzero(lows.to_numpy() <= zone)
    left = int(reached[0]) if len(reached) else low_pos
    add("left_side", left >= int(cup["min_left_side_sessions"]),
        f"Từ đỉnh xuống vùng đáy mất {left} phiên (cần ≥ {cup['min_left_side_sessions']}; nhanh hơn là rơi thẳng kiểu chữ V)")
    bottom = int((closes <= zone).sum())
    add("u_bottom", bottom >= int(cup["min_bottom_sessions"]),
        f"{bottom} phiên đóng cửa ở 1/4 dưới của cốc (cần ≥ {cup['min_bottom_sessions']}; đáy tròn chữ U cần thời gian)")

    look = int(cup["prior_uptrend_lookback_sessions"])
    before = frame.iloc[max(0, top_index - look): top_index + 1]
    # The MOST RECENT lowest low before the top: after a long flat stretch the
    # first of equal lows would make a one-week spike look like a long advance.
    pre_lows = before["low"].astype("float64").to_numpy()
    pre_low_pos = len(pre_lows) - 1 - int(np.argmin(pre_lows[::-1]))
    pre_low = float(before["low"].iloc[pre_low_pos])
    gain = (top / pre_low - 1) * 100.0 if pre_low else 0.0
    run = len(before) - 1 - pre_low_pos
    add("prior_uptrend", gain >= float(cup["prior_uptrend_pct"]) and run >= int(cup["prior_uptrend_min_sessions"]),
        f"Trước nền tăng {gain:.0f}% trong {run} phiên (cần ≥ {cup['prior_uptrend_pct']}% "
        f"kéo dài ≥ {cup['prior_uptrend_min_sessions']} phiên, không phải một cú vọt)")
    # The text states what the numbers say (tester M10: "đã hồi lên nửa trên" beside 17,500 < 17,700).
    add("upper_half", last >= mid,
        f"Giá {_n(last)} đã ở nửa trên của cốc (giữa cốc {_n(mid)})" if last >= mid
        else f"Giá {_n(last)} còn ở nửa dưới của cốc (giữa cốc {_n(mid)}) — cần hồi lên nửa trên")
    ok = all(c["ok"] for c in checks)

    # Handle: the highest point after the low (right lip), then a shallow,
    # quiet pullback in the upper half lasting at least a week.
    after_low = highs.iloc[low_pos:]
    rl_pos = low_pos + int(np.argmax(after_low.to_numpy()))
    right_lip = float(highs.iloc[rl_pos])
    tail = base.iloc[rl_pos + 1:]
    result = CupRead(ok=ok, handle=False, checks=checks, right_lip=right_lip, right_lip_pos=rl_pos)
    if len(tail) >= int(h["min_sessions"]) and right_lip >= mid:
        hl_rel = int(np.argmin(tail["low"].astype("float64").to_numpy()))
        handle_low = float(tail["low"].iloc[hl_rel])
        h_depth = (1 - handle_low / right_lip) * 100.0
        quiet = float(tail["volume"].mean()) < float(vols.mean())
        handle_ok = 0 < h_depth <= float(h["max_depth_pct"]) and handle_low >= mid and quiet
        result.handle_low, result.handle_low_pos = handle_low, rl_pos + 1 + hl_rel
        result.handle = handle_ok
        checks.append({"rule": "handle", "ok": handle_ok,
                       "text": f"Tay cầm {len(tail)} phiên, sâu {_pct(h_depth)} (cần ≤ {h['max_depth_pct']}%), "
                               f"{'nằm' if handle_low >= mid else 'KHÔNG nằm'} ở nửa trên, khối lượng "
                               f"{'thấp hơn' if quiet else 'không thấp hơn'} trung bình cốc"})
    else:
        checks.append({"rule": "handle", "ok": False,
                       "text": f"Chưa có tay cầm (cần ≥ {h['min_sessions']} phiên điều chỉnh nhẹ sau đỉnh bên phải, ở nửa trên)"})
    return result


def read_base(frame: pd.DataFrame, exchange: str, settings: Mapping[str, Any]) -> BaseRead:
    """The base the stock is in (see _read_base_raw), on the exchange's tick grid.

    The pivot is rounded up. The stop is rounded down, but never below pivot - max_stop_pct
    rounded UP, so the risk from the pivot never exceeds max_stop_pct (tester M03: ABB's
    17,480 rounded down to 17,400 was 8.4%). Only a forming base keeps a stop: without a buy
    point at a pivot there is nothing for a stop to protect (user decision 2026-10-11).
    """
    read = _read_base_raw(frame, exchange, settings)
    if read.pivot:
        read.pivot = round_to_tick(read.pivot, exchange, up=True)
        read.distance_to_pivot_pct = (float(frame["close"].iloc[-1]) / read.pivot - 1) * 100.0
    if read.forming and read.pivot and read.stop:
        floor = round_to_tick(read.pivot * (1 - float(settings["base"]["max_stop_pct"]) / 100.0), exchange, up=True)
        read.stop = max(round_to_tick(read.stop, exchange, up=False), floor)
    else:
        read.stop = None
    return read


def tracked_base(frame: pd.DataFrame, exchange: str, settings: Mapping[str, Any]) -> BaseRead:
    """The base to show and judge today: today's base, except after an event.

    Read on every session, a base takes the breakout bar as its new right side,
    so the pivot climbs above a stock that has just broken out (GMD 07/10/2026:
    out of 52,770, then "pivot 55,100") and the stop drops to wherever the price
    fell after breaking it (VIC 09/10/2026: stop 226,900 -> 225,500, the close).
    For `base.hold_sessions` after a close above the base's pivot or below its
    stop, the base that was knowable the session before that close is kept --
    pivot, stop, label -- and marked `held_since` / `held_by`. A new forming
    base that starts after that session replaces it [TRIỂN KHAI]. A close above a base
    whose top was set inside the window is not a breakout: after a breakout bar the
    base can be re-read with that bar as its top (it is within left_high_tolerance_pct
    of the old one), and the next close above that re-read pivot is the same move.
    """
    current = read_base(frame, exchange, settings)
    hold = int(settings["base"].get("hold_sessions", 0) or 0)
    close = frame["close"].to_numpy(dtype="float64")
    n = len(frame)
    for k in range(min(hold, n - int(settings["base"]["min_base_sessions"]) - 1)):
        i = n - 1 - k
        prior = read_base(frame.iloc[:i], exchange, settings)
        if not prior.pivot:
            continue
        if not prior.forming:
            continue                                         # only a real base has a pivot and stop to keep
        recent_top = bool(prior.top_date) and prior.top_date >= frame.index[max(0, i - hold)].date().isoformat()
        broke_out = close[i] > prior.pivot >= close[i - 1] and not recent_top
        broke_stop = bool(prior.stop) and close[i] < prior.stop <= close[i - 1]
        if not (broke_out or broke_stop):
            continue
        day = frame.index[i].date().isoformat()
        if current.forming and current.start_date and current.start_date > day:
            return current                                   # a new base has formed since
        held = BaseRead(**{**asdict(prior), "contractions": list(prior.contractions),
                           "notes": list(prior.notes), "cup_checks": list(prior.cup_checks)})
        held.held_since, held.held_by = day, "BREAKOUT" if broke_out else "STOP"
        held.distance_to_pivot_pct = (float(close[-1]) / held.pivot - 1) * 100.0
        what = "breakout" if broke_out else "phiên thủng stop"
        held.notes.insert(0, f"pivot và stop giữ từ {what} {_dmy(day)}")
        return held
    return current


def _effective_highs(window: pd.DataFrame, exchange: str, b: Mapping[str, Any]) -> tuple[np.ndarray, list[int]]:
    """Highs for measuring a base, with an opening spike counted at its close (tester M02).

    ABB opened at its UPCoM ceiling, 18,900, on 09/07/2026 and closed at 17,200; nothing
    else traded within 4% of that print for three months, yet it became the base's top
    and the pivot sat 7.9% above the price. A session counts as such a spike when its high
    was printed at the open (open within one tick of the high), it closed in the lower
    part of its range and its high stands `isolation_pct` above every other high within
    `neighbour_sessions` [TRIỂN KHAI]. Returns the highs and the spike positions.
    """
    high = window["high"].to_numpy(dtype="float64")
    eff = high.copy()
    cfg = b.get("spike_bar")
    if not cfg or len(high) < 2:
        return eff, []
    opn = window["open"].to_numpy(dtype="float64")
    low = window["low"].to_numpy(dtype="float64")
    close = window["close"].to_numpy(dtype="float64")
    nb = int(cfg["neighbour_sessions"])
    s = pd.Series(high)
    before = s.shift(1).rolling(nb, min_periods=1).max().to_numpy()
    after = s[::-1].shift(1).rolling(nb, min_periods=1).max()[::-1].to_numpy()
    others = np.fmax(before, after)
    rng = high - low
    candidates = np.flatnonzero((rng > 0) & (close - low <= float(cfg["max_close_position"]) * rng)
                                & (high >= others * (1 + float(cfg["isolation_pct"]) / 100.0)))
    spikes = [int(i) for i in candidates if opn[i] >= high[i] - tick_size(high[i], exchange) - 1e-6]
    eff[spikes] = close[spikes]
    return eff, spikes


def _base_start(highs: np.ndarray, lows: np.ndarray, top_pos: int, b: Mapping[str, Any]) -> int:
    """Where the base starts: its left-side high.

    Walking back from the highest high, the earliest session whose high is within
    `left_high_tolerance_pct` of it and was followed by a pullback of at least
    `min_swing_pct` before the top -- ABB's base began at 18,000 on 10/07, not at 18,200 on
    18/09. Without the pullback the earlier high is a step of the advance, not the left side
    of a base. The walk stops where the base from there would be deeper than a cup may be: an
    18,000 high a year ago, a 40% fall and a climb back to 18,200 are two bases, not one.
    """
    tol = float(b.get("left_high_tolerance_pct") or 0) / 100.0
    if tol <= 0:
        return top_pos
    top = float(highs[top_pos])
    limit = float(b["cup"]["max_depth_pct"]) / 100.0
    swing = float(b["min_swing_pct"]) / 100.0
    low = float(lows[top_pos:].min())           # the base's low if it starts at j
    between = math.inf                           # the lowest low strictly between j and the top
    start = top_pos
    for j in range(top_pos - 1, -1, -1):
        if j + 1 < top_pos:
            between = min(between, float(lows[j + 1]))
        low = min(low, float(lows[j]))
        if 1 - low / top > limit:
            break
        if highs[j] >= top * (1 - tol) and between <= highs[j] * (1 - swing):
            start = j
    return start


def major_contractions(highs: np.ndarray, lows: np.ndarray, min_swing_pct: float,
                       min_leg_sessions: int = 2) -> list[tuple[float, int, int]]:
    """A base's major contractions as (depth %, high position, low position), from position 0.

    From the highest high: the lowest low after it, then the highest high after that low,
    and so on -- lower highs and higher lows, so no contraction is deeper than the one
    before it. A decline shallower than `min_swing_pct` ends the walk: nothing after it can
    be deeper. A leg also spans at least `min_leg_sessions` sessions, high bar to low bar.
    Counting every 3% zigzag wiggle instead gave KSV 37 "contractions" (tester M11).
    Each next high is the LAST session at that highest price, so highs strictly fall: a penny
    stock swinging between the same two ticks (VHG, 1,600 dong, 100-dong tick) is one
    contraction, not eighteen.
    """
    n = len(highs)
    legs: list[tuple[float, int, int]] = []
    h = 0
    while h < n - 1:
        lo = h + 1 + int(np.argmin(lows[h + 1:]))
        depth = (1 - float(lows[lo]) / float(highs[h])) * 100.0
        if depth < min_swing_pct:
            break
        if lo - h + 1 >= min_leg_sessions:
            legs.append((depth, h, lo))
        if lo >= n - 1:
            break
        after = highs[lo + 1:]
        h = lo + 1 + len(after) - 1 - int(np.argmax(after[::-1]))
    return legs


def _tight_area(highs: np.ndarray, lows: np.ndarray, after: int, mid: float,
                b: Mapping[str, Any]) -> tuple[int, int] | None:
    """(high, low) positions of the final tight area after the last contraction's low.

    The pause after the last major contraction is shallower than `min_swing_pct` by
    construction. It is the final contraction -- pivot at its high, stop at its low -- when
    it has lasted `min_sessions` and sits in the upper half of the base; lower down it is a
    rebound inside the base, not the right side (HCM: 23,750, 13% under its left high).
    """
    cfg = b.get("tight_area")
    n = len(highs)
    if not cfg or after >= n - 1:
        return None
    ht = after + 1 + int(np.argmax(highs[after + 1:]))
    if n - ht < int(cfg["min_sessions"]):
        return None
    if cfg.get("upper_half", True) and float(highs[ht]) < mid:
        return None
    return ht, ht + int(np.argmin(lows[ht:]))


def _read_base_raw(frame: pd.DataFrame, exchange: str, settings: Mapping[str, Any]) -> BaseRead:
    """Describe the base the stock is in.

    The top is the highest high of the last `max_base_sessions` (an opening spike counted
    at its close); the base starts at the left-side high within `left_high_tolerance_pct`
    of it. The contractions are the major ones measured from the top, plus the final tight
    area when there is one; a VCP needs 2-6 of them, the last starting in the upper half of
    the base. Every threshold is an implementation choice.
    """
    b = settings["base"]
    vcp = b["vcp"]
    read = BaseRead()
    window = frame.tail(int(b["max_base_sessions"]))
    if len(window) < int(b["min_base_sessions"]):
        read.notes.append("chưa đủ phiên để đọc nền")
        return read

    raw_high = window["high"].to_numpy(dtype="float64")
    eff, spikes = _effective_highs(window, exchange, b)
    window = window.assign(high=eff)                 # every measure below uses the effective highs
    lows = window["low"].to_numpy(dtype="float64")
    close = window["close"].astype("float64")
    last = float(close.iloc[-1])
    dates = [d.date().isoformat() for d in window.index]
    top_pos = int(np.argmax(eff))
    top = float(eff[top_pos])
    start = _base_start(eff, lows, top_pos, b)
    base = window.iloc[start:]
    read.sessions = len(base)
    read.start_date, read.top, read.top_date = dates[start], top, dates[top_pos]
    read.spike_dates = [dates[i] for i in spikes if raw_high[i] > top]       # spikes that would have been the top
    if read.spike_dates:
        read.notes.append("đã bỏ phiên giật " + ", ".join(_dmy(d) for d in read.spike_dates)
                          + " (mở cửa ở giá cao nhất rồi đảo chiều) khi tìm đỉnh nền")
    tick = tick_size(top, exchange)

    if read.sessions < int(b["min_base_sessions"]):
        # The high is recent: either breaking out right now or extended.
        read.pivot = top + tick
        read.distance_to_pivot_pct = (last / read.pivot - 1) * 100.0
        zone = float(b["buy_zone_max_pct"])
        if read.distance_to_pivot_pct > zone:
            read.kind = "EXTENDED"
            read.notes.append("giá đã vượt đỉnh quá vùng mua")
        elif read.distance_to_pivot_pct < -2 * zone:
            # Topped within a few weeks and already fell hard: not "near the high".
            read.kind = "SHARP_PULLBACK"
            read.notes.append("vừa lập đỉnh rồi giảm mạnh trong vài tuần")
        else:
            read.kind = "NEAR_HIGH"
            read.notes.append("đỉnh mới trong vài tuần gần đây, chưa có nền đủ dài")
        return read

    low = float(base["low"].min())
    read.low, read.low_date = low, base["low"].idxmin().date().isoformat()
    read.depth_pct = (1 - low / top) * 100.0
    mid = low + (top - low) / 2

    # Contractions, measured from the top (positions relative to it).
    t_hi, t_lo = eff[top_pos:], lows[top_pos:]
    legs = major_contractions(t_hi, t_lo, float(b["min_swing_pct"]), int(b.get("min_leg_sessions", 2)))
    read.legs = [{"high_date": dates[top_pos + h], "high": float(t_hi[h]), "low_date": dates[top_pos + lo],
                  "low": float(t_lo[lo]), "depth": d, "tight": False} for d, h, lo in legs]
    tight = _tight_area(t_hi, t_lo, legs[-1][2], mid, b) if legs else None
    if tight:
        ht, lt = tight
        read.legs.append({"high_date": dates[top_pos + ht], "high": float(t_hi[ht]), "low_date": dates[top_pos + lt],
                          "low": float(t_lo[lt]), "depth": (1 - float(t_lo[lt]) / float(t_hi[ht])) * 100.0,
                          "tight": True})
    read.contractions = [leg["depth"] for leg in read.legs]

    # Pivot: the high of the last contraction -- the final tight area's when there is one
    # (skill 02 §3.1: "pivot = max(high) của nhịp co hẹp cuối"); the top when none was measured.
    if read.legs:
        right_high, last_leg_low = read.legs[-1]["high"], read.legs[-1]["low"]
        read.tightness_pct = read.legs[-1]["depth"]
    else:
        right_high, last_leg_low = top, low
    read.pivot = right_high + tick_size(right_high, exchange)
    read.distance_to_pivot_pct = (last / read.pivot - 1) * 100.0

    vol = frame["volume"].astype("float64")
    vol50 = float(vol.tail(50).mean()) if len(vol) >= 50 else float("nan")
    recent = float(vol.tail(int(vcp["dryup_window"])).mean())
    read.volume_dryup = bool(vol50 == vol50 and vol50 > 0 and recent < float(vcp["dryup_ratio"]) * vol50)

    max_stop = float(b["max_stop_pct"])
    read.stop = max(last_leg_low, read.pivot * (1 - max_stop / 100.0))

    if read.distance_to_pivot_pct > float(b["buy_zone_max_pct"]):
        read.kind = "EXTENDED"
        read.notes.append("giá đã vượt pivot quá vùng mua")
        return read
    if last < low:
        read.kind = "BROKEN"
        read.notes.append("giá thủng đáy nền")
        return read

    depths = read.contractions
    shrinking = all(
        later <= earlier + float(vcp["depth_tolerance_pct"]) for earlier, later in zip(depths, depths[1:])
    )
    upper = not vcp.get("last_contraction_upper_half") or (bool(read.legs) and read.legs[-1]["high"] >= mid)
    is_vcp = (
        int(vcp["min_contractions"]) <= len(depths) <= int(vcp["max_contractions"])
        and float(vcp["first_depth_min_pct"]) <= depths[0] <= float(vcp["first_depth_max_pct"])
        and shrinking
        and depths[-1] <= float(vcp["last_depth_max_pct"])
        and upper
    ) if depths else False

    flat, cup = b["flat_base"], b["cup"]
    # Every base of cup depth and length is measured as a cup, so its rule-by-
    # rule result can be shown whatever label wins. Order of labels: a cup with
    # a handle is the most specific pattern (and carries O'Neil's own buy
    # point), then VCP, flat base, a cup still without a handle, a plain base.
    c = None
    if read.depth_pct <= float(cup["max_depth_pct"]) and read.sessions >= int(cup["min_sessions"]):
        top_index = len(frame) - len(window) + start
        c = evaluate_cup(frame, top_index, base, settings)
        read.cup_checks = c.checks
        read.right_lip = c.right_lip
        read.right_lip_date = base.index[c.right_lip_pos].date().isoformat() if c.right_lip_pos is not None else None
        read.handle_low = c.handle_low
        read.handle_low_date = (base.index[c.handle_low_pos].date().isoformat()
                                if c.handle_low_pos is not None else None)

    if c is not None and c.ok and c.handle:
        read.kind = "CUP_HANDLE"
        # O'Neil: buy point = handle high (the right lip) + 10 cents -> one tick.
        read.pivot = c.right_lip + tick_size(c.right_lip, exchange)
        read.stop = max(c.handle_low, read.pivot * (1 - max_stop / 100.0))
    elif is_vcp:
        read.kind = "VCP"
    elif read.depth_pct <= float(flat["max_depth_pct"]) and read.sessions >= int(flat["min_sessions"]):
        read.kind = "FLAT_BASE"
        # IBD: a flat base's buy point is the highest price in the base plus
        # one tick [TRANG GỐC] -- not the right-side high a VCP uses.
        read.pivot = top + tick
        read.stop = max(low, read.pivot * (1 - max_stop / 100.0))
    elif c is not None and c.ok:
        read.kind = "CUP"
        # Cup without handle: buy point = the base's high + one tick (IBD: the left-side high).
        read.pivot = top + tick
        read.stop = read.pivot * (1 - max_stop / 100.0)
    elif read.depth_pct <= float(cup["max_depth_pct"]) and read.sessions < int(b.get("min_generic_sessions", 0) or 0):
        # Too short for a base of its own (HII: 19 sessions after a 28% fall in two weeks; R3-06).
        if (last / top - 1) * 100.0 < -2 * float(b["buy_zone_max_pct"]):
            read.kind = "SHARP_PULLBACK"
            read.notes.append(f"giảm mạnh từ đỉnh, nền mới {read.sessions} phiên "
                              f"(cần ≥ {b['min_generic_sessions']} phiên để là một nền)")
        else:
            read.kind = "NEAR_HIGH"
            read.notes.append(f"nền mới {read.sessions} phiên, chưa đủ {b['min_generic_sessions']} phiên")
    elif read.depth_pct <= float(cup["max_depth_pct"]):
        read.kind = "BASE"
        if c is not None:
            failed = [x["text"] for x in c.checks if not x["ok"] and x["rule"] != "handle"]
            if failed:
                read.notes.append("chưa đạt hình cốc: " + "; ".join(failed))
    else:
        read.kind = "TOO_DEEP"
        read.notes.append("nhịp điều chỉnh quá sâu cho một nền lành mạnh")
    read.distance_to_pivot_pct = (last / read.pivot - 1) * 100.0

    # Quality for ranking only: tight, dry, shallow, close to the pivot.
    if read.forming:
        score = {"VCP": 45.0, "CUP_HANDLE": 40.0, "FLAT_BASE": 35.0, "CUP": 30.0, "BASE": 15.0}[read.kind]
        if read.volume_dryup:
            score += 20.0
        if read.tightness_pct is not None:
            score += max(0.0, 15.0 - read.tightness_pct) * 1.0
        if read.distance_to_pivot_pct is not None and read.distance_to_pivot_pct >= -10:
            score += 20.0 * (1 - min(10.0, abs(min(read.distance_to_pivot_pct, 0.0))) / 10.0)
        read.quality = min(100.0, score)
    return read


def stop_breached_on(frame: pd.DataFrame, read: BaseRead) -> str | None:
    """The session the close went from above the stop to at or below it, while it still is
    (tester M01: the chart drew KSV's stop above the price as if it still held).

    Only a crossing AFTER the low the stop rests on counts (the last contraction's, the
    handle's or the base's low): before that low the base was still falling into shape,
    and a stop of pivot - 8% sits above any price more than 8% under the pivot. HCM read
    "stop breached 24/09" for a stop that existed only once its base had formed (R3-02);
    that is a pending stop (`stop_pending`), not a breach.
    """
    if not read.stop:
        return None
    close = frame["close"].to_numpy(dtype="float64")
    if close[-1] > read.stop:
        return None
    if read.held_by == "STOP" and read.held_since:
        return read.held_since
    anchor = max((d for d in ((read.legs[-1]["low_date"] if read.legs else None), read.low_date,
                              read.handle_low_date) if d), default=None)
    for i in range(len(close) - 1, 0, -1):
        if close[i] <= read.stop < close[i - 1]:
            day = frame.index[i].date().isoformat()
            return day if anchor is None or day > anchor else None
    return None


def _vcp_checks(read: BaseRead, mid: float, vcp: Mapping[str, Any], add: Any) -> None:
    d = read.contractions
    tight = bool(read.legs) and read.legs[-1].get("tight")
    add(f"{len(d)} nhịp co hẹp chính{' (kể cả vùng chặt cuối)' if tight else ''} "
        f"(cần {vcp['min_contractions']}–{vcp['max_contractions']})",
        int(vcp["min_contractions"]) <= len(d) <= int(vcp["max_contractions"]))
    if not d:
        return
    add(f"Nhịp đầu sâu {_pct(d[0])} (cần {vcp['first_depth_min_pct']}–{vcp['first_depth_max_pct']}%)",
        float(vcp["first_depth_min_pct"]) <= d[0] <= float(vcp["first_depth_max_pct"]))
    add("Mỗi nhịp nông hơn nhịp trước: " + " → ".join(f"{_n(x)}%" for x in d),
        all(later <= earlier + float(vcp["depth_tolerance_pct"]) for earlier, later in zip(d, d[1:])))
    add(f"Nhịp cuối {_pct(d[-1])} (cần ≤ {vcp['last_depth_max_pct']}%)", d[-1] <= float(vcp["last_depth_max_pct"]))
    if vcp.get("last_contraction_upper_half"):
        h = read.legs[-1]["high"]
        add(f"Nhịp cuối bắt đầu ở nửa trên của nền (đỉnh nhịp {_n(h)}, giữa nền {_n(mid)})", h >= mid)


def explain_base(frame: pd.DataFrame, exchange: str, settings: Mapping[str, Any]) -> dict[str, Any]:
    """The base as the screener sees it, with every rule it checked -- for drawing.

    `checks` carries (text, ok) where ok is True/False, or None for a feature
    of the textbook pattern this implementation does not test. Showing the
    unchecked items is the point: a label from a loose rule must not read as
    a textbook pattern. Each label is checked against its own rules (tester M09:
    a flat base was shown the cup-with-handle checklist).
    """
    b = settings["base"]
    read = tracked_base(frame, exchange, settings)       # the same base the monitor and the list show
    out: dict[str, Any] = {k: v for k, v in read.as_dict().items() if k in (
        "kind", "sessions", "depth_pct", "pivot", "stop", "distance_to_pivot_pct",
        "volume_dryup", "start_date", "top", "top_date", "low_date", "low", "notes",
        "right_lip_date", "right_lip", "handle_low_date", "handle_low", "held_since", "held_by",
        "legs", "spike_dates", "rules")}
    last = float(frame["close"].iloc[-1])
    out["stop_breached_on"] = stop_breached_on(frame, read)
    out["stop_breached"] = out["stop_breached_on"] is not None
    # Under the stop without having crossed it since the base formed: the stop for a buy at the pivot.
    out["stop_pending"] = bool(read.stop) and last <= read.stop and not out["stop_breached"]
    out["contractions"] = [round(x, 1) for x in read.contractions]
    # Supply overhead: the left-side high above the pivot (tester M04). Normal in a VCP or a
    # cup with handle; information from overhead_info_pct, a warning from overhead_warn_pct.
    if read.forming and read.pivot and read.top and read.top > read.pivot:
        over = (read.top / read.pivot - 1) * 100.0
        level = ("warn" if over >= float(b.get("overhead_warn_pct", 10))
                 else "info" if over >= float(b.get("overhead_info_pct", 5)) else None)
        if level:
            out["overhead"] = {"pct": round(over, 1), "top": read.top, "level": level}
    if read.top is None or read.low is None:
        out["checks"], out["swings"], out["checks_title"] = [], [], None
        return out
    mid = read.low + (read.top - read.low) / 2
    out["mid"] = round(mid, 2)

    # The drawn zigzag is the counted contractions and nothing else (tester M11).
    swings: list[dict[str, Any]] = []
    for leg in read.legs:
        for kind, t, v in (("HIGH", leg["high_date"], leg["high"]), ("LOW", leg["low_date"], leg["low"])):
            if not swings or swings[-1]["time"] != t:
                swings.append({"time": t, "value": round(v, 2), "kind": kind})
    out["swings"] = swings

    cup, flat, vcp = b["cup"], b["flat_base"], b["vcp"]
    depth = read.depth_pct or 0.0
    checks: list[dict[str, Any]] = []
    title = None

    def add(text: str, ok: bool | None) -> None:
        checks.append({"text": text, "ok": ok})

    if read.kind in ("CUP_HANDLE", "CUP"):
        for c in read.cup_checks:
            add(c["text"], c["ok"])
    elif read.kind == "FLAT_BASE":
        add(f"Độ sâu {_pct(depth)} (cần ≤ {flat['max_depth_pct']}%)", depth <= float(flat["max_depth_pct"]))
        add(f"Dài {read.sessions} phiên (cần ≥ {flat['min_sessions']})", read.sessions >= int(flat["min_sessions"]))
        add("Có nền sâu hơn ngay trước đó (nền phẳng thường là nền thứ hai)", None)
    elif read.kind == "VCP":
        _vcp_checks(read, mid, vcp, add)
    elif read.kind == "BASE":
        # A plain base passed no pattern: say which pattern's rules it was held against.
        if read.cup_checks:
            title = "Chưa đạt mẫu hình chuẩn — kiểm tra theo quy tắc cốc"
            for c in read.cup_checks:
                add(c["text"], c["ok"])
        else:
            title = "Chưa đạt mẫu hình chuẩn — kiểm tra theo quy tắc VCP"
            _vcp_checks(read, mid, vcp, add)
    elif read.kind == "TOO_DEEP":
        add(f"Độ sâu {_pct(depth)} (cần ≤ {cup['max_depth_pct']}%, O'Neil)", False)
    if read.forming or read.kind == "TOO_DEEP":
        add("Khối lượng cạn ở vùng pivot", read.volume_dryup)
    out["checks"], out["checks_title"] = checks, title
    return out


# ---------------------------------------------------------------------------
# Market-direction gate (skill 02 §3.5) -- simplified [TRIỂN KHAI]
# ---------------------------------------------------------------------------

FTD_WAIT, FTD_ATTEMPT, FTD_ACTIVE = "WAIT_LOW", "RALLY_ATTEMPT", "FTD"
FTD_STATE_TEXT = {FTD_WAIT: "Chờ đáy", FTD_ATTEMPT: "Nỗ lực hồi phục", FTD_ACTIVE: "FTD còn hiệu lực"}


def follow_through(index: pd.DataFrame, settings: Mapping[str, Any]) -> dict[str, Any]:
    """IBD's rally attempt and follow-through day, walked session by session.

    The FTD criteria are IBD's [TRANG GỐC]; the reference low and the state
    machine are [TRIỂN KHAI] (congthuc.md §11, reviewed 2026-10-06):

        WAIT_LOW -> RALLY_ATTEMPT -> FTD -> (failed / invalidated) -> WAIT_LOW

    * The reference low is the lowest low since the cycle began: the start of
      the window, or the session an FTD failed or lost its validity. A new low
      cancels day 1 and waits again.
    * Day 1 is the first up close; from `earliest_day` on, a close up at least
      `min_gain_pct` on higher volume than the day before is the FTD.
    * An FTD fails when the index undercuts the reference low (`undercut_on`:
      the session low, or the close) and loses its validity once
      `pressure_distribution_days` distribution days still counted (with their
      expiry) have accumulated since it. Either way a new cycle starts there,
      so a later correction with a higher low is still recognised.
    Uses the index low when available, otherwise the close.
    """
    m = settings["market"]
    cfg = m["ftd"]
    n_look = int(cfg["lookback_sessions"])
    offset = max(0, len(index) - n_look)
    frame = index.iloc[offset:].reset_index(drop=True)
    if len(frame) < 10:
        return {"state": FTD_WAIT, "date": None, "rally_start": None, "failed": False, "last_ftd": None}
    close = frame["close"].astype("float64").to_numpy()
    vol_now, vol_prev = index_volume_pair(index, settings)
    vol, prev_vol = vol_now.to_numpy()[offset:], vol_prev.to_numpy()[offset:]
    low = frame["low"].astype("float64").to_numpy() if "low" in frame else close
    low = np.where(np.isnan(low), close, low)
    dates = [str(d)[:10] for d in (frame["trade_date"] if "trade_date" in frame else frame.index)]
    earliest, gain = int(cfg["earliest_day"]), float(cfg["min_gain_pct"])
    undercut = low if str(cfg.get("undercut_on", "low")) == "low" else close
    max_dist = int(m["pressure_distribution_days"])
    # distribution days with their expiry, measured on the whole history, in this window's row numbers
    expiry = {d - offset: e - offset for d, e in distribution_expiry(index, settings).items() if d >= offset}

    def dist_since(ftd: int, i: int) -> int:
        return sum(1 for d, e in expiry.items() if ftd < d <= i < e)

    state, ref_low, low_i = FTD_WAIT, low[0], 0
    day1: int | None = None
    ftd: int | None = None
    last_ftd: dict[str, Any] | None = None
    for i in range(1, len(frame)):
        if state == FTD_ACTIVE:
            since = dist_since(ftd, i)
            if undercut[i] < ref_low or since >= max_dist:
                failed = undercut[i] < ref_low
                last_ftd = {"date": dates[ftd], "gain_pct": float(round((close[ftd] / close[ftd - 1] - 1) * 100.0, 2)),
                            "outcome": "failed" if failed else "invalidated", "on": dates[i],
                            "distribution_since": since}
                state, ref_low, low_i, day1, ftd = FTD_WAIT, low[i], i, None, None
            continue
        if low[i] < ref_low:
            ref_low, low_i, day1, state = low[i], i, None, FTD_WAIT
            continue
        if day1 is None:
            if close[i] > close[i - 1]:
                day1, state = i, FTD_ATTEMPT
        else:
            day = i - day1 + 1
            change = (close[i] / close[i - 1] - 1) * 100.0
            if day >= earliest and change >= gain and vol[i] > prev_vol[i]:
                ftd, state = i, FTD_ACTIVE

    last = len(frame) - 1
    since_now = dist_since(ftd, last) if ftd is not None else None
    ended = ftd is None and last_ftd is not None
    return {
        "state": state,
        "state_text": FTD_STATE_TEXT[state],
        "rally_low_date": dates[low_i],
        "rally_start": dates[day1] if day1 is not None else None,
        "rally_day": (len(frame) - day1) if day1 is not None else None,
        "date": dates[ftd] if ftd is not None else None,
        "gain_pct": float(round((close[ftd] / close[ftd - 1] - 1) * 100.0, 2)) if ftd is not None else None,
        "distribution_since": since_now,
        "pressured": bool(ended and last_ftd["outcome"] == "invalidated"),
        "failed": bool(ended and last_ftd["outcome"] == "failed"),
        "failed_date": last_ftd["date"] if ended and last_ftd["outcome"] == "failed" else None,
        "last_ftd": last_ftd if ended else None,
    }


def index_volume_pair(index: pd.DataFrame, settings: Mapping[str, Any]) -> tuple[pd.Series, pd.Series]:
    """Each session's volume and the previous session's, on one definition per pair.

    `index_volume: matched` compares matched trading (the boards' figure, DNSE
    market-index `contauctAccTrdVol`, column `matched_volume`); a pair where
    either session lacks it is compared on the OHLC volume for both, never one
    of each. `total` -- or a frame without the column -- uses the OHLC volume
    throughout (it adds put-through deals). congthuc.md section 11.
    """
    total = index["volume"].astype("float64")
    if str(settings["market"].get("index_volume", "total")) != "matched" or "matched_volume" not in index:
        return total, total.shift(1)
    matched = index["matched_volume"].astype("float64")
    both = matched.notna() & matched.shift(1).notna()
    return matched.where(both, total), matched.shift(1).where(both, total.shift(1))


def index_volume_basis(index: pd.DataFrame, settings: Mapping[str, Any], sessions: int) -> dict[str, Any]:
    """Which volume the last `sessions` comparisons used: {"basis", "fallback_sessions", "matched_since"}."""
    want = str(settings["market"].get("index_volume", "total"))
    if want != "matched" or "matched_volume" not in index:
        return {"basis": "total", "fallback_sessions": 0, "matched_since": None}
    matched = index["matched_volume"].astype("float64")
    both = (matched.notna() & matched.shift(1).notna()).iloc[-int(sessions):]
    dates = index["trade_date"] if "trade_date" in index else pd.Series(index.index, index=index.index)
    first = dates[matched.notna()]
    return {"basis": "matched", "fallback_sessions": int((~both).sum()),
            "matched_since": str(first.iloc[0])[:10] if len(first) else None}


def distribution_days(index: pd.DataFrame, settings: Mapping[str, Any]) -> pd.Series:
    """True on each session the index closed down at least `distribution_min_drop_pct`
    on higher volume than the session before (IBD's distribution day), the volume
    per `index_volume_pair`."""
    close = index["close"].astype("float64")
    vol, prev = index_volume_pair(index, settings)
    change = close.pct_change(fill_method=None) * 100.0
    return (change <= -float(settings["market"]["distribution_min_drop_pct"])) & (vol > prev)


def distribution_expiry(index: pd.DataFrame, settings: Mapping[str, Any]) -> dict[int, int]:
    """Row position of each distribution day -> the first row it no longer counts on.

    IBD drops a distribution day after `distribution_window` sessions, or sooner
    once the index trades `distribution_expire_gain_pct` above that day's close
    [TRANG GỐC IBD; measured on the session high per `distribution_expire_on`,
    THỨ CẤP] -- the selling that day has been absorbed. A row number equal to
    len(index) means it still counts on the last session.
    """
    m = settings["market"]
    window = int(m["distribution_window"])
    gain = float(m.get("distribution_expire_gain_pct", 0) or 0) / 100.0
    close = index["close"].astype("float64").to_numpy()
    probe = close
    if str(m.get("distribution_expire_on", "high")) == "high" and "high" in index:
        high = index["high"].astype("float64").to_numpy()
        probe = np.where(np.isnan(high), close, high)        # a missing high falls back to the close
    flags = distribution_days(index, settings).to_numpy()
    n = len(close)
    out: dict[int, int] = {}
    for d in np.flatnonzero(flags):
        end = min(n, int(d) + window)
        if gain > 0:
            above = np.flatnonzero(probe[d + 1:end] >= close[d] * (1 + gain))
            if len(above):
                end = int(d) + 1 + int(above[0])
        out[int(d)] = end
    return out


def active_distribution(index: pd.DataFrame, settings: Mapping[str, Any], at: int | None = None) -> pd.Series:
    """True on each distribution day still counted on row `at` (default: the last session)."""
    at = len(index) - 1 if at is None else at
    alive = pd.Series(False, index=index.index)
    for d, end in distribution_expiry(index, settings).items():
        if d <= at < end:
            alive.iloc[d] = True
    return alive


def active_distribution_count(index: pd.DataFrame, settings: Mapping[str, Any]) -> pd.Series:
    """Distribution days still counted on each session."""
    counts = np.zeros(len(index), dtype=int)
    for d, end in distribution_expiry(index, settings).items():
        counts[d:end] += 1
    return pd.Series(counts, index=index.index)


def market_status(index: pd.DataFrame, settings: Mapping[str, Any]) -> dict[str, Any]:
    m = settings["market"]
    if len(index) < 200:
        return {"status": "UNKNOWN", "reason": "chưa đủ 200 phiên dữ liệu chỉ số"}
    close = index["close"].astype("float64")
    last = float(close.iloc[-1])
    ma50, ma200 = float(ind.sma(close, 50).iloc[-1]), float(ind.sma(close, 200).iloc[-1])
    dd_count = int(active_distribution_count(index, settings).iloc[-1])
    pressure = dd_count >= int(m["pressure_distribution_days"])
    if last < ma200:
        status = "CORRECTION"
    elif last > ma50 > ma200 and not pressure:
        status = "UPTREND"
    elif pressure or last < ma50:
        status = "UNDER_PRESSURE"
    else:
        status = "MIXED"
    return {
        "status": status, "index": m["index"], "close": round(last, 2),
        "ma50": round(ma50, 2), "ma200": round(ma200, 2),
        "distribution_days": dd_count, "window": int(m["distribution_window"]),
        "volume_basis": index_volume_basis(index, settings, int(m["distribution_window"])),
        "ftd": follow_through(index, settings) if "ftd" in m else None,
        "note": "Ngưỡng ngày phân phối / FTD chưa kiểm định cho VN [TRIỂN KHAI]",
    }


# ---------------------------------------------------------------------------
# Ranking
# ---------------------------------------------------------------------------

def composite_score(rs: float | None, passed: int, below_high_pct: float | None,
                    base_quality: float, settings: Mapping[str, Any], fundamentals: float | None = None) -> float:
    w = dict(settings["ranking_weights"])
    parts = {
        "rs": (rs or 0.0),
        "trend_template": passed / 8 * 100.0,
        "near_high": max(0.0, 100.0 - 4.0 * (below_high_pct or 100.0)),
        "base_quality": base_quality,
    }
    if fundamentals is not None:
        parts["fundamentals"] = fundamentals
    used = {k: w[k] for k in parts if k in w}
    total = sum(used.values()) or 1.0
    return round(sum(parts[k] * used[k] for k in used) / total, 2)


# ---------------------------------------------------------------------------
# Whole-universe run
# ---------------------------------------------------------------------------

@dataclass
class Candidate:
    ticker: str
    exchange: str
    close: float
    liquidity_vnd: float
    rs_rating: float | None
    criteria: dict[str, Criterion]
    base: BaseRead
    ma200_rising_preferred: bool | None
    group: str | None = None
    score: float = 0.0
    rs_tier: str | None = None
    setup: dict[str, Any] = field(default_factory=dict)   # trend / setup / entry / status (setup_status)
    fundamentals: dict[str, Any] = field(default_factory=dict)
    demoted: str | None = None                           # why a technical A/B was kept off the main list
    industry: dict[str, Any] = field(default_factory=dict)  # industry strength (display only, weight 0)
    themes: list[dict[str, Any]] = field(default_factory=list)  # user themes it belongs to, hottest first

    @property
    def passed(self) -> int:
        return sum(1 for c in self.criteria.values() if c.passed)

    @property
    def failed(self) -> list[str]:
        return [k for k, c in self.criteria.items() if not c.passed]


@dataclass
class ScreenResult:
    run_id: str
    as_of: date
    funnel: dict[str, int]
    exclusions: dict[str, int]
    market: dict[str, Any]
    members: list[Candidate]
    # Liquid tickers excluded only because their series is flagged -- shown so
    # a data problem never silently hides a candidate. (ticker, exchange, value)
    flagged_liquid: list[tuple[str, str, float]] = field(default_factory=list)
    industries: list[dict[str, Any]] = field(default_factory=list)   # strongest first
    leaders: list[dict[str, Any]] = field(default_factory=list)      # most-traded per industry
    themes: list[dict[str, Any]] = field(default_factory=list)       # user themes, strongest first
    rs_all: dict[str, float] = field(default_factory=dict)           # RS of every liquid ticker (for off-list names)


def _load_universe_frames(as_of: date, sessions: int, path: Path | None) -> tuple[dict[str, pd.DataFrame], dict[str, str], set[str]]:
    since = (as_of - timedelta(days=int(sessions * 7 / 5) + 30)).isoformat()
    with connect(path) as conn:
        tickers = {r[0]: r[1] for r in conn.execute("SELECT ticker, exchange FROM tickers WHERE active = 1")}
        stale = {r[0] for r in conn.execute(
            "SELECT scope_key FROM data_freshness WHERE dataset = 'prices_daily' AND is_stale = 1")}
        rows = conn.execute(
            "SELECT ticker, trade_date, open, high, low, close, volume FROM prices_daily "
            "WHERE trade_date >= ? AND trade_date <= ? ORDER BY ticker, trade_date",
            (since, as_of.isoformat()),
        ).fetchall()
    df = pd.DataFrame(rows, columns=["ticker", "trade_date", "open", "high", "low", "close", "volume"])
    frames: dict[str, pd.DataFrame] = {}
    for t, g in df.groupby("ticker", sort=False):
        if t in tickers:
            g = g.set_index(pd.to_datetime(g["trade_date"])).drop(columns=["ticker", "trade_date"])
            frames[t] = g.astype("float64")
    return frames, tickers, stale


def liquid_tickers(as_of: date | None = None, path: Path | None = None,
                   thresholds: Mapping[str, Any] | None = None) -> list[str]:
    """Tickers that clear the data and liquidity gates today (the screen's working set)."""
    settings = screen_settings(thresholds)
    as_of = as_of or datetime.now(timezone.utc).date()
    frames, exchanges, stale = _load_universe_frames(as_of, int(settings["min_history_sessions"]) + 120, path)
    floors = settings["liquidity"]["min_value_vnd"]
    out = []
    for t, f in frames.items():
        if t in stale or len(f) < int((settings.get("recent_listings") or {}).get("min_sessions", 252)):
            continue
        value, active = liquidity(f, settings)
        floor = float(floors.get(exchanges[t].upper(), max(floors.values())))
        if value is not None and value >= floor and active >= float(settings["liquidity"]["min_active_session_ratio"]):
            out.append(t)
    return sorted(out)


def grade_ticker(ticker: str, settings: Mapping[str, Any], path: Path | None):
    from src.quant.fundamentals import grade_ticker as _grade  # noqa: PLC0415 - avoids an import cycle
    return _grade(ticker, settings, path)


def run_screen(as_of: date | None = None, persist: bool = True, path: Path | None = None,
               thresholds: Mapping[str, Any] | None = None) -> ScreenResult:
    settings = screen_settings(thresholds)
    as_of = as_of or datetime.now(timezone.utc).date()
    frames, exchanges, stale = _load_universe_frames(as_of, int(settings["min_history_sessions"]) + 120, path)
    # Only ~1.5 years are loaded; the full stored count tells a recent listing from a stock that
    # trades on few days (the tester read "history < 252" as "only ~376 sessions stored").
    with connect(path) as conn:
        stored = dict(conn.execute("SELECT ticker, COUNT(*) FROM prices_daily WHERE trade_date <= ? GROUP BY ticker",
                                   (as_of.isoformat(),)).fetchall())

    funnel: dict[str, int] = {"universe": len(exchanges)}
    exclusions: dict[str, int] = {}

    def drop(reason: str) -> None:
        exclusions[reason] = exclusions.get(reason, 0) + 1

    # 1. data gate
    min_hist = int(settings["min_history_sessions"])
    max_age = timedelta(days=int(settings["max_last_bar_age_days"]))
    waivers = {str(k).upper() for k in (settings.get("gap_waivers") or {})}
    ipo_cfg = settings.get("recent_listings")
    recent_listings: dict[str, pd.DataFrame] = {}
    floors = settings["liquidity"]["min_value_vnd"]
    flagged_liquid: list[tuple[str, str, float]] = []
    clean: dict[str, pd.DataFrame] = {}
    for t, ex in exchanges.items():
        f = frames.get(t)
        if f is None or f.empty:
            drop("không có dữ liệu giá")
        elif t in stale and t not in waivers:
            drop("dữ liệu bị đánh dấu stale")
            value, _ = liquidity(f, settings)
            if value is not None and value >= float(floors.get(ex.upper(), max(floors.values()))):
                flagged_liquid.append((t, ex, value))
        elif len(f) < min_hist and stored.get(t, 0) >= min_hist:
            drop(f"giao dịch thưa / tạm ngừng: < {min_hist} phiên có giá trong ~1,5 năm (lịch sử dài hơn)")
        elif len(f) < min_hist:
            drop(f"mới niêm yết: lịch sử < {min_hist} phiên")
            if ipo_cfg and len(f) >= int(ipo_cfg["min_sessions"]) and as_of - f.index[-1].date() <= max_age:
                recent_listings[t] = f
        elif as_of - f.index[-1].date() > max_age:
            drop("phiên cuối quá cũ (tạm ngừng / hủy niêm yết?)")
        else:
            clean[t] = f
    funnel["data_ok"] = len(clean)

    # 2. liquidity
    liquid: dict[str, tuple[pd.DataFrame, float]] = {}
    min_active = float(settings["liquidity"]["min_active_session_ratio"])
    for t, f in clean.items():
        value, active = liquidity(f, settings)
        floor = float(floors.get(exchanges[t].upper(), max(floors.values())))
        if value is None or value < floor:
            drop("thanh khoản dưới ngưỡng")
        elif active < min_active:
            drop("quá nhiều phiên không giao dịch")
        else:
            liquid[t] = (f, value)
    funnel["liquid"] = len(liquid)

    # 3. RS across every ticker with clean data, liquid or not: IBD ranks the whole market, so an
    # RS of 90 means the top 10% of it, not of the ~190 liquid names (user decision 2026-10-11).
    rs_cfg = settings["rs"]
    raw = {t: s for t, f in clean.items()
           if (s := rs_raw_score(f["close"], str(rs_cfg["variant"]), rs_cfg["periods"])) is not None}
    ratings = rs_ratings(raw)
    funnel["rs_universe"] = len(ratings)

    # 4-5. Trend Template and base
    gb = settings["group_b"]
    candidates: list[Candidate] = []
    demoted = 0
    broken_demoted = 0
    broken_names: list[str] = []
    for t, (f, value) in liquid.items():
        rs = ratings.get(t)
        criteria = trend_template(f, rs, settings)
        base = read_base(f, exchanges[t], settings)
        c = Candidate(t, exchanges[t], float(f["close"].iloc[-1]), value, rs, criteria, base,
                      ma200_rising_preferred(f, settings))
        fund = grade_ticker(t, settings, path) if "fundamentals" in settings else None
        c.fundamentals = fund.as_dict() if fund else {}
        c.score = composite_score(rs, c.passed, criteria["TT7"].value, base.quality, settings,
                                  fundamentals=fund.score if fund else None)
        if c.passed == 8:
            c.group = GROUP_A
        elif (c.passed >= int(gb["min_criteria_passed"])
              and all(criteria[k].passed for k in gb["must_pass"])
              and (base.forming or not gb["require_base"])):
            c.group = GROUP_B
        # A chart that just broke leaves the main list whatever the 8 criteria say.
        if c.group in (GROUP_A, GROUP_B) and base.kind in set(settings.get("main_exclude_bases") or ()):
            c.demoted = (f"nhóm {c.group} bị loại khỏi danh sách chính: gãy chart "
                         f"({'giảm mạnh gần đây' if base.kind == 'SHARP_PULLBACK' else 'thủng nền'})")
            c.group = None
            broken_demoted += 1
            broken_names.append(c.ticker)
        # Fundamental gate: a good chart on deteriorating profits stays out of the main list.
        if (c.group in (GROUP_A, GROUP_B) and fund is not None and fund.grade == "FAIL"
                and settings["fundamentals"].get("fail_leaves_main_list", True)):
            c.demoted = f"nhóm {c.group} bị loại khỏi danh sách chính: cơ bản FAIL ({'; '.join(fund.reasons)})"
            c.group = None
            demoted += 1
        candidates.append(c)
    funnel["trend_template_8of8"] = sum(1 for c in candidates if c.group == GROUP_A)
    funnel["near_pass_group_b"] = sum(1 for c in candidates if c.group == GROUP_B)
    funnel["broken_chart_demoted"] = broken_demoted
    if broken_names:
        exclusions["gãy chart, rời danh sách chính: " + ", ".join(sorted(broken_names))] = len(broken_names)
    funnel["fundamental_fail_demoted"] = demoted

    # 6. rank: A first, then B, at most max_members
    cap = int(settings["max_members"])
    # Within a group, stronger leadership tiers first, then the composite score.
    for c in candidates:
        c.rs_tier = rs_tier(c.rs_rating, settings)
    lead_key = lambda c: (RS_TIER_ORDER[c.rs_tier], -c.score)  # noqa: E731
    ranked_a = sorted((c for c in candidates if c.group == GROUP_A), key=lead_key)
    ranked_b = sorted((c for c in candidates if c.group == GROUP_B), key=lead_key)
    members = (ranked_a + ranked_b)[:cap]

    # Recent listings (Minervini's IPO / primary base): liquid, holding within
    # the Trend Template's distance of their post-listing high. They lead group C.
    ipo_members: list[Candidate] = []
    recent_liquid: list[Candidate] = []     # every liquid recent listing, for the industry-leader ranking
    if ipo_cfg:
        within = float(settings["trend_template"]["within_52w_high_pct"])
        for t, f in recent_listings.items():
            value, active = liquidity(f, settings)
            floor = float(floors.get(exchanges[t].upper(), max(floors.values())))
            if value is None or value < floor or active < min_active:
                continue
            last = float(f["close"].iloc[-1])
            high = float(f["high"].max())
            below_high = (1 - last / high) * 100.0 if high else None
            near_high = below_high is not None and below_high <= within
            criteria = {code: Criterion(code, False, None, f"cần ≥ {min_hist} phiên (mới {len(f)})", assessable=False)
                        for code in CRITERIA}
            criteria["TT7"] = Criterion("TT7", near_high, below_high, f"<= {within:.0f}% dưới đỉnh sau niêm yết")
            base = read_base(f, exchanges[t], settings)
            base.notes.insert(0, f"mới niêm yết: {len(f)} phiên dữ liệu")
            c = Candidate(t, exchanges[t], last, value, None, criteria, base, None)
            c.score = composite_score(None, 1, below_high, base.quality, settings)
            recent_liquid.append(c)
            if not near_high:
                drop("mới niêm yết nhưng cách đỉnh sau niêm yết > {:.0f}%".format(within))
                continue
            c.group = GROUP_C
            ipo_members.append(c)
        ipo_members.sort(key=lambda c: -c.score)
    funnel["recent_listings"] = len(ipo_members)
    members += ipo_members[: max(0, cap - len(members))]

    # Industry and theme strength over the whole liquid universe. Display only
    # (weight 0), except that hot-theme members lead group C.
    industries: list[dict[str, Any]] = []
    leaders: list[dict[str, Any]] = []
    theme_list: list[dict[str, Any]] = []
    stats: dict[str, Any] = {}
    of: dict[str, str] = {}
    theme_stats: dict[str, Any] = {}
    frames_for_industry = {t: f for t, (f, _) in liquid.items()} | {c.ticker: recent_listings[c.ticker]
                                                                     for c in recent_liquid}
    ab_groups = {c.ticker: c.group for c in members}
    if "industry" in settings:
        from src.quant.industry import industry_strength, load_industries  # noqa: PLC0415

        stats, of = industry_strength(frames_for_industry, raw, ab_groups, load_industries(path), settings)
        funnel["classified_liquid"] = len(of)
    if "themes" in settings:
        from src.quant.themes import load_themes, theme_strength  # noqa: PLC0415

        means = {code: st.mean_rs_raw for code, st in stats.items()
                 if st.rs_percentile is not None and st.mean_rs_raw is not None}
        theme_stats = theme_strength(load_themes(), frames_for_industry, raw, ab_groups, means, settings)
    hot_members = {t for st in theme_stats.values() if st.hot for t in st.members}

    # Fill the rest of the list with names that are in an uptrend but not
    # SEPA-qualified, labelled C. Not padded: fewer qualifying, shorter list.
    gc = settings.get("group_c")
    if gc and len(members) < cap:
        excluded = set(gc.get("exclude_bases") or ())
        required = list(gc.get("require_criteria") or ())
        hot_first = bool(gc.get("hot_themes_first"))
        fill = sorted(
            (c for c in candidates
             if c.group is None
             and c.rs_rating is not None and c.rs_rating >= float(gc["min_rs_rating"])
             and c.passed >= int(gc["min_criteria_passed"])
             and all(c.criteria[k].passed for k in required)
             and c.base.kind not in excluded),
            key=lambda c: (not (hot_first and c.ticker in hot_members), -c.score),
        )[: cap - len(members)]
        for c in fill:
            c.group = GROUP_C
        members += fill
    funnel["fill_group_c"] = sum(1 for c in members if c.group == GROUP_C) - funnel["recent_listings"]
    funnel["hot_theme_in_c"] = sum(1 for c in members if c.group == GROUP_C and c.ticker in hot_members)
    funnel["members"] = len(members)

    if stats:
        lead = float(settings["industry"]["leading_percentile"])
        for c in members:
            if c.ticker in of:
                c.industry = stats[of[c.ticker]].as_dict(lead)
        industries = sorted((st.as_dict(lead) for st in stats.values()),
                            key=lambda d: (d["rs_percentile"] is None, -(d["rs_percentile"] or 0), -d["count"]))
        leaders = industry_leaders(candidates + recent_liquid, members, stats, of, settings)
    if theme_stats:
        from src.quant.themes import themes_of  # noqa: PLC0415

        for c in members:
            c.themes = themes_of(c.ticker, theme_stats)
        theme_list = sorted((st.as_dict() for st in theme_stats.values()),
                            key=lambda d: (d["rs_percentile"] is None, -(d["rs_percentile"] or 0)))

    # market gate
    idx_symbol = settings["market"]["index"]
    with connect(path) as conn:
        idx_rows = conn.execute(
            "SELECT trade_date, close, volume, low, high, matched_volume FROM index_daily WHERE symbol = ? AND trade_date <= ? "
            "ORDER BY trade_date DESC LIMIT 400", (idx_symbol, as_of.isoformat())).fetchall()
    idx = pd.DataFrame(list(reversed(idx_rows)), columns=["trade_date", "close", "volume", "low", "high", "matched_volume"])
    market = market_status(idx, settings)
    for c in members:
        # Groups were decided on today's base; what is shown and stored is the held one after a
        # breakout or a stop breach, so the list, the events and the detail agree (tracked_base).
        f = clean.get(c.ticker, recent_listings.get(c.ticker))
        if f is not None:
            held = tracked_base(f, c.exchange, settings)
            if held.held_since:
                c.base = held
        c.setup = assess(c.group, c.base.kind, c.base.distance_to_pivot_pct, settings, market,
                         stop_breached=f is not None and stop_breached_on(f, c.base) is not None)

    last_session = max((f.index[-1].date() for f in clean.values()), default=as_of)
    result = ScreenResult(uuid.uuid4().hex[:16], last_session, funnel, exclusions, market, members,
                          sorted(flagged_liquid, key=lambda x: -x[2]), industries, leaders, theme_list, ratings)
    if persist:
        _persist(result, settings, path)
    return result


def industry_leaders(candidates: Sequence["Candidate"], members: Sequence["Candidate"],
                     stats: Mapping[str, Any], of: Mapping[str, str],
                     settings: Mapping[str, Any]) -> list[dict[str, Any]]:
    """The most-traded liquid stocks of each classified industry, strongest industry first.

    Ranked by 20-session traded value, which stands in for size: market
    capitalisation is not in the database. These are industry bellwethers to
    watch (SSI, VCK... for brokers), not SEPA candidates.
    """
    n = int(settings["industry"].get("leaders_per_industry", 0))
    if n <= 0:
        return []
    group = {c.ticker: c.group for c in members}
    by_industry: dict[str, list[Candidate]] = {}
    for c in candidates:
        if c.ticker in of:
            by_industry.setdefault(of[c.ticker], []).append(c)
    order = sorted(by_industry, key=lambda code: -(stats[code].rs_percentile or 0))
    out = []
    for code in order:
        for rank, c in enumerate(sorted(by_industry[code], key=lambda c: -c.liquidity_vnd)[:n], start=1):
            out.append({
                "industry_code": code, "industry": stats[code].name,
                "industry_rs": stats[code].rs_percentile, "rank": rank, "ticker": c.ticker,
                "exchange": c.exchange, "grp": group.get(c.ticker), "close": c.close,
                "liquidity_vnd": c.liquidity_vnd, "liquidity_tier": liquidity_tier(c.liquidity_vnd, settings),
                "rs_rating": c.rs_rating,
                "rs_tier": rs_tier(c.rs_rating, settings), "tt_passed": c.passed,
                "missing": [k for k, v in c.criteria.items() if not v.passed and v.assessable],
                "new_listing": any(not v.assessable for v in c.criteria.values()),
                "base": c.base.kind, "pivot": c.base.pivot,
                "distance_to_pivot_pct": c.base.distance_to_pivot_pct,
                "fundamental_grade": (c.fundamentals or {}).get("grade"),
            })
    return out


def _persist(result: ScreenResult, settings: Mapping[str, Any], path: Path | None) -> None:
    init_db(path)
    bands = load_config().thresholds.get("exchange_price_limit_pct", {}) or {}
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO screen_runs (run_id, created_at, as_of_date, params_json, funnel_json, market_json, note) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (result.run_id, now, result.as_of.isoformat(), json.dumps(settings, default=str),
             json.dumps({"funnel": result.funnel, "exclusions": result.exclusions,
                         "industries": result.industries, "themes": result.themes,
                         "rs_ratings": result.rs_all}, ensure_ascii=False),
             json.dumps(result.market, ensure_ascii=False),
             "Ứng viên nghiên cứu theo SEPA; không phải khuyến nghị mua."),
        )
        for rank, c in enumerate(result.members, start=1):
            band = bands.get(c.exchange.upper())
            b = c.base
            risk = (b.pivot - b.stop) / b.pivot * 100.0 if b.pivot and b.stop else None
            conn.execute(
                "INSERT INTO screen_members (run_id, ticker, grp, rank, score, exchange, close, rs_rating, "
                "criteria_json, base_json, pivot, stop, risk_to_stop_pct, locked_worst_case_pct, liquidity_vnd) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (result.run_id, c.ticker, c.group, rank, c.score, c.exchange, c.close, c.rs_rating,
                 json.dumps({k: v.as_dict() for k, v in c.criteria.items()}
                            | {"ma200_rising_4_5_months": c.ma200_rising_preferred}, ensure_ascii=False),
                 json.dumps(b.as_dict() | {"rs_tier": c.rs_tier, "setup": c.setup, "fundamentals": c.fundamentals,
                                           "demoted": c.demoted, "industry": c.industry, "themes": c.themes,
                                           "liquidity_tier": liquidity_tier(c.liquidity_vnd, settings)}, ensure_ascii=False),
                 b.pivot, b.stop, None if risk is None else round(risk, 2),
                 locked_worst_case_pct(float(band)) if band is not None else None,
                 c.liquidity_vnd),
            )
        for d in result.leaders:
            conn.execute(
                "INSERT INTO screen_industry_leaders (run_id, industry_code, industry, rank, ticker, grp, "
                "liquidity_vnd, detail_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (result.run_id, d["industry_code"], d["industry"], d["rank"], d["ticker"], d["grp"],
                 d["liquidity_vnd"], json.dumps(d, ensure_ascii=False, default=float)))


def latest_focus_list(path: Path | None = None) -> list[str]:
    """Tickers monitored until the next screen: the latest run's members, then its industry leaders."""
    with connect(path) as conn:
        row = conn.execute("SELECT run_id FROM screen_runs ORDER BY created_at DESC LIMIT 1").fetchone()
        if not row:
            return []
        out = [r[0] for r in conn.execute(
            "SELECT ticker FROM screen_members WHERE run_id = ? ORDER BY rank", (row[0],))]
        for (t,) in conn.execute("SELECT ticker FROM screen_industry_leaders WHERE run_id = ? "
                                 "ORDER BY industry_code, rank", (row[0],)):
            if t not in out:
                out.append(t)
        return out
