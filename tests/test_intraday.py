"""Intraday alerts (Phase 11): levels of the last close against the session's matched trades."""

import asyncio
import json
from datetime import date, datetime, timedelta, timezone

import pytest

from src.collectors import intraday_watch as iw
from src.database import connect, init_db
from src.quant.intraday import IntradayWatcher, Levels, session_fraction

VN = timezone(timedelta(hours=7))
CFG = {"sessions": {"HOSE": [["09:15", "11:30"], ["13:00", "14:30"]], "UPCOM": [["09:00", "11:30"], ["13:00", "15:00"]]},
       "min_session_fraction": 0.1, "pace_breakout": 1.5}


def at(hm: str) -> datetime:
    return datetime.fromisoformat(f"2026-10-12T{hm}:00+07:00")


def test_the_day_is_counted_in_continuous_trading_minutes():
    hose = CFG["sessions"]["HOSE"]
    assert session_fraction(at("09:10"), hose) == 0.0                       # the ATO is not on the clock
    assert session_fraction(at("10:22"), hose) == pytest.approx(67 / 225)
    assert session_fraction(at("12:15"), hose) == pytest.approx(135 / 225)  # the lunch break does not count
    assert session_fraction(at("14:40"), hose) == 1.0


def test_a_pivot_cross_fires_once_and_is_not_called_a_breakout():
    lv = Levels("AAA", "HOSE", prev_close=19_800, pivot=20_000, avg_volume=1_000_000, forming=True)
    w = IntradayWatcher({"AAA": lv}, CFG)
    assert w.on_trade("AAA", 19_900, 100_000, at("10:00")) == []
    first = w.on_trade("AAA", 20_050, 900_000, at("11:30"))
    assert [a.alert_type for a in first] == ["PIVOT_CROSS"]
    assert first[0].volume_pace == pytest.approx(900_000 / (1_000_000 * 135 / 225), abs=0.01)
    assert "Chưa phải breakout" in first[0].text and "khối lượng đang cao" in first[0].text   # 1.5x the pace
    assert w.on_trade("AAA", 20_100, 950_000, at("11:31")) == []                            # once a day


def test_no_pivot_alert_without_a_forming_base_or_from_above():
    w = IntradayWatcher({"A": Levels("A", "HOSE", 19_800, pivot=20_000, forming=False),
                         "B": Levels("B", "HOSE", 20_200, pivot=20_000, forming=True)}, CFG)
    assert w.on_trade("A", 20_100, None, at("10:00")) == [] and w.on_trade("B", 20_300, None, at("10:00")) == []


def test_a_stop_is_watched_only_while_it_holds():
    live = Levels("A", "HOSE", 20_000, stop=19_000, stop_active=True)
    pending = Levels("B", "HOSE", 18_500, stop=19_000, stop_active=False)      # already under it: a pending stop
    w = IntradayWatcher({"A": live, "B": pending}, CFG)
    assert [a.alert_type for a in w.on_trade("A", 18_950, None, at("10:00"))] == ["STOP_TOUCH"]
    assert w.on_trade("B", 18_400, None, at("10:00")) == []


def test_ma50_and_a_breakout_falling_back_under_its_pivot():
    lv = Levels("A", "HOSE", 20_500, pivot=20_000, ma50=20_200, status="BREAKOUT", forming=True)
    kinds = [a.alert_type for a in IntradayWatcher({"A": lv}, CFG).on_trade("A", 19_900, None, at("13:30"))]
    assert kinds == ["MA50_LOST", "BREAKOUT_UNDO"]


def test_a_skipped_ticker_keeps_its_last_price_and_raises_nothing():
    lv = Levels("A", "HOSE", 20_000, pivot=19_000, forming=True, skip_reason="quyền")
    w = IntradayWatcher({"A": lv}, CFG)
    assert w.on_trade("A", 25_000, None, at("10:00")) == [] and w.last["A"]["price"] == 25_000


def test_no_volume_pace_early_in_the_day():
    lv = Levels("A", "HOSE", 19_800, pivot=20_000, avg_volume=1_000_000, forming=True)
    assert IntradayWatcher({"A": lv}, CFG).on_trade("A", 20_000, 300_000, at("09:20"))[0].volume_pace is None


def test_a_dnse_trade_frame_is_read_with_both_configured_scales():
    payload = {"T": "t", "symbol": "hpg", "matchPrice": 20.75, "matchQtty": 10, "totalVolumeTraded": 2_860_570,
               "time": {"Seconds": 1791789138, "Nanos": 0}}
    ticker, price, volume, when = iw.trade_from_payload(payload, 1000.0, 10.0)
    assert (ticker, price, volume) == ("HPG", 20_750.0, 28_605_700.0)
    assert when.utcoffset() == timedelta(hours=7)


# -- the watcher process ------------------------------------------------------------------

@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "i.db"
    init_db(path)
    days = [date(2026, 10, 9) - timedelta(days=i) for i in range(80)]
    with connect(path) as conn:
        conn.execute("INSERT INTO sources (source_id, kind, source_level, enabled) VALUES ('dnse_openapi','market_data',2,1)")
        for t, ex in (("AAA", "HOSE"), ("BBB", "HOSE")):
            conn.execute("INSERT INTO tickers (ticker, exchange, name, created_at, updated_at) VALUES (?,?,?,'x','x')",
                         (t, ex, f"CTCP {t}"))
            conn.executemany("INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source_id, "
                             "observed_at) VALUES (?,?,?,?,?,?,1000000,'dnse_openapi','x')",
                             [(t, d.isoformat(), 19_800, 19_800, 19_800, 19_800) for d in days])
            state = {"trade_date": "2026-10-09", "pivot": 20_000, "stop": 18_500, "base_kind": "VCP",
                     "stop_breached": False, "stop_pending": False, "setup": {"status": "APPROACHING"}}
            conn.execute("INSERT INTO monitor_state (ticker, trade_date, state_json, computed_at) VALUES (?,?,?,'x')",
                         (t, "2026-10-09", json.dumps(state)))
        # BBB: today's reference is not the stored close -- a corporate action, levels on another scale
        conn.execute("INSERT INTO reference_prices (ticker, session_date, reference, ceiling, floor, published_at, "
                     "source_id, observed_at) VALUES ('BBB','2026-10-12',16500,17650,15350,'x','dnse_openapi','x')")
    monkeypatch.setattr("src.quant.monitor.monitored_tickers", lambda path=None: (["AAA", "BBB"], {}, {}))
    monkeypatch.setattr(iw, "STATUS_DIR", tmp_path / "intraday")
    return path


class FakeStream:
    def __init__(self, frames):
        self.frames, self.subs, self.closed = frames, {}, False

    def add_subscription(self, channel, symbols=()):
        self.subs[channel] = list(symbols)

    async def run(self, handler, max_sessions=None):
        from src.collectors.providers.dnse_stream import StreamEvent
        for f in self.frames:
            handler(StreamEvent(event="trade", channel_type="t", payload=f))

    async def close(self):
        self.closed = True


def _frame(symbol, price, volume, hm):
    return {"symbol": symbol, "matchPrice": price, "totalVolumeTraded": volume,
            "time": {"Seconds": at(hm).timestamp()}}


def test_the_watcher_stores_one_alert_skips_a_rescaled_ticker_and_writes_its_status(db):
    levels = iw.build_levels(date(2026, 10, 12), db)
    assert levels["AAA"].skip_reason is None and levels["AAA"].ma50 == pytest.approx(19_800)
    assert "tham chiếu" in levels["BBB"].skip_reason
    stream = FakeStream([_frame("AAA", 20.05, 50_000, "10:30"), _frame("AAA", 20.10, 60_000, "10:31"),
                         _frame("BBB", 25.0, 50_000, "10:30")])
    status = asyncio.run(iw.run_watch(path=db, stream=stream, now=lambda: at("10:00"), echo=lambda s: None))
    assert stream.subs == {"tick.G1.json": ["AAA"]} and stream.closed
    assert status.trades == 3 and status.alerts == 1 and status.ended_at and status.skipped.keys() == {"BBB"}
    with connect(db) as conn:
        rows = conn.execute("SELECT alert_id, ticker, alert_type, price, volume, source_id FROM intraday_alerts").fetchall()
    assert [tuple(r) for r in rows] == [("2026-10-12:AAA:PIVOT_CROSS", "AAA", "PIVOT_CROSS", 20_050.0, 500_000.0,
                                         "dnse_openapi")]
    # a restart the same day does not repeat it, and the rows cannot be edited
    again = asyncio.run(iw.run_watch(path=db, stream=FakeStream([_frame("AAA", 20.2, 70_000, "11:00")]),
                                     now=lambda: at("10:00"), echo=lambda s: None))
    assert again.alerts == 0
    with connect(db) as conn, pytest.raises(Exception):
        conn.execute("UPDATE intraday_alerts SET price = 1")
    assert json.loads((iw.STATUS_DIR / "status_2026-10-12.json").read_text(encoding="utf-8"))["ended_at"]


def test_the_api_reads_the_alerts_and_says_when_the_feed_is_stale(db):
    from src.web import server
    asyncio.run(iw.run_watch(path=db, stream=FakeStream([_frame("AAA", 20.05, 50_000, "10:30")]),
                             now=lambda: at("10:00"), echo=lambda s: None))
    data = server.api_intraday({"date": "2026-10-12"}, db)
    assert [a["alert_type"] for a in data["alerts"]] == ["PIVOT_CROSS"] and data["status"]["running"] is False
    assert server.api_intraday({}, db)["session_date"] == "2026-10-12"        # latest session with alerts
