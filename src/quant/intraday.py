"""Intraday alerts on the focus list (Phase 11): what crossed a level during the session.

Deterministic and display only. The levels are the ones the dashboard already shows,
from the latest monitor state (the session before): pivot, stop, MA50. A matched trade
from DNSE's websocket that reaches a level raises one alert per ticker, type and day:

    PIVOT_CROSS   price at or above the pivot of a forming base, from below at the last close.
                  Not a breakout: a breakout is judged on the CLOSE with the session's volume
                  (src/quant/monitor.py). The alert carries the volume pace so far.
    STOP_TOUCH    price at or below the stop, from above at the last close (a stop already
                  breached or only pending is not watched).
    MA50_LOST     price under MA50 after a close above it (Minervini: a break of the 50-day
                  line on volume is a warning for a stock that has run [SÁCH]).
    BREAKOUT_UNDO price back under the pivot of a stock whose status is "Vừa breakout".

Volume pace = volume so far / (50-session average x share of the trading day elapsed). The
day is taken as uniform over the continuous-trading minutes, which it is not (volume is
heavier at the open and in the ATC): a rough reading only [TRIỂN KHAI]. Thresholds:
`intraday` in config/thresholds.yaml.

A ticker whose levels may sit on another price scale today (a corporate action: today's
reference price is not the stored close on HOSE / HNX) is not watched; nothing is guessed.
Labels stay neutral -- never a buy or sell instruction.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, time
from typing import Any, Mapping, Sequence

from src.quant.vnfmt import num

ALERT_TEXT = {
    "PIVOT_CROSS": "Giá chạm / vượt pivot trong phiên",
    "STOP_TOUCH": "Giá xuống tới stop trong phiên",
    "MA50_LOST": "Giá xuống dưới MA50 trong phiên",
    "BREAKOUT_UNDO": "Mã vừa breakout quay lại dưới pivot",
}


@dataclass
class Levels:
    """One ticker's levels for the day, read after the previous close."""

    ticker: str
    exchange: str
    prev_close: float
    pivot: float | None = None
    stop: float | None = None
    ma50: float | None = None
    avg_volume: float | None = None          # 50-session average (from the ex-date when one is recent)
    base_kind: str | None = None
    status: str | None = None                # monitor status of the last session
    forming: bool = False                     # a forming base: only then is the pivot a buy point
    stop_active: bool = False                 # a stop that is neither breached nor pending
    skip_reason: str | None = None            # set when the ticker is not watched today


@dataclass
class Alert:
    ticker: str
    alert_type: str
    price: float
    level: float
    at: str                                   # ISO time of the trade (Vietnam)
    volume: float | None = None               # the session's volume so far
    volume_pace: float | None = None
    text: str = ""

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def session_fraction(at: datetime, sessions: Sequence[Sequence[str]]) -> float:
    """Share of the day's continuous-trading minutes elapsed at `at` (0..1)."""
    def minutes(t: time) -> int:
        return t.hour * 60 + t.minute

    now = minutes(at.time()) + at.second / 60.0
    total = done = 0.0
    for start, end in sessions:
        a, b = minutes(time.fromisoformat(start)), minutes(time.fromisoformat(end))
        total += b - a
        done += min(max(now - a, 0.0), b - a)
    return done / total if total else 0.0


@dataclass
class IntradayWatcher:
    """Feed trades in, get alerts out. One alert per ticker and type per day."""

    levels: Mapping[str, Levels]
    cfg: Mapping[str, Any]
    fired: set[tuple[str, str]] = field(default_factory=set)
    last: dict[str, dict[str, Any]] = field(default_factory=dict)

    def on_trade(self, ticker: str, price: float, volume: float | None, at: datetime) -> list[Alert]:
        lv = self.levels.get(ticker)
        if lv is None or price <= 0:
            return []
        self.last[ticker] = {"price": price, "volume": volume, "at": at.isoformat(timespec="seconds"),
                             "change_pct": round((price / lv.prev_close - 1) * 100.0, 2) if lv.prev_close else None}
        if lv.skip_reason:
            return []
        pace = self.volume_pace(lv, volume, at)
        out: list[Alert] = []
        if lv.forming and lv.pivot and lv.prev_close < lv.pivot and price >= lv.pivot:
            out.append(self._alert(lv, "PIVOT_CROSS", price, lv.pivot, at, volume, pace))
        if lv.stop_active and lv.stop and lv.prev_close > lv.stop and price <= lv.stop:
            out.append(self._alert(lv, "STOP_TOUCH", price, lv.stop, at, volume, pace))
        if lv.ma50 and lv.prev_close > lv.ma50 and price < lv.ma50:
            out.append(self._alert(lv, "MA50_LOST", price, lv.ma50, at, volume, pace))
        if lv.status == "BREAKOUT" and lv.pivot and lv.prev_close >= lv.pivot and price < lv.pivot:
            out.append(self._alert(lv, "BREAKOUT_UNDO", price, lv.pivot, at, volume, pace))
        return [a for a in out if a is not None]

    def volume_pace(self, lv: Levels, volume: float | None, at: datetime) -> float | None:
        sessions = self.cfg["sessions"]
        frac = session_fraction(at, sessions.get(lv.exchange.upper()) or sessions["HOSE"])
        if not volume or not lv.avg_volume or frac < float(self.cfg["min_session_fraction"]):
            return None                                   # too early in the day to say anything
        return round(volume / (lv.avg_volume * frac), 2)

    def _alert(self, lv: Levels, kind: str, price: float, level: float, at: datetime,
               volume: float | None, pace: float | None) -> Alert | None:
        if (lv.ticker, kind) in self.fired:
            return None
        self.fired.add((lv.ticker, kind))
        return Alert(lv.ticker, kind, price, level, at.isoformat(timespec="seconds"), volume, pace,
                     describe(kind, price, level, pace, self.cfg))


def describe(kind: str, price: float, level: float, pace: float | None, cfg: Mapping[str, Any]) -> str:
    """One neutral sentence: what happened, against which level, and what it does not mean."""
    gap = (price / level - 1) * 100.0
    digits = 2 if abs(gap) < 1 else 1                  # 79.400 vs MA50 79.432 is -0,04%, not "-0,0%"
    g = ("+" if gap > 0 else "") + num(gap, digits) + "%"
    pace_text = "" if pace is None else f"; khối lượng tới lúc này ≈ {num(pace, 1)} lần nhịp bình quân (ước tính)"
    p, lvl = num(price), num(level)
    if kind == "PIVOT_CROSS":
        strong = pace is not None and pace >= float(cfg["pace_breakout"])
        return (f"Giá {p} chạm / vượt pivot {lvl} ({g}){pace_text}. Chưa phải breakout: breakout xét giá đóng "
                f"cửa và khối lượng cả phiên{' — khối lượng đang cao' if strong else ''}.")
    if kind == "STOP_TOUCH":
        return (f"Giá {p} xuống tới stop tham chiếu {lvl}{pace_text}. Theo SEPA, đóng cửa dưới stop là "
                f"tín hiệu nền thất bại; giá trong phiên còn có thể đổi.")
    if kind == "MA50_LOST":
        return f"Giá {p} xuống dưới MA50 {lvl} ({g}){pace_text}. Xét lại ở giá đóng cửa."
    return f"Giá {p} quay lại dưới pivot {lvl} sau breakout ({g}){pace_text}. Xét lại ở giá đóng cửa."
