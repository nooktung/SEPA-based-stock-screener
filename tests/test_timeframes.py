"""Weekly / monthly chart bars: how daily sessions are grouped, and which MAs they get."""

from datetime import date

import pytest

from src.config import load_config
from src.quant import timeframes as tfm


def day(d, o, h, l, c, v=100):
    return {"trade_date": d, "open": o, "high": h, "low": l, "close": c, "volume": v}


# Mon 28/09 .. Fri 02/10 (one holiday-free week), then Mon 05/10 and Tue 06/10.
BARS = [
    day("2026-09-28", 10, 11, 9.5, 10.5), day("2026-09-29", 10.5, 12, 10, 11.8),
    day("2026-09-30", 11.8, 12.2, 11, 11.2), day("2026-10-01", 11.2, 11.5, 8.9, 9.4, 300),
    day("2026-10-02", 9.4, 10, 9.2, 9.9), day("2026-10-05", 9.9, 10.4, 9.8, 10.2), day("2026-10-06", 10.2, 10.3, 10, 10.1),
]


def test_a_week_takes_first_open_extremes_last_close_and_summed_volume():
    weeks = tfm.resample(BARS, "W", today=date(2026, 10, 6))
    assert [w["trade_date"] for w in weeks] == ["2026-09-28", "2026-10-05"]
    w = weeks[0]
    assert (w["open"], w["high"], w["low"], w["close"], w["volume"]) == (10, 12.2, 8.9, 9.9, 700)
    assert w["period_end"] == "2026-10-02" and w["sessions"] == 5 and w["partial"] is False


def test_the_running_week_and_month_are_flagged_unfinished():
    weeks = tfm.resample(BARS, "W", today=date(2026, 10, 6))
    assert weeks[-1]["partial"] is True and weeks[-1]["sessions"] == 2
    months = tfm.resample(BARS, "M", today=date(2026, 10, 6))
    assert [m["trade_date"] for m in months] == ["2026-09-28", "2026-10-01"]
    assert months[0]["partial"] is False and months[0]["close"] == 11.2          # September closed on the 30th
    assert months[-1]["partial"] is True and months[-1]["open"] == 11.2


def test_a_finished_period_is_not_partial_even_if_it_ends_early():
    # The last stored session is Friday: the week is complete whatever today is.
    assert tfm.resample(BARS[:5], "W", today=date(2026, 10, 2))[-1]["partial"] is False
    # Data stops on Tuesday but the week is long over (a halted stock): not "running".
    assert tfm.resample(BARS, "W", today=date(2026, 11, 20))[-1]["partial"] is False


def test_daily_is_returned_unchanged_and_an_unknown_timeframe_is_refused():
    assert tfm.resample(BARS, "D") == BARS
    with pytest.raises(ValueError):
        tfm.resample(BARS, "Q")


def test_configured_periods_keep_the_daily_meaning():
    cfg = load_config().thresholds["chart_timeframes"]
    assert tfm.periods("D", cfg) == {"MA20": 20, "MA50": 50, "MA150": 150, "MA200": 200}
    assert tfm.periods("W", cfg) == {"MA20": 4, "MA50": 10, "MA150": 30, "MA200": 40}
    with pytest.raises(ValueError):
        tfm.periods("W", {"W": [4, 10]})


def test_series_start_at_the_first_full_window():
    bars = [day(f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", 1, 1, 1, float(i + 1)) for i in range(60)]
    ma, osc = tfm.series(bars, {"MA20": 4, "MA50": 10})
    assert len(ma["MA20"]) == 57 and ma["MA20"][0] == {"time": bars[3]["trade_date"], "value": 2.5}
    assert len(ma["MA50"]) == 51
    assert set(osc) == {"RSI", "MACD", "SIGNAL", "HIST"} and osc["RSI"][-1]["value"] == 100
