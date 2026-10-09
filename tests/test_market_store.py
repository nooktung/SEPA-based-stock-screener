"""Persistence rules: provenance kept, fixtures kept out, staleness recorded."""

from datetime import date, datetime, timezone

import pytest

from src.collectors.base import OHLCV, Provenance, SourceLevel
from src.collectors.market_store import (
    bar_disagrees_with_tape,
    DATASET_PRICES_DAILY,
    StoreError,
    count_bars,
    latest_bar_date,
    mark_freshness,
    record_source_outcome,
    suspicious_gaps,
    upsert_daily_bars,
    upsert_index_bars,
)
from src.collectors.runner import collect_market_data
from src.database import connect, init_db, sync_reference_data

OBSERVED = datetime(2026, 8, 26, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def db(tmp_path, monkeypatch):
    """A schema-loaded database with the watchlist and sources already synced."""
    path = tmp_path / "test.db"
    import src.database as database

    monkeypatch.setattr(database, "db_path", lambda: path)
    init_db(path)
    sync_reference_data(path)
    return path


def _bar(symbol="SSI", day=date(2026, 8, 26), close=24_300.0, source_id="dnse_openapi", volume=1000):
    return OHLCV(
        symbol=symbol,
        trade_date=day,
        open=close - 100,
        high=close + 200,
        low=close - 300,
        close=close,
        volume=volume,
        value_vnd=None,
        provenance=Provenance(
            source_id=source_id,
            source_level=SourceLevel.DATA_VENDOR,
            observed_at=OBSERVED,
            source_url="https://openapi.dnse.com.vn/price/ohlc",
        ),
    )


# -- writes ------------------------------------------------------------------

def test_bars_are_written_with_their_provenance(db):
    upsert_daily_bars([_bar()], path=db)
    with connect(db) as conn:
        row = conn.execute("SELECT * FROM prices_daily WHERE ticker='SSI'").fetchone()
    assert row["close"] == 24_300.0
    assert row["source_id"] == "dnse_openapi"
    assert row["observed_at"].startswith("2026-08-26T12:00:00")


def test_rewriting_a_session_updates_it_in_place(db):
    upsert_daily_bars([_bar(close=24_300.0)], path=db)
    upsert_daily_bars([_bar(close=24_500.0)], path=db)
    assert count_bars("SSI", path=db) == 1
    with connect(db) as conn:
        assert conn.execute("SELECT close FROM prices_daily WHERE ticker='SSI'").fetchone()[0] == 24_500.0


def test_a_bar_for_an_unknown_ticker_is_refused(db):
    # The foreign key exists so a typo cannot quietly create a shadow ticker.
    with pytest.raises(StoreError) as exc:
        upsert_daily_bars([_bar(symbol="ZZZZ")], path=db)
    assert "initdb" in str(exc.value)


def test_latest_bar_date_reads_back_what_was_written(db):
    upsert_daily_bars([_bar(day=date(2026, 8, 25)), _bar(day=date(2026, 8, 26))], path=db)
    assert latest_bar_date("SSI", path=db) == date(2026, 8, 26)


def test_latest_bar_date_is_none_for_an_empty_table(db):
    assert latest_bar_date("SSI", path=db) is None


def test_index_bars_go_to_their_own_table(db):
    upsert_index_bars([_bar(symbol="VNINDEX", close=1686.31)], path=db)
    with connect(db) as conn:
        row = conn.execute("SELECT close, advancers FROM index_daily WHERE symbol='VNINDEX'").fetchone()
    assert row["close"] == 1686.31
    # Breadth belongs to the flow source; an OHLC write must not blank it.
    assert row["advancers"] is None


def test_empty_input_writes_nothing(db):
    assert upsert_daily_bars([], path=db) == 0
    assert upsert_index_bars([], path=db) == 0


# -- freshness and source health --------------------------------------------

def test_successful_refresh_clears_staleness(db):
    mark_freshness(DATASET_PRICES_DAILY, "SSI", stale=False, path=db)
    with connect(db) as conn:
        row = conn.execute("SELECT * FROM data_freshness WHERE scope_key='SSI'").fetchone()
    assert row["is_stale"] == 0
    assert row["last_updated_at"]


def test_failure_marks_stale_without_touching_the_last_success_time(db):
    mark_freshness(DATASET_PRICES_DAILY, "SSI", stale=False, updated_at="2026-08-20T01:00:00", path=db)
    mark_freshness(DATASET_PRICES_DAILY, "SSI", stale=True, path=db)
    with connect(db) as conn:
        row = conn.execute("SELECT * FROM data_freshness WHERE scope_key='SSI'").fetchone()
    assert row["is_stale"] == 1
    assert row["last_updated_at"] == "2026-08-20T01:00:00"


def test_source_health_counters_move_in_both_directions(db):
    record_source_outcome("dnse_openapi", ok=False, path=db)
    record_source_outcome("dnse_openapi", ok=False, path=db)
    with connect(db) as conn:
        row = conn.execute("SELECT * FROM sources WHERE source_id='dnse_openapi'").fetchone()
    assert row["consecutive_fails"] == 2
    assert row["last_failure_at"]

    record_source_outcome("dnse_openapi", ok=True, path=db)
    with connect(db) as conn:
        row = conn.execute("SELECT * FROM sources WHERE source_id='dnse_openapi'").fetchone()
    assert row["consecutive_fails"] == 0
    assert row["last_success_at"]


# -- adjustment check --------------------------------------------------------

def test_large_overnight_move_is_flagged():
    bars = [
        _bar(day=date(2026, 8, 24), close=30_000.0),
        _bar(day=date(2026, 8, 25), close=25_000.0),  # -16.7%: impossible in one HOSE session
    ]
    flagged = suspicious_gaps(bars, exchange="HOSE")
    assert flagged and flagged[0][0] == date(2026, 8, 25)
    assert flagged[0][1] == pytest.approx(-16.67, abs=0.01)


def test_normal_sessions_are_not_flagged():
    bars = [
        _bar(day=date(2026, 8, 24), close=30_000.0),
        _bar(day=date(2026, 8, 25), close=31_500.0),  # +5%, inside the daily band
    ]
    assert suspicious_gaps(bars, exchange="HOSE") == []


def test_the_band_is_read_per_exchange():
    # A 9% session is impossible on HOSE and an ordinary day on HNX. One
    # threshold for both reports every busy HNX session as a corporate action.
    bars = [
        _bar(day=date(2026, 8, 24), close=10_000.0),
        _bar(day=date(2026, 8, 25), close=10_900.0),  # +9%
    ]
    assert suspicious_gaps(bars, exchange="HOSE")
    assert suspicious_gaps(bars, exchange="HNX") == []


def test_upcom_has_the_widest_band():
    bars = [
        _bar(day=date(2026, 8, 24), close=10_000.0),
        _bar(day=date(2026, 8, 25), close=11_300.0),  # +13%
    ]
    assert suspicious_gaps(bars, exchange="HNX")
    assert suspicious_gaps(bars, exchange="UPCOM") == []


def test_an_unknown_venue_flags_nothing_rather_than_guessing():
    # Indices have no price band at all, and a detector that does not know the
    # rules should stay quiet instead of inventing a threshold.
    bars = [
        _bar(day=date(2026, 8, 24), close=30_000.0),
        _bar(day=date(2026, 8, 25), close=15_000.0),
    ]
    assert suspicious_gaps(bars) == []
    assert suspicious_gaps(bars, exchange="NASDAQ") == []
    assert suspicious_gaps(bars, threshold_pct=10.0)


# -- the run -----------------------------------------------------------------

def test_fixture_data_is_never_persisted(db):
    """The whole point of the fixture flag: synthetic bars stay out of the DB."""
    report = collect_market_data(lookback_days=10, path=db, persist=True)

    assert report.fixture_only is True
    assert report.bars_written == 0
    assert count_bars("SSI", path=db) == 0
    with connect(db) as conn:
        stale = conn.execute("SELECT COUNT(*) FROM data_freshness WHERE is_stale=1").fetchone()[0]
    assert stale > 0


def test_every_run_is_recorded(db):
    report = collect_market_data(lookback_days=10, path=db, persist=True)
    with connect(db) as conn:
        row = conn.execute("SELECT * FROM collection_runs WHERE run_id=?", (report.run_id,)).fetchone()
    assert row["job_type"] == "MORNING"
    assert row["status"] in ("OK", "PARTIAL", "FAILED")


def test_dry_run_writes_nothing_at_all(db):
    report = collect_market_data(lookback_days=10, path=db, persist=False)
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*) FROM collection_runs").fetchone()[0] == 0
        assert conn.execute("SELECT COUNT(*) FROM data_freshness").fetchone()[0] == 0
    assert report.outcomes


def test_run_covers_watchlist_and_benchmarks(db):
    report = collect_market_data(lookback_days=5, path=db, persist=False)
    scopes = {o.scope for o in report.outcomes}
    assert {"SSI", "VIX", "VND", "VCI", "HCM"} <= scopes
    assert "VNINDEX" in scopes


def test_explicit_ticker_selection_is_honoured(db):
    report = collect_market_data(lookback_days=5, tickers=["SSI"], benchmarks=[], path=db, persist=False)
    assert [o.scope for o in report.outcomes] == ["SSI"]


# -- settled-bar verification ------------------------------------------------

def _tape(close=33_400.0, high=33_650.0, low=32_700.0, volume=27_675_200):
    return OHLCV(
        symbol="TCB", trade_date=date(2026, 8, 28), open=33_350.0, high=high, low=low,
        close=close, volume=volume, value_vnd=None,
        provenance=Provenance("dnse_openapi", SourceLevel.DATA_VENDOR, OBSERVED),
    )


def test_a_settled_bar_agrees_with_the_tape():
    bar = _bar(symbol="TCB", day=date(2026, 8, 28), close=33_400.0)
    settled = OHLCV(
        symbol="TCB", trade_date=date(2026, 8, 28), open=33_350.0, high=33_650.0,
        low=32_700.0, close=33_400.0, volume=27_675_200, value_vnd=None,
        provenance=bar.provenance,
    )
    assert bar_disagrees_with_tape(settled, _tape()) is None


def test_a_bar_closing_away_from_the_last_match_has_not_settled():
    # Observed live: the candle read 22,550 while the tape's last match was
    # 22,850. The candle was still catching up half an hour after the close.
    lagging = OHLCV(
        symbol="VCI", trade_date=date(2026, 8, 28), open=22_600.0, high=22_600.0,
        low=22_550.0, close=22_550.0, volume=50_700, value_vnd=None,
        provenance=Provenance("dnse_openapi", SourceLevel.DATA_VENDOR, OBSERVED),
    )
    reason = bar_disagrees_with_tape(lagging, _tape(close=22_850.0, high=23_150.0, low=22_500.0))
    assert reason and "last match" in reason


def test_a_bar_narrower_than_the_session_has_not_settled():
    # Same close, but the candle never saw the range that actually traded.
    narrow = OHLCV(
        symbol="SSI", trade_date=date(2026, 8, 28), open=21_300.0, high=21_350.0,
        low=21_300.0, close=21_350.0, volume=526_500, value_vnd=None,
        provenance=Provenance("dnse_openapi", SourceLevel.DATA_VENDOR, OBSERVED),
    )
    reason = bar_disagrees_with_tape(narrow, _tape(close=21_350.0, high=21_600.0, low=21_300.0))
    assert reason and "session high" in reason


def test_volume_alone_never_condemns_a_bar():
    # The two feeds have been seen to count volume in different units, which
    # is not evidence about settlement.
    bar = OHLCV(
        symbol="TCB", trade_date=date(2026, 8, 28), open=33_350.0, high=33_650.0,
        low=32_700.0, close=33_400.0, volume=2_767_520, value_vnd=None,
        provenance=Provenance("dnse_openapi", SourceLevel.DATA_VENDOR, OBSERVED),
    )
    assert bar_disagrees_with_tape(bar, _tape(volume=27_675_200)) is None


def test_missing_fields_do_not_condemn_a_bar():
    bar = OHLCV(
        symbol="TCB", trade_date=date(2026, 8, 28), open=None, high=None, low=None,
        close=None, volume=None, value_vnd=None,
        provenance=Provenance("dnse_openapi", SourceLevel.DATA_VENDOR, OBSERVED),
    )
    assert bar_disagrees_with_tape(bar, _tape()) is None


def test_a_bar_adjusted_ahead_of_an_ex_date_is_settled_not_lagging():
    # GMD 06/10/2026, evening before its 2:1 bonus issue went ex: DNSE had rescaled the whole
    # history by 2/3, today's bar included (52,000), while the tape still showed 78,000.
    from src.collectors.market_store import tape_adjustment_factor

    tape = OHLCV(symbol="GMD", trade_date=date(2026, 10, 6), open=77_800.0, high=78_500.0, low=77_500.0,
                 close=78_000.0, volume=1_000_000, value_vnd=None,
                 provenance=Provenance("dnse_openapi", SourceLevel.DATA_VENDOR, OBSERVED))
    adjusted = OHLCV(symbol="GMD", trade_date=date(2026, 10, 6), open=51_870.0, high=52_330.0, low=51_670.0,
                     close=52_000.0, volume=1_500_000, value_vnd=None, provenance=tape.provenance)
    assert tape_adjustment_factor(adjusted, tape) == pytest.approx(2 / 3, abs=2e-4)
    assert bar_disagrees_with_tape(adjusted, tape) is None
    # An unsettled bar is not uniformly scaled: its open still matches the tape.
    lagging = OHLCV(symbol="GMD", trade_date=date(2026, 10, 6), open=77_800.0, high=78_000.0, low=77_500.0,
                    close=77_600.0, volume=300_000, value_vnd=None, provenance=tape.provenance)
    assert tape_adjustment_factor(lagging, tape) is None
    assert "last match" in bar_disagrees_with_tape(lagging, tape)
