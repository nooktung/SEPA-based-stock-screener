"""Foreign-flow provider, storage and features."""

from datetime import date, datetime, timezone

import pandas as pd
import pytest

from src.collectors.base import (
    FlowRecord,
    ProviderDataMissing,
    ProviderError,
    Provenance,
    SourceLevel,
)
from src.collectors.market_store import stored_flow_dates, update_breadth, upsert_flows
from src.collectors.providers.dnse_flow_provider import DnseFlowProvider
from src.collectors.runner import collect_foreign_flows
from src.database import connect, init_db, sync_reference_data
from src.market_calendar import (
    VN_TZ,
    is_weekday,
    missing_runs,
    session_is_complete,
    sessions_between,
)
from src.quant.flow import compute_flow_features, compute_for_ticker, net_streak
from src.quant.store import load_flows


class RecordingClient:
    """Replays foreign-trading payloads and records the queries made."""

    def __init__(self, by_day=None, default=None):
        self.by_day = by_day or {}
        self.default = default if default is not None else {"foreigners": []}
        self.queries = []

    def get(self, path, query=None):
        query = query or {}
        self.queries.append({"path": path, **query})
        key = _day_of(query.get("from"))
        payload = self.by_day.get(key, self.default)
        if isinstance(payload, Exception):
            raise payload
        return payload


def _day_of(epoch):
    from src.collectors.providers.dnse_provider import epoch_to_vn_date

    return epoch_to_vn_date(epoch) if epoch is not None else None


def snapshot(buy, sell, time="15:32:22.530", **extra):
    return {
        "foreigners": [
            {
                "marketId": "STO",
                "boardId": "G1",
                "symbol": "SSI",
                "totalBuyTradedAmount": buy,
                "totalSellTradedAmount": sell,
                "totalBuyVolume": 200_976,
                "totalSellVolume": 326_506,
                "foreignerOrderLimitQuantity": 1_022_025_471,
                "foreignerBuyPossibleQuantity": 1_028_640_000,
                "time": f"2026-08-27 {time}",
                **extra,
            }
        ],
        "nextPageToken": "abc",
    }


def _provider(client, **config):
    return DnseFlowProvider(
        source_id="dnse_foreign_flow",
        source_level=2,
        config={"capabilities": ["foreign_net"], **config},
        client=client,
    )


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    import src.database as database

    monkeypatch.setattr(database, "db_path", lambda: path)
    init_db(path)
    sync_reference_data(path)
    return path


def _record(day=date(2026, 8, 27), buy=100.0, sell=60.0, key="SSI"):
    return FlowRecord(
        scope="TICKER",
        scope_key=key,
        trade_date=day,
        foreign_buy_value=buy,
        foreign_sell_value=sell,
        foreign_net_value=None if buy is None or sell is None else buy - sell,
        provenance=Provenance(
            source_id="dnse_foreign_flow",
            source_level=SourceLevel.DATA_VENDOR,
            observed_at=datetime(2026, 8, 27, 9, 0, tzinfo=timezone.utc),
        ),
        boards_split=True,
    )


# -- the calendar ------------------------------------------------------------

def test_weekends_are_not_sessions():
    assert is_weekday(date(2026, 8, 22)) is False       # Saturday
    assert is_weekday(date(2026, 8, 24)) is True        # Monday


def test_sessions_between_skips_weekends():
    days = sessions_between(date(2026, 8, 21), date(2026, 8, 25))
    assert days == [date(2026, 8, 21), date(2026, 8, 24), date(2026, 8, 25)]


def test_missing_runs_fills_a_hole_in_the_middle():
    # The naive approach -- start after the newest session held -- would leave
    # an early gap unfetched forever.
    sessions = sessions_between(date(2026, 8, 3), date(2026, 8, 14))
    held = set(sessions_between(date(2026, 8, 10), date(2026, 8, 13)))
    assert missing_runs(sessions, held) == [
        (date(2026, 8, 3), date(2026, 8, 7)),
        (date(2026, 8, 14), date(2026, 8, 14)),
    ]


def test_a_weekend_does_not_split_a_run():
    sessions = sessions_between(date(2026, 8, 20), date(2026, 8, 25))
    assert missing_runs(sessions, set()) == [(date(2026, 8, 20), date(2026, 8, 25))]


def test_nothing_missing_is_no_runs():
    sessions = sessions_between(date(2026, 8, 3), date(2026, 8, 7))
    assert missing_runs(sessions, set(sessions)) == []


# -- the provider ------------------------------------------------------------

def test_one_request_per_session_asking_for_the_final_snapshot():
    # The endpoint caps the window at one day and serves intraday snapshots,
    # so the day's totals are the newest row: limit 1, newest first.
    client = RecordingClient(default=snapshot(100.0, 60.0))
    _provider(client).get_flows("TICKER", "SSI", date(2026, 8, 24), date(2026, 8, 26))
    assert len(client.queries) == 3
    for query in client.queries:
        assert query["path"] == "/price/SSI/foreign-trading"
        assert query["limit"] == 1
        assert query["order"] == "DESC"
        assert query["boardId"] == "G1"


def test_weekends_are_not_requested_at_all():
    client = RecordingClient(default=snapshot(1.0, 1.0))
    _provider(client).get_flows("TICKER", "SSI", date(2026, 8, 21), date(2026, 8, 25))
    assert len(client.queries) == 3


def test_totals_become_buy_sell_and_net():
    client = RecordingClient(default=snapshot(100_078_675_000, 58_846_980_000))
    record = _provider(client).get_flows("TICKER", "ssi", date(2026, 8, 26), date(2026, 8, 26))[0]
    assert record.scope_key == "SSI"
    assert record.foreign_buy_value == 100_078_675_000
    assert record.foreign_sell_value == 58_846_980_000
    assert record.foreign_net_value == pytest.approx(41_231_695_000)
    assert record.provenance.source_id == "dnse_foreign_flow"


def test_absent_sides_are_not_read_as_zero():
    # One side missing must not produce a net that treats the other as zero.
    client = RecordingClient(default=snapshot(100.0, None))
    record = _provider(client).get_flows("TICKER", "SSI", date(2026, 8, 26), date(2026, 8, 26))[0]
    assert record.foreign_sell_value is None
    assert record.foreign_net_value is None


def test_a_non_trading_day_produces_no_record():
    client = RecordingClient(default={"foreigners": []})
    assert _provider(client).get_flows("TICKER", "SSI", date(2026, 8, 26), date(2026, 8, 26)) == []


def test_unavailable_fields_stay_empty():
    client = RecordingClient(default=snapshot(100.0, 60.0))
    record = _provider(client).get_flows("TICKER", "SSI", date(2026, 8, 26), date(2026, 8, 26))[0]
    # Proprietary desks, ETF creations and matched value are not on this feed.
    assert record.prop_net_value is None
    assert record.etf_net_value is None
    assert record.total_match_value is None


def test_a_malformed_payload_is_an_error_not_an_empty_day():
    client = RecordingClient(default={"unexpected": []})
    with pytest.raises(ProviderError):
        _provider(client).get_flows("TICKER", "SSI", date(2026, 8, 26), date(2026, 8, 26))


def test_market_scope_is_refused_rather_than_aggregated():
    # Summing the watchlist would describe the watchlist, not the market.
    with pytest.raises(ProviderDataMissing):
        _provider(RecordingClient()).get_flows("MARKET", "HOSE", date(2026, 8, 26), date(2026, 8, 26))


def test_reversed_range_is_rejected():
    with pytest.raises(ProviderError):
        _provider(RecordingClient()).get_flows("TICKER", "SSI", date(2026, 8, 26), date(2026, 8, 1))


def test_breadth_is_reported_missing_not_approximated():
    with pytest.raises(ProviderDataMissing) as exc:
        _provider(RecordingClient()).get_market_breadth(date(2026, 8, 26))
    assert "websocket" in str(exc.value)


def test_provider_builds_without_credentials():
    assert DnseFlowProvider("dnse_foreign_flow", 2, {}).is_fixture is False


# -- storage -----------------------------------------------------------------

def test_flows_round_trip(db):
    upsert_flows([_record()], path=db)
    frame = load_flows("SSI", path=db)
    assert len(frame) == 1
    assert frame["foreign_net_value"].iloc[-1] == pytest.approx(40.0)


def test_rewriting_a_session_updates_in_place(db):
    upsert_flows([_record(buy=100.0)], path=db)
    upsert_flows([_record(buy=200.0)], path=db)
    frame = load_flows("SSI", path=db)
    assert len(frame) == 1
    assert frame["foreign_buy_value"].iloc[-1] == pytest.approx(200.0)


def test_stored_dates_are_reported_for_incremental_collection(db):
    upsert_flows([_record(day=date(2026, 8, 25)), _record(day=date(2026, 8, 26))], path=db)
    held = stored_flow_dates("TICKER", "SSI", date(2026, 8, 1), date(2026, 8, 31), path=db)
    assert held == {date(2026, 8, 25), date(2026, 8, 26)}


def test_breadth_attaches_to_an_existing_index_session_only(db):
    from src.collectors.base import OHLCV
    from src.collectors.market_store import upsert_index_bars

    bar = OHLCV(
        symbol="VNINDEX", trade_date=date(2026, 8, 27), open=1.0, high=1.0, low=1.0,
        close=1.0, volume=1, value_vnd=None,
        provenance=Provenance("dnse_openapi", SourceLevel.DATA_VENDOR, datetime.now(timezone.utc)),
    )
    upsert_index_bars([bar], path=db)

    assert update_breadth("VNINDEX", date(2026, 8, 27), 144, 158, 67, path=db) == 1
    # No bar for that date means no row to attach to -- and inventing an index
    # row with breadth but no prices would be worse than recording nothing.
    assert update_breadth("VNINDEX", date(2026, 8, 20), 1, 1, 1, path=db) == 0

    with connect(db) as conn:
        row = conn.execute("SELECT advancers, decliners FROM index_daily WHERE trade_date='2026-08-27'").fetchone()
    assert (row["advancers"], row["decliners"]) == (144, 158)


# -- the run -----------------------------------------------------------------

def test_flow_collection_skips_sessions_already_held(db, monkeypatch):
    # The whole 30-day window is held, not just its tail: a gap anywhere in it
    # has to be refetched, so "up to date" means every session, not the newest.
    upsert_flows(
        [_record(day=day) for day in sessions_between(date(2026, 7, 22), date(2026, 8, 21))],
        path=db,
    )
    calls = []

    def fake_fetch(kind, call, capability=None):
        calls.append(capability)
        from src.collectors.registry import FetchResult

        return FetchResult(value=[], source_id="dnse_foreign_flow", stale=False, errors={})

    monkeypatch.setattr("src.collectors.runner.fetch_with_fallback", fake_fetch)
    report = collect_foreign_flows(
        lookback_days=30, tickers=["SSI"], end=date(2026, 8, 21),
        now=datetime(2026, 8, 28, 16, 0, tzinfo=VN_TZ), path=db,
    )
    # Everything in the window is already stored and settled, so nothing was asked for.
    assert calls == []
    assert "up to date" in report.outcomes[0].note


def test_a_gap_inside_the_stored_range_is_refetched(db, monkeypatch):
    # Sessions 2026-08-10..14 are held, the days before and after are not.
    upsert_flows(
        [_record(day=day) for day in sessions_between(date(2026, 8, 10), date(2026, 8, 14))],
        path=db,
    )
    requested = []

    class Recorder:
        def get_flows(self, scope, key, start, end):
            requested.append((start, end))
            return []

    def fake_fetch(kind, call, capability=None):
        from src.collectors.registry import FetchResult

        return FetchResult(value=call(Recorder()), source_id="dnse_foreign_flow", stale=False, errors={})

    monkeypatch.setattr("src.collectors.runner.fetch_with_fallback", fake_fetch)
    collect_foreign_flows(
        lookback_days=12, tickers=["SSI"], end=date(2026, 8, 18),
        now=datetime(2026, 8, 28, 16, 0, tzinfo=VN_TZ), path=db,
    )

    # Two runs: the days before the stored block, and the days after it.
    assert requested == [(date(2026, 8, 6), date(2026, 8, 7)), (date(2026, 8, 17), date(2026, 8, 18))]


def test_an_unsettled_session_is_refetched(db):
    # Today's stored record is a running total, so it must not be treated as
    # final just because a row exists.
    today = date(2026, 8, 28)
    upsert_flows([_record(day=today)], path=db)
    held = {
        day
        for day in stored_flow_dates("TICKER", "SSI", date(2026, 8, 1), today, path=db)
        if session_is_complete(day, now=datetime(2026, 8, 28, 11, 0, tzinfo=VN_TZ))
    }
    assert held == set()


def test_fixture_flow_is_never_persisted(db):
    report = collect_foreign_flows(lookback_days=5, tickers=["SSI"], path=db)
    assert report.fixture_only is True
    assert load_flows("SSI", path=db).empty


# -- features ----------------------------------------------------------------

def flow_frame(nets, end=date(2026, 8, 27)):
    index = pd.bdate_range(end=pd.Timestamp(end), periods=len(nets))
    buys = [abs(n) + 10.0 for n in nets]
    frame = pd.DataFrame(
        {
            "foreign_buy_value": buys,
            "foreign_sell_value": [b - n for b, n in zip(buys, nets)],
            "foreign_net_value": [float(n) for n in nets],
            "prop_net_value": None,
            "etf_net_value": None,
            "total_match_value": None,
        },
        index=index,
    )
    frame.index.name = "trade_date"
    return frame


def test_streak_counts_consecutive_buying():
    assert net_streak(pd.Series([-5.0, 3.0, 4.0, 1.0])) == 3


def test_streak_is_negative_while_selling():
    assert net_streak(pd.Series([5.0, -3.0, -4.0])) == -2


def test_a_flat_session_breaks_the_run():
    assert net_streak(pd.Series([3.0, 4.0, 0.0])) == 0


def test_streak_of_nothing_is_none():
    assert net_streak(pd.Series([], dtype="float64")) is None


def test_short_history_leaves_the_long_windows_empty():
    vector = compute_flow_features(flow_frame([1.0] * 6), ticker="SSI")
    assert vector.features["foreign_net_5d"] is not None
    # Six sessions summed and labelled "20d" would understate every later
    # comparison against it.
    assert vector.features["foreign_net_20d"] is None


def test_full_history_computes_the_long_windows():
    vector = compute_flow_features(flow_frame([2.0] * 25), ticker="SSI")
    assert vector.features["foreign_net_20d"] == pytest.approx(40.0)
    assert vector.features["foreign_buy_share_20d"] > 0.5


def test_buy_share_is_bounded_and_balanced_at_half():
    balanced = flow_frame([0.0] * 25)
    assert compute_flow_features(balanced, ticker="SSI").features["foreign_buy_share_20d"] == pytest.approx(0.5)


def test_net_as_share_of_turnover_needs_prices():
    flows = flow_frame([10.0] * 25)
    without = compute_flow_features(flows, prices=None, ticker="SSI")
    assert without.features["foreign_net_pct_turnover_20d"] is None


def test_net_as_share_of_turnover_uses_the_stocks_own_volume():
    flows = flow_frame([10.0] * 25)
    prices = pd.DataFrame(
        {"close": [100.0] * 25, "adj_close": [100.0] * 25, "volume": [1.0] * 25},
        index=flows.index,
    )
    vector = compute_flow_features(flows, prices=prices, ticker="SSI")
    # 20 sessions x net 10 = 200, against 20 x (100 x 1) = 2000 of turnover.
    assert vector.features["foreign_net_pct_turnover_20d"] == pytest.approx(10.0)


def test_empty_flow_history_yields_an_empty_vector():
    vector = compute_flow_features(flow_frame([]), ticker="SSI")
    assert vector.trade_date is None
    assert vector.completeness == 0.0


def test_compute_for_ticker_excludes_the_unsettled_session(db):
    upsert_flows(
        [_record(day=day) for day in sessions_between(date(2026, 8, 3), date(2026, 8, 28))],
        path=db,
    )
    vector = compute_for_ticker("SSI", path=db, now=datetime(2026, 8, 28, 11, 0, tzinfo=VN_TZ))
    assert vector.trade_date == date(2026, 8, 27)
    assert vector.partial_session_excluded is True


def test_compute_for_ticker_without_data_says_what_to_run(db):
    with pytest.raises(ValueError) as exc:
        compute_for_ticker("SSI", path=db)
    assert "collect" in str(exc.value)


def test_a_session_read_intraday_is_refetched_after_it_closes(db, monkeypatch):
    """The closing bell does not settle a record captured at lunchtime."""
    today = date(2026, 8, 28)
    midday = FlowRecord(
        scope="TICKER", scope_key="SSI", trade_date=today,
        foreign_buy_value=10.0, foreign_sell_value=5.0, foreign_net_value=5.0,
        provenance=Provenance(
            "dnse_foreign_flow", SourceLevel.DATA_VENDOR,
            datetime(2026, 8, 28, 6, 33, tzinfo=timezone.utc),   # 13:33 in Vietnam
        ),
        boards_split=True,
    )
    upsert_flows([midday], path=db)

    requested = []

    class Recorder:
        def get_flows(self, scope, key, start, end):
            requested.append((start, end))
            return []

    def fake_fetch(kind, call, capability=None):
        from src.collectors.registry import FetchResult

        return FetchResult(value=call(Recorder()), source_id="dnse_foreign_flow", stale=False, errors={})

    monkeypatch.setattr("src.collectors.runner.fetch_with_fallback", fake_fetch)
    collect_foreign_flows(
        lookback_days=0, tickers=["SSI"], end=today,
        now=datetime(2026, 8, 28, 16, 0, tzinfo=VN_TZ), path=db,
    )
    assert requested == [(today, today)]


def test_a_session_read_after_its_close_is_left_alone(db, monkeypatch):
    today = date(2026, 8, 28)
    settled = FlowRecord(
        scope="TICKER", scope_key="SSI", trade_date=today,
        foreign_buy_value=10.0, foreign_sell_value=5.0, foreign_net_value=5.0,
        provenance=Provenance(
            "dnse_foreign_flow", SourceLevel.DATA_VENDOR,
            datetime(2026, 8, 28, 8, 40, tzinfo=timezone.utc),   # 15:40 in Vietnam
        ),
        boards_split=True,
    )
    upsert_flows([settled], path=db)

    called = []
    monkeypatch.setattr(
        "src.collectors.runner.fetch_with_fallback",
        lambda kind, call, capability=None: called.append(1),
    )
    report = collect_foreign_flows(
        lookback_days=0, tickers=["SSI"], end=today,
        now=datetime(2026, 8, 28, 16, 0, tzinfo=VN_TZ), path=db,
    )
    assert called == []
    assert "up to date" in report.outcomes[0].note


# -- the matched board beside the day totals (2026-10-13) ------------------------------------

def test_the_matched_board_is_kept_beside_the_totals_that_include_put_through():
    """HDB 06/10/2026: totals sold 76,603,547 (G1 2,299,100 + put-through 74,304,352 + odd lot 95)."""
    client = RecordingClient(default=snapshot(87_876_890_950, 2_045_523_747_850, totalBuyVolume=3_157_848,
                                         totalSellVolume=76_603_547, buyVolume=3_157_800, sellVolume=2_299_100,
                                         buyTradedAmount=87_875_000_000, sellTradedAmount=62_000_000_000))
    [rec] = _provider(client).get_flows("TICKER", "HDB", date(2026, 10, 6), date(2026, 10, 6))
    assert (rec.foreign_buy_volume, rec.foreign_sell_volume) == (3_157_848, 76_603_547)
    assert (rec.foreign_buy_volume_matched, rec.foreign_sell_volume_matched) == (3_157_800, 2_299_100)
    assert rec.foreign_sell_value_matched == 62_000_000_000 and rec.boards_split


def test_a_session_stored_before_the_board_split_is_read_again(db):
    from dataclasses import replace
    old = replace(_record(day=date(2026, 8, 25)), boards_split=False)
    upsert_flows([old, _record(day=date(2026, 8, 26))], path=db)
    assert stored_flow_dates("TICKER", "SSI", date(2026, 8, 20), date(2026, 8, 31), path=db) == {date(2026, 8, 26)}
