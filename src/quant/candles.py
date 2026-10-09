"""Candlestick pattern of the latest session(s). Deterministic geometry.

Definitions follow the skill reference
`.claude/skills/sepa-vn-screening/references/03-candlesticks.md` (Nison,
Bulkowski, TA-Lib defaults), with every threshold in `candles` in
config/thresholds.yaml. A reversal pattern only counts after the prior trend
it reverses; every pattern needs confirmation by the next session, and the
Bulkowski statistics quoted are US-market samples, untested in Vietnam.

Sessions locked at the ceiling or floor (one price all day, or a close on the
band with no range above / below) are not classified: their shape reflects the
price limit, not supply and demand.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from src.quant import indicators as ind


@dataclass
class Pattern:
    key: str
    name: str
    bias: str             # tăng | giảm | lưỡng lự
    bars: int
    stat: str             # Bulkowski: behaviour % / overall rank, or ""
    note: str = "cần phiên sau xác nhận"


@dataclass
class _Bar:
    o: float
    h: float
    l: float
    c: float

    @property
    def body(self) -> float: return abs(self.c - self.o)
    @property
    def rng(self) -> float: return self.h - self.l
    @property
    def upper(self) -> float: return self.h - max(self.o, self.c)
    @property
    def lower(self) -> float: return min(self.o, self.c) - self.l
    @property
    def white(self) -> bool: return self.c > self.o
    @property
    def black(self) -> bool: return self.c < self.o
    @property
    def mid(self) -> float: return (self.o + self.c) / 2
    @property
    def top(self) -> float: return max(self.o, self.c)
    @property
    def bottom(self) -> float: return min(self.o, self.c)


def _locked(frame: pd.DataFrame, i: int, band_pct: float | None) -> bool:
    row = frame.iloc[i]
    if row["high"] == row["low"]:
        return True
    if band_pct is None or i == 0:
        return False
    prev = float(frame["close"].iloc[i - 1])
    move = (float(row["close"]) / prev - 1) * 100 if prev else 0.0
    at_ceiling = move >= band_pct * 0.97 and row["close"] == row["high"]
    at_floor = move <= -band_pct * 0.97 and row["close"] == row["low"]
    return bool(at_ceiling or at_floor)


def _trend(close: pd.Series, end: int, cfg: Mapping[str, Any]) -> str:
    """Trend into bar `end` (exclusive): close vs SMA and the SMA's slope. [TRIỂN KHAI]"""
    n, slope_n = int(cfg["trend_sma"]), int(cfg["trend_slope_sessions"])
    s = ind.sma(close.iloc[:end], n)
    if len(s) < slope_n + 1 or np.isnan(s.iloc[-1]) or np.isnan(s.iloc[-1 - slope_n]):
        return "flat"
    last = float(close.iloc[end - 1])
    if last > s.iloc[-1] and s.iloc[-1] > s.iloc[-1 - slope_n]:
        return "up"
    if last < s.iloc[-1] and s.iloc[-1] < s.iloc[-1 - slope_n]:
        return "down"
    return "flat"


def latest_pattern(frame: pd.DataFrame, cfg: Mapping[str, Any], band_pct: float | None = None) -> dict[str, Any] | None:
    """The most specific pattern ending on the last session, or None."""
    if len(frame) < 30:
        return None
    n = len(frame)
    if _locked(frame, n - 1, band_pct):
        return None
    bars = [_Bar(*map(float, frame[["open", "high", "low", "close"]].iloc[i])) for i in range(n - 3, n)]
    b1, b2, b3 = bars
    rng = (frame["high"] - frame["low"]).astype("float64")
    avg10 = float(rng.iloc[n - 11:n - 1].mean())
    avg22 = lambda i: float(rng.iloc[max(0, i - 22):i].mean())  # noqa: E731
    tall = lambda b, i: b.rng > float(cfg["tall_candle_mult"]) * avg22(i)  # noqa: E731
    small = lambda b: b.body <= float(cfg["small_body_ratio"]) * avg10  # noqa: E731
    doji = lambda b: b.body <= float(cfg["doji_ratio"]) * avg10  # noqa: E731
    short_shadow = float(cfg["short_shadow_ratio"]) * avg10
    trend3 = _trend(frame["close"], n - 3, cfg)          # into the 3-bar pattern
    trend2 = _trend(frame["close"], n - 2, cfg)
    trend1 = _trend(frame["close"], n - 1, cfg)
    if any(b.rng == 0 for b in bars) or any(_locked(frame, i, band_pct) for i in (n - 3, n - 2)):
        return _single(b3, trend1, doji, short_shadow, cfg)

    found = None
    # three bars
    if trend3 == "down" and b1.black and tall(b1, n - 3) and small(b2) and b2.top < b1.c and b3.white \
            and b3.bottom > b2.top and b3.c >= b1.mid:
        found = Pattern("MORNING_STAR", "Sao mai", "tăng", 3, "đảo chiều 78% / hạng 12")
    elif trend3 == "up" and b1.white and tall(b1, n - 3) and small(b2) and b2.bottom > b1.c and b3.black \
            and b3.top < b2.bottom and b3.c <= b1.mid:
        found = Pattern("EVENING_STAR", "Sao hôm", "giảm", 3, "đảo chiều 72% / hạng 4")
    elif all(b.white and b.upper <= short_shadow for b in bars) and b1.c < b2.c < b3.c \
            and b1.bottom <= b2.o <= b1.top and b2.bottom <= b3.o <= b2.top and all(tall(b, n - 3 + k) for k, b in enumerate(bars)):
        found = Pattern("THREE_WHITE_SOLDIERS", "Ba chàng lính trắng", "tăng", 3, "đảo chiều 82% / hạng 32 — xu hướng sau thường ngắn")
    elif all(b.black and b.lower <= short_shadow for b in bars) and b1.c > b2.c > b3.c \
            and b1.bottom <= b2.o <= b1.top and b2.bottom <= b3.o <= b2.top and all(tall(b, n - 3 + k) for k, b in enumerate(bars)):
        found = Pattern("THREE_BLACK_CROWS", "Ba con quạ đen", "giảm", 3, "đảo chiều 78% / hạng 3")
    if found:
        return asdict(found)

    # two bars
    if trend2 == "down" and b2.black and b3.white and b3.o <= b2.c and b3.c >= b2.o and b3.body > b2.body:
        found = Pattern("BULLISH_ENGULFING", "Nhấn chìm tăng", "tăng", 2, "đảo chiều 63% / hạng 84")
    elif trend2 == "up" and b2.white and b3.black and b3.o >= b2.c and b3.c <= b2.o and b3.body > b2.body:
        found = Pattern("BEARISH_ENGULFING", "Nhấn chìm giảm", "giảm", 2, "đảo chiều 79% / hạng 91 — xu hướng sau ngắn")
    elif trend2 == "down" and b2.black and b3.white and b3.o < b2.l and b2.mid < b3.c < b2.o:
        found = Pattern("PIERCING_LINE", "Đường xuyên", "tăng", 2, "đảo chiều 64% / hạng 13")
    elif trend2 == "up" and b2.white and tall(b2, n - 2) and b3.black and b3.o > b2.h and b2.o < b3.c < b2.mid:
        found = Pattern("DARK_CLOUD_COVER", "Mây đen che phủ", "giảm", 2, "đảo chiều 60% / hạng 22")
    elif trend2 == "down" and b2.black and tall(b2, n - 2) and b3.white and b3.top <= b2.o and b3.bottom >= b2.c:
        found = Pattern("BULLISH_HARAMI", "Harami tăng", "tăng", 2, "đảo chiều 53% / hạng 38")
    elif trend2 == "up" and b2.white and tall(b2, n - 2) and b3.black and b3.top <= b2.c and b3.bottom >= b2.o:
        found = Pattern("BEARISH_HARAMI", "Harami giảm", "giảm", 2, "thực tế tiếp diễn tăng 53% / hạng 72")
    if found:
        return asdict(found)
    return _single(b3, trend1, doji, short_shadow, cfg)


def _single(b: _Bar, trend: str, doji, short_shadow: float, cfg: Mapping[str, Any]) -> dict[str, Any] | None:
    if b.rng == 0:
        return None
    long_mult = float(cfg["long_shadow_mult"])
    if doji(b):
        if b.lower >= 0.6 * b.rng and b.upper <= short_shadow:
            p = Pattern("DRAGONFLY_DOJI", "Doji chuồn chuồn", "lưỡng lự", 1, "đảo chiều 50% / hạng 98")
        elif b.upper >= 0.6 * b.rng and b.lower <= short_shadow:
            p = Pattern("GRAVESTONE_DOJI", "Doji bia mộ", "lưỡng lự", 1, "đảo chiều giảm 51% / hạng 77")
        else:
            p = Pattern("DOJI", "Doji", "lưỡng lự", 1, "")
        return asdict(p)
    body = max(b.body, 1e-9)
    if b.lower >= long_mult * body and b.upper <= short_shadow and b.bottom >= b.l + b.rng * 0.6:
        if trend == "down":
            return asdict(Pattern("HAMMER", "Búa", "tăng", 1, "đảo chiều tăng 60% / hạng 65"))
        if trend == "up":
            return asdict(Pattern("HANGING_MAN", "Người treo cổ", "giảm", 1, "thực tế tiếp diễn tăng 59% / hạng 87"))
    if b.upper >= long_mult * body and b.lower <= short_shadow and b.top <= b.l + b.rng * 0.4:
        if trend == "up":
            return asdict(Pattern("SHOOTING_STAR", "Sao băng", "giảm", 1, "đảo chiều giảm 59% / hạng 55"))
        if trend == "down":
            return asdict(Pattern("INVERTED_HAMMER", "Búa ngược", "tăng", 1, "thực tế tiếp diễn giảm 65% / hạng 6"))
    return None
