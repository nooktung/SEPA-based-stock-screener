"""Index session figures from DNSE market-index, matched-volume distribution days, and the trading calendar."""
import json
from datetime import date, datetime

import numpy as np
import pandas as pd
import pytest

import src.market_calendar as mc
from src.collectors import trading_calendar
from src.collectors.base import ProviderUnavailable
from src.collectors.index_session import collect_index_sessions, load_depth
from src.collectors.providers.dnse_provider import DnseMarketProvider, parse_market_index, vn_day_bounds
from src.collectors.refprices import session_for
from src.database import connect, init_db
from src.market_calendar import VN_TZ, is_trading_day, next_trading_day
from src.quant.channel import future_sessions
from src.quant.screener import distribution_days, follow_through, index_volume_pair, market_status, screen_settings

# VN-Index 08/10/2026 as DNSE served it (trimmed), checked against VNDirect's volume.
SNAPSHOT = {
    "marketId": "STO", "indexName": "VNINDEX", "transactionTime": "2026-10-08 15:05:05.019",
    "valueIndexes": 1738.97, "totalVolumeTraded": 676634772, "grossTradeAmount": 15883.31124405,
    "contauctAccTrdVol": 532713885, "contauctAccTrdVal": 12740.76910514,
    "blkTrdAccTrdVol": 143920887, "blkTrdAccTrdVal": 3142.54213891,
    "fluctuationUpperLimitIssueCount": 12, "fluctuationUpIssueCount": 110, "fluctuationSteadinessIssueCount": 56,
    "fluctuationDownIssueCount": 183, "fluctuationLowerLimitIssueCount": 7,
    "highestValueIndexes": 1764.45, "lowestValueIndexes": 1722.27, "priorValueIndexes": 1753.39,
}
INDEX_CFG = {"symbols": ["VNINDEX"], "backfill_sessions": 300, "stop_after_empty": 3,
             "final_snapshot_after": "15:00", "close_tolerance_pct": 0.05}
PIPE_CFG = {"session_closed_after": "15:05"}
S = screen_settings()


# -- provider ------------------------------------------------------------------------

class Recorder:
    def __init__(self, body):
        self.calls, self.body = [], body

    def get(self, path, query=None):
        self.calls.append((path, dict(query or {})))
        return self.body


def test_market_index_close_asks_for_one_snapshot_of_that_day_and_maps_its_fields():
    client = Recorder({"marketIndices": [SNAPSHOT], "nextPageToken": "x"})
    p = DnseMarketProvider("dnse_openapi", 2, {"market_index_value_scale": 1e9}, client=client)
    snap = p.get_market_index_close("vnindex", date(2026, 10, 8))
    path, query = client.calls[0]
    assert path == "/price/VNINDEX/market-index"
    assert (query["from"], query["to"]) == vn_day_bounds(date(2026, 10, 8)) and query["limit"] == 1
    assert snap["matched_volume"] == 532713885 and snap["put_through_volume"] == 143920887
    assert snap["matched_volume"] + snap["put_through_volume"] == SNAPSHOT["totalVolumeTraded"]
    assert snap["matched_value_vnd"] == pytest.approx(12740.76910514e9)
    assert (snap["advancers"], snap["unchanged"], snap["decliners"], snap["ceilings"], snap["floors"]) == (110, 56, 183, 12, 7)
    assert snap["close"] == 1738.97 and snap["source_id"] == "dnse_openapi"


def test_a_snapshot_from_another_day_or_an_empty_answer_is_nothing():
    assert parse_market_index(SNAPSHOT, date(2026, 10, 7), 1e9, "dnse_openapi", datetime.now()) is None
    p = DnseMarketProvider("dnse_openapi", 2, {}, client=Recorder({"marketIndices": []}))
    assert p.get_market_index_close("VNINDEX", date(2025, 10, 8)) is None


# -- collector -----------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    path = tmp_path / "i.db"
    init_db(path)
    days = pd.bdate_range("2026-09-21", "2026-10-08")
    with connect(path) as conn:
        conn.executemany("INSERT INTO index_daily (symbol, trade_date, close, volume, source_id, observed_at) "
                         "VALUES ('VNINDEX', ?, 1738.97, 700000000, 'dnse_openapi', 'x')",
                         [(d.date().isoformat(),) for d in days])
    return path


def snap_for(day, **changes):
    row = {**SNAPSHOT, "transactionTime": f"{day.isoformat()} 15:05:05.019", **changes}
    return parse_market_index(row, day, 1e9, "dnse_openapi", datetime(2026, 10, 8, 9, 0))


def stored(db):
    with connect(db) as conn:
        return {r[0]: (r[1], r[2], r[3]) for r in conn.execute(
            "SELECT trade_date, matched_volume, advancers, ceilings FROM index_daily ORDER BY trade_date")}


def test_figures_are_attached_to_closed_sessions_newest_first(db, tmp_path):
    asked = []

    def fetch(symbol, day):
        asked.append(day)
        return snap_for(day)

    rep = collect_index_sessions(fetch, INDEX_CFG, PIPE_CFG, sessions=3, now=datetime(2026, 10, 8, 15, 10, tzinfo=VN_TZ),
                                 db_path=db, depth_path=tmp_path / "d.json")
    assert asked == [date(2026, 10, 8), date(2026, 10, 7), date(2026, 10, 6)]
    assert rep.written == ["2026-10-08", "2026-10-07", "2026-10-06"]
    assert stored(db)["2026-10-08"] == (532713885, 110, 12) and stored(db)["2026-10-05"] == (None, None, None)
    # a second run asks only for what is still missing
    asked.clear()
    collect_index_sessions(fetch, INDEX_CFG, PIPE_CFG, sessions=2, now=datetime(2026, 10, 8, 16, 0, tzinfo=VN_TZ),
                           db_path=db, depth_path=tmp_path / "d.json")
    assert asked == [date(2026, 10, 5), date(2026, 10, 2)]


def test_an_unfinished_session_or_a_snapshot_before_the_close_is_not_written(db, tmp_path):
    before_close = collect_index_sessions(lambda s, d: snap_for(d), INDEX_CFG, PIPE_CFG, sessions=1,
                                          now=datetime(2026, 10, 8, 14, 0, tzinfo=VN_TZ), db_path=db,
                                          depth_path=tmp_path / "d.json")
    assert before_close.not_final == ["2026-10-08"] and before_close.requests == 1 and not before_close.written
    early = collect_index_sessions(lambda s, d: snap_for(d, transactionTime=f"{d.isoformat()} 14:50:00.000"),
                                   INDEX_CFG, PIPE_CFG, sessions=1, now=datetime(2026, 10, 9, 9, 0, tzinfo=VN_TZ),
                                   db_path=db, depth_path=tmp_path / "d.json")
    assert early.not_final == ["2026-10-08"] and stored(db)["2026-10-08"] == (None, None, None)


def test_a_close_that_disagrees_with_the_stored_bar_is_reported_not_written(db, tmp_path):
    rep = collect_index_sessions(lambda s, d: snap_for(d, valueIndexes=1745.0), INDEX_CFG, PIPE_CFG, sessions=1,
                                 now=datetime(2026, 10, 9, 9, 0, tzinfo=VN_TZ), db_path=db, depth_path=tmp_path / "d.json")
    assert list(rep.mismatched) == ["2026-10-08"] and not rep.written


def test_the_walk_back_stops_where_dnse_history_ends_and_remembers_it(db, tmp_path):
    marker = tmp_path / "d.json"
    has_data = {date(2026, 10, 8), date(2026, 10, 7)}
    rep = collect_index_sessions(lambda s, d: snap_for(d) if d in has_data else None, INDEX_CFG, PIPE_CFG,
                                 now=datetime(2026, 10, 9, 9, 0, tzinfo=VN_TZ), db_path=db, depth_path=marker)
    assert rep.written == ["2026-10-08", "2026-10-07"] and rep.empty == ["2026-10-06", "2026-10-05", "2026-10-02"]
    assert rep.history_starts_after == "2026-10-06" and load_depth(marker) == {"VNINDEX": "2026-10-06"}
    asked = []
    collect_index_sessions(lambda s, d: asked.append(d), INDEX_CFG, PIPE_CFG,
                           now=datetime(2026, 10, 9, 9, 0, tzinfo=VN_TZ), db_path=db, depth_path=marker)
    assert asked == []                                  # nothing before the remembered date is asked again


def test_an_outage_stops_after_three_errors(db, tmp_path):
    def down(symbol, day):
        raise ProviderUnavailable("DNSE server error")

    rep = collect_index_sessions(down, INDEX_CFG, PIPE_CFG, now=datetime(2026, 10, 9, 9, 0, tzinfo=VN_TZ),
                                 db_path=db, depth_path=tmp_path / "d.json")
    assert len(rep.errors) == 3 and not rep.written


# -- matched volume in the market rules -----------------------------------------------

def frame(closes, total, matched):
    dates = pd.bdate_range(end="2026-09-25", periods=len(closes))
    c = np.asarray(closes, dtype="float64")
    return pd.DataFrame({"trade_date": [d.date().isoformat() for d in dates], "close": c, "low": c * 0.998,
                         "high": c * 1.002, "volume": np.asarray(total, dtype="float64"),
                         "matched_volume": pd.array(matched, dtype="Float64").astype("float64")})


def test_a_distribution_day_is_measured_on_matched_volume_when_configured():
    # last session -1%: total volume up (a big put-through deal), matched volume down
    f = frame([1000, 1000, 990], [100, 100, 160], [80, 80, 70])
    assert bool(distribution_days(f, S).iloc[-1]) is False                              # matched (default)
    total = {**S, "market": {**S["market"], "index_volume": "total"}}
    assert bool(distribution_days(f, total).iloc[-1]) is True


def test_a_pair_without_matched_volume_compares_total_volume_for_both_sessions():
    f = frame([1000, 1000, 990], [100, 100, 160], [None, None, 70])
    vol, prev = index_volume_pair(f, S)
    assert (vol.iloc[-1], prev.iloc[-1]) == (160.0, 100.0)                          # never 70 against 100
    assert bool(distribution_days(f, S).iloc[-1]) is True


def test_the_ftd_volume_test_uses_matched_volume():
    closes = [1000] * 6 + [980, 960, 940, 900, 905, 910, 915, 935]                  # day 4: +2.2%
    total = [100] * 13 + [150]
    assert follow_through(frame(closes, total, [80] * 14), S)["date"] is None          # matched flat: no FTD
    assert follow_through(frame(closes, total, [80] * 13 + [90]), S)["date"] == frame(closes, total, [80] * 14)["trade_date"].iloc[-1]


def test_market_status_says_which_volume_it_counted():
    closes = list(np.linspace(1500, 1800, 210))
    f = frame(closes, [100] * 210, [None] * 200 + [80] * 10)
    vb = market_status(f, S)["volume_basis"]
    assert vb["basis"] == "matched" and vb["fallback_sessions"] == 16 and vb["matched_since"] == f["trade_date"].iloc[200]


# -- trading calendar -----------------------------------------------------------------

def write_calendar(dates, known_from, known_to, fetched_on="2026-10-08"):
    mc.CALENDAR_FILE.parent.mkdir(parents=True, exist_ok=True)
    mc.CALENDAR_FILE.write_text(json.dumps({"fetched_on": fetched_on, "known_from": known_from, "known_to": known_to,
                                            "dates": dates}), encoding="utf-8")


def test_dnse_holidays_are_skipped_inside_the_stored_range_and_weekdays_used_outside():
    days = [d.date().isoformat() for d in pd.bdate_range("2026-11-16", "2026-11-30") if d.date() != date(2026, 11, 24)]
    write_calendar(days, "2026-11-16", "2026-11-30")
    assert is_trading_day(date(2026, 11, 24)) is False and is_trading_day(date(2026, 11, 23)) is True
    assert next_trading_day(date(2026, 11, 23)) == date(2026, 11, 25)
    assert is_trading_day(date(2026, 12, 1)) is True and is_trading_day(date(2026, 12, 5)) is False   # beyond: weekdays
    cfg = {"next_session_after": "15:00"}
    assert session_for(datetime(2026, 11, 23, 20, 46), cfg) == date(2026, 11, 25)
    assert future_sessions(pd.Timestamp("2026-11-20"), 3) == ["2026-11-23", "2026-11-25", "2026-11-26"]


def test_without_a_stored_calendar_every_weekday_is_a_session():
    assert is_trading_day(date(2026, 11, 24)) is True
    assert session_for(datetime(2026, 11, 23, 20, 46), {"next_session_after": "15:00"}) == date(2026, 11, 24)


def test_a_refresh_keeps_passed_sessions_and_replaces_the_rest():
    write_calendar(["2026-10-07", "2026-10-08", "2026-10-09", "2026-10-12"], "2026-10-07", "2026-10-12",
                   fetched_on="2026-10-07")
    calls = []

    def get(path):
        calls.append(path)
        return {"workingDates": ["2026-10-08", "2026-10-12", "2026-10-13"]}       # 09/10 became a holiday

    note = trading_calendar.refresh(get, date(2026, 10, 8), "dnse_openapi")
    data = trading_calendar.load()
    assert calls == ["/market/working-dates"] and "2026-10-07" in data["dates"]
    assert data["dates"] == ["2026-10-07", "2026-10-08", "2026-10-12", "2026-10-13"]
    assert (data["known_from"], data["known_to"], data["fetched_on"]) == ("2026-10-07", "2026-10-13", "2026-10-08")
    assert "lịch giao dịch" in note
    assert trading_calendar.refresh(get, date(2026, 10, 8), "dnse_openapi").endswith("2026-10-13)")
    assert calls == ["/market/working-dates"]                                    # once a day


def test_the_command_brings_the_index_bar_up_to_date_before_attaching(monkeypatch, tmp_path):
    """Run before `monitor` / `backfill`, today's index row would not exist yet (09/10/2026)."""
    import src.collectors.backfill as bf
    import src.collectors.index_session as isess
    import src.main as main
    from src.collectors.runner import CollectionReport

    order = []

    class P:
        source_id = "dnse_openapi"
        client = Recorder({"workingDates": ["2026-10-09"]})

        def get_market_index_close(self, symbol, day):
            return None

    monkeypatch.setattr(main, "init_db", lambda *a, **k: None)
    monkeypatch.setattr("src.env.load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(bf, "live_market_provider", lambda: P())
    monkeypatch.setattr(bf, "run_backfill", lambda **k: order.append(("bars", k["benchmarks"], k["tickers"]))
                        or CollectionReport(run_id="x", started_at=datetime.now()))
    monkeypatch.setattr(isess, "collect_index_sessions",
                        lambda fetch, cfg, pipe, symbol, sessions, now: order.append(("attach", symbol))
                        or isess.IndexSessionReport(symbol=symbol))
    assert main.index_session([]) == 0
    assert order == [("bars", ["VNINDEX"], []), ("attach", "VNINDEX")]
