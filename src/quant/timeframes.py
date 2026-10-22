"""Weekly and monthly bars for the dashboard chart (display only).

Daily bars are grouped by calendar week (Monday-Friday) or calendar month:
open = first session's open, high / low = extremes, close = last session's
close, volume = sum. A bar is labelled with its first session's date (what
the chart's time axis needs) and carries its last session's date and session
count. The current, unfinished week or month is flagged `partial` -- its
"close" is only the latest close so far.

Moving averages keep the daily chart's meaning on the other timeframes:
weekly 4 / 10 / 30 / 40 weeks are the daily 20 / 50 / 150 / 200 sessions
(10 and 40 weeks are the lines MarketSmith's weekly chart uses, 30 weeks is
Weinstein's stage line). Monthly uses 5 / 10 / 20 / 40 months. RSI 14 and
MACD 12/26/9 are computed on the weekly / monthly closes themselves, as
TradingView does. Periods: `chart_timeframes` in config/thresholds.yaml.
"""

from __future__ import annotations

from datetime import date
from typing import Any, Mapping, Sequence

import pandas as pd

from src.quant import indicators as ind

TIMEFRAMES = {"D": "ngày", "W": "tuần", "M": "tháng"}
SLOTS = ("MA20", "MA50", "MA150", "MA200")     # the chart's MA layer keys, whatever the timeframe


def resample(bars: Sequence[Mapping[str, Any]], tf: str, today: date | None = None) -> list[dict[str, Any]]:
    """Daily bars (ascending, `trade_date` ISO strings) -> weekly ("W") or monthly ("M") bars."""
    if tf == "D" or not bars:
        return [dict(b) for b in bars]
    if tf not in ("W", "M"):
        raise ValueError(f"unknown timeframe {tf}")
    df = pd.DataFrame(bars)
    stamps = pd.to_datetime(df["trade_date"])
    df["period"] = stamps.dt.to_period("W-FRI" if tf == "W" else "M")
    # Built-in grouped reductions preserve first/last non-null prices and avoid
    # constructing hundreds of Series objects for each chart.
    grouped = df.groupby("period", sort=True)
    values = grouped.agg(trade_date=("trade_date", "first"),
                         period_end=("trade_date", "last"),
                         open=("open", "first"), high=("high", "max"),
                         low=("low", "min"), close=("close", "last"),
                         volume=("volume", "sum"), sessions=("trade_date", "size"))
    out = []
    for period, row in zip(values.index, values.to_dict("records")):
        for key in ("open", "high", "low", "close"):
            value = row[key]
            row[key] = None if pd.isna(value) else float(value)
        row["trade_date"], row["period_end"] = str(row["trade_date"]), str(row["period_end"])
        row["volume"], row["sessions"] = float(row["volume"]), int(row["sessions"])
        row["partial"], row["_period"] = False, period
        out.append(row)
    if out:
        last = out[-1]
        last_session = date.fromisoformat(last["period_end"])
        calendar_end = last["_period"].end_time.date()          # Friday for a week, the 28th-31st for a month
        today = today or date.today()
        # unfinished: the period runs past the last stored session and has not ended yet
        last["partial"] = last_session < calendar_end and today <= calendar_end
    for b in out:
        b.pop("_period")
    return out


def periods(tf: str, cfg: Mapping[str, Any]) -> dict[str, int]:
    """MA layer key -> period on this timeframe."""
    values = list(cfg[tf])
    if len(values) != len(SLOTS):
        raise ValueError(f"chart_timeframes.{tf} needs {len(SLOTS)} periods")
    return dict(zip(SLOTS, (int(v) for v in values)))


def series(bars: Sequence[Mapping[str, Any]], ma_periods: Mapping[str, int]) -> tuple[dict[str, list], dict[str, list]]:
    """MAs (keyed by layer slot), RSI 14 and MACD 12/26/9 on these bars' closes, from the first full window on."""
    ma: dict[str, list[dict[str, Any]]] = {}
    osc: dict[str, list[dict[str, Any]]] = {}
    if not bars:
        return ma, osc
    close = pd.Series([b["close"] for b in bars], dtype="float64")
    times = [b["trade_date"] for b in bars]

    def points(s: pd.Series, digits: int = 2) -> list[dict[str, Any]]:
        return [{"time": times[i], "value": round(float(v), digits)} for i, v in enumerate(s) if v == v]

    for slot, n in ma_periods.items():
        ma[slot] = points(ind.sma(close, n))
    osc["RSI"] = points(ind.rsi(close, 14))
    line, signal, hist = ind.macd(close, 12, 26, 9)
    osc["MACD"], osc["SIGNAL"], osc["HIST"] = points(line, 3), points(signal, 3), points(hist, 3)
    return ma, osc
