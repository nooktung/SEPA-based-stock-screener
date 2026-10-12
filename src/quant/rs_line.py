"""Relative-strength line: the stock's close divided by the market index's close (display only).

[SÁCH] O'Neil and Minervini read the RS line beside the price: it should slope up, and a
new high of the line before the price makes one ("the RS line leads") marks a stock the
market has started to favour. IBD / MarketSmith draw it under the candles and put a blue
dot on each new 52-week high of the line [TRANG GỐC IBD]. This is not the RS Rating (a
percentile rank across the market, src/quant/screener.py); it compares one stock with the
index only. Weight 0: no score, group or status reads it.

The line is scaled to 100 at its first point so its value reads as "relative performance
since then". Stock prices are DNSE's adjusted series and the index has no adjustments, so
the ratio is continuous across ex-rights dates. A session missing on either side has no
point; nothing is interpolated. Periods: `rs_line` in config/thresholds.yaml.
"""

from __future__ import annotations

from typing import Any, Mapping, Sequence

import numpy as np


def ratios(bars: Sequence[Mapping[str, Any]], index_close: Mapping[str, float],
           date_key: str = "trade_date") -> list[tuple[str, float, float]]:
    """(time, stock / index, stock close) for every bar whose session the index also has.
    `date_key` names the session the close belongs to ("period_end" on weekly / monthly bars);
    the point is placed at the bar's own `trade_date`."""
    out = []
    for b in bars:
        day, close = b.get(date_key) or b["trade_date"], b.get("close")
        idx = index_close.get(day)
        if close and idx:
            out.append((b["trade_date"], float(close) / float(idx), float(close)))
    return out


def rs_line(bars: Sequence[Mapping[str, Any]], index_close: Mapping[str, float], lookback: int,
            trend: int, date_key: str = "trade_date") -> dict[str, Any] | None:
    """The line's points (100 = its first point), the sessions it set a `lookback`-bar high
    ("new_highs"), those where it did so while the close was below its own `lookback` high
    ("leads": the blue dot of a line ahead of the price), and a summary of the latest bar."""
    rows = ratios(bars, index_close, date_key)
    if len(rows) < 2:
        return None
    times = [r[0] for r in rows]
    ratio = np.array([r[1] for r in rows], dtype="float64")
    close = np.array([r[2] for r in rows], dtype="float64")
    scale = 100.0 / ratio[0]
    line = ratio * scale
    new_highs: list[str] = []
    leads: list[str] = []
    for i in range(lookback, len(line)):
        if line[i] >= line[i - lookback:i].max():
            new_highs.append(times[i])
            if close[i] < close[i - lookback:i].max():
                leads.append(times[i])
    last = len(line) - 1
    window = line[max(0, last - lookback + 1):]
    high = float(window.max())
    high_at = times[max(0, last - lookback + 1) + int(window.argmax())]
    summary = {
        "value": round(float(line[last]), 2),
        "high": round(high, 2), "high_on": high_at,
        "from_high_pct": round((float(line[last]) / high - 1) * 100.0, 2),
        "change_pct": round((float(line[last]) / float(line[last - trend]) - 1) * 100.0, 2) if last >= trend else None,
        "trend_bars": trend, "lookback_bars": lookback,
        "full_window": len(line) > lookback,
        "new_high_on": new_highs[-1] if new_highs else None,
        "leads_on": leads[-1] if leads else None,
        "at_new_high": bool(new_highs) and new_highs[-1] == times[last],
    }
    return {"points": [{"time": t, "value": round(float(v), 3)} for t, v in zip(times, line)],
            "new_highs": new_highs, "leads": leads, "summary": summary}


def settings(thresholds: Mapping[str, Any], tf: str) -> tuple[int, int]:
    """(lookback, trend) bars on this timeframe."""
    cfg = thresholds["rs_line"]
    return int(cfg["lookback"][tf]), int(cfg["trend"][tf])
