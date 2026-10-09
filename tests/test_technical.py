"""The technical engine: config binding, insufficient history, session
completeness, and persistence of the feature vector.
"""

from datetime import date, datetime, time, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from src.collectors.base import OHLCV, Provenance, SourceLevel
from src.collectors.market_store import upsert_daily_bars, upsert_index_bars
from src.config import load_config
from src.database import connect, init_db, sync_reference_data
from src.quant.store import (
    load_features,
    load_prices,
    price_series,
    upsert_features,
    uses_adjusted_prices,
)
from src.quant.technical import (
    VN_TZ,
    FeatureVector,
    TechnicalConfig,
    compute_features,
    compute_for_ticker,
    drop_partial_session,
    run_technical,
    session_is_complete,
)


def price_frame(n=300, start=20_000.0, seed=11, end=date(2026, 8, 27), adjusted=True):
    """A deterministic OHLCV frame ending on `end`, ascending by trading day."""
    rng = np.random.default_rng(seed)
    closes = np.abs(start + np.cumsum(rng.normal(0, 150, size=n)))
    index = pd.bdate_range(end=pd.Timestamp(end), periods=n)
    frame = pd.DataFrame(
        {
            "open": closes * 0.995,
            "high": closes * 1.012,
            "low": closes * 0.988,
            "close": closes,
            "volume": rng.integers(1_000_000, 30_000_000, size=n).astype(float),
            "value_vnd": np.nan,
            "adj_close": closes if adjusted else np.nan,
            "adj_factor": 1.0,
        },
        index=index,
    )
    frame.index.name = "trade_date"
    return frame


@pytest.fixture
def config():
    return TechnicalConfig.from_thresholds()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    import src.database as database

    monkeypatch.setattr(database, "db_path", lambda: path)
    init_db(path)
    sync_reference_data(path)
    return path


def seed_prices(path, ticker="SSI", n=300, end=date(2026, 8, 27), symbol_is_index=False, seed=11):
    frame = price_frame(n=n, end=end, seed=seed)
    bars = [
        OHLCV(
            symbol=ticker,
            trade_date=stamp.date(),
            open=float(row["open"]),
            high=float(row["high"]),
            low=float(row["low"]),
            close=float(row["close"]),
            volume=int(row["volume"]),
            value_vnd=None,
            adj_close=float(row["adj_close"]),
            provenance=Provenance(
                source_id="dnse_openapi",
                source_level=SourceLevel.DATA_VENDOR,
                observed_at=datetime(2026, 8, 27, 12, 0, tzinfo=timezone.utc),
            ),
        )
        for stamp, row in frame.iterrows()
    ]
    if symbol_is_index:
        upsert_index_bars(bars, path=path)
    else:
        upsert_daily_bars(bars, path=path)
    return frame


# -- configuration binding ---------------------------------------------------

def test_periods_come_from_thresholds_not_from_code(config):
    configured = load_config().thresholds["technical"]
    assert config.rsi_period == configured["rsi_period"]
    assert config.ma_periods == tuple(configured["ma_periods"])
    assert config.macd_fast == configured["macd"]["fast"]
    assert config.bollinger_std == configured["bollinger"]["std"]


def test_feature_names_follow_the_configured_periods(config):
    vector = compute_features(price_frame(), config, "SSI")
    for period in config.ma_periods:
        assert f"ma{period}" in vector.features
    assert f"rsi{config.rsi_period}" in vector.features
    assert f"atr{config.atr_period}" in vector.features
    assert f"breakout_{config.breakout_lookback}d" in vector.features


def test_a_changed_config_changes_the_output(config):
    prices = price_frame()
    slower = TechnicalConfig.from_thresholds({**load_config().thresholds["technical"], "rsi_period": 28})
    assert compute_features(prices, config, "SSI").features["rsi14"] != pytest.approx(
        compute_features(prices, slower, "SSI").features["rsi28"]
    )


# -- the feature vector ------------------------------------------------------

def test_full_history_computes_every_feature(config):
    vector = compute_features(
        price_frame(n=300),
        config,
        "ssi",
        benchmark_prices=price_frame(n=300, start=1_600.0, seed=5),
        benchmark_name="VNINDEX",
    )
    assert vector.ticker == "SSI"
    assert vector.completeness == 1.0
    assert vector.missing == []
    assert len(vector.features) > 40


def test_short_history_leaves_long_features_empty_rather_than_shortening_them(config):
    # 60 sessions cannot produce a 200-day average. The alternative to None is
    # a 60-day average wearing the ma200 label, which is worse than nothing.
    vector = compute_features(price_frame(n=60), config, "SSI")
    assert vector.features["ma200"] is None
    assert vector.features["ma50"] is not None
    assert "ma200" in vector.missing
    assert 0.0 < vector.completeness < 1.0


def test_compact_drops_the_blanks_and_rounds(config):
    vector = compute_features(price_frame(n=60), config, "SSI")
    compact = vector.compact(digits=2)
    assert "ma200" not in compact
    assert all(value == round(value, 2) for value in compact.values())


def test_empty_history_is_an_error_not_an_empty_vector(config):
    with pytest.raises(ValueError):
        compute_features(price_frame(n=0), config, "SSI")


def test_flags_are_stored_as_numbers(config):
    vector = compute_features(price_frame(), config, "SSI")
    for name in ("ma_alignment_bullish", "vol_expansion", "gap_flag"):
        assert vector.features[name] in (0.0, 1.0)


def test_breakout_needs_a_close_above_the_prior_range(config):
    rising = price_frame(n=120)
    # Force a decisive new high on the last session.
    rising.iloc[-1, rising.columns.get_indexer(["close", "adj_close", "high"])] = (
        rising["close"].max() * 1.10
    )
    vector = compute_features(rising, config, "SSI")
    assert vector.features[f"breakout_{config.breakout_lookback}d"] == 1.0


def test_relative_strength_is_none_without_a_benchmark(config):
    vector = compute_features(price_frame(), config, "SSI", benchmark_prices=None, benchmark_name="VNINDEX")
    assert vector.features["rs_vnindex_20"] is None


def test_relative_strength_uses_the_benchmark_when_present(config):
    prices = price_frame(seed=3)
    benchmark = price_frame(seed=9, start=1_600.0)
    vector = compute_features(prices, config, "SSI", benchmark_prices=benchmark, benchmark_name="VNINDEX")
    assert vector.features["rs_vnindex_20"] is not None


def test_turnover_estimate_is_named_as_an_estimate(config):
    # The OHLC feed carries no traded value, so this is close x volume. The
    # name has to say so wherever it surfaces.
    vector = compute_features(price_frame(), config, "SSI")
    assert "avg_turnover_20d_est_vnd" in vector.features


def test_completeness_of_an_empty_vector_is_zero():
    assert FeatureVector(ticker="SSI", trade_date=date(2026, 8, 27)).completeness == 0.0


# -- adjusted prices ---------------------------------------------------------

def test_adjusted_closes_are_preferred(config):
    frame = price_frame()
    frame["adj_close"] = frame["close"] * 0.5
    assert price_series(frame).iloc[-1] == pytest.approx(frame["close"].iloc[-1] * 0.5)
    assert compute_features(frame, config, "SSI").adjusted_prices is True


def test_a_fully_unadjusted_frame_falls_back_to_close(config):
    frame = price_frame(adjusted=False)
    assert price_series(frame).equals(frame["close"])
    assert uses_adjusted_prices(frame) is False
    assert compute_features(frame, config, "SSI").adjusted_prices is False


def test_a_half_adjusted_frame_is_not_treated_as_adjusted(config):
    # Mixing adjusted and raw closes puts a step in the middle of every average.
    frame = price_frame()
    frame.iloc[:50, frame.columns.get_loc("adj_close")] = np.nan
    assert price_series(frame).equals(frame["close"])
    assert uses_adjusted_prices(frame) is False


# -- session completeness ----------------------------------------------------

def test_a_past_session_is_complete():
    now = datetime(2026, 8, 28, 9, 0, tzinfo=VN_TZ)
    assert session_is_complete(date(2026, 8, 27), now=now) is True


def test_todays_session_is_incomplete_before_the_close():
    now = datetime(2026, 8, 28, 12, 25, tzinfo=VN_TZ)
    assert session_is_complete(date(2026, 8, 28), now=now) is False


def test_todays_session_is_complete_after_the_close():
    now = datetime(2026, 8, 28, 15, 30, tzinfo=VN_TZ)
    assert session_is_complete(date(2026, 8, 28), now=now) is True


def test_completeness_is_judged_in_vietnam_time():
    # 23:00 UTC on the 27th is already 06:00 on the 28th in Ho Chi Minh City,
    # so the 28th has not closed -- a machine in London must agree.
    now = datetime(2026, 8, 27, 23, 0, tzinfo=timezone.utc)
    assert session_is_complete(date(2026, 8, 28), now=now) is False
    assert session_is_complete(date(2026, 8, 27), now=now) is True


def test_close_time_is_configurable():
    now = datetime(2026, 8, 28, 14, 0, tzinfo=VN_TZ)
    assert session_is_complete(date(2026, 8, 28), now=now, close_time=time(13, 0)) is True
    assert session_is_complete(date(2026, 8, 28), now=now, close_time=time(15, 0)) is False


def test_partial_session_is_dropped():
    frame = price_frame(n=10, end=date(2026, 8, 28))
    trimmed, dropped = drop_partial_session(frame, now=datetime(2026, 8, 28, 12, 0, tzinfo=VN_TZ))
    assert dropped is True
    assert len(trimmed) == len(frame) - 1


def test_a_closed_session_is_kept():
    frame = price_frame(n=10, end=date(2026, 8, 28))
    trimmed, dropped = drop_partial_session(frame, now=datetime(2026, 8, 28, 16, 0, tzinfo=VN_TZ))
    assert dropped is False
    assert len(trimmed) == len(frame)


# -- persistence -------------------------------------------------------------

def test_features_round_trip_through_the_database(db):
    seed_prices(db, n=30)
    upsert_features("SSI", date(2026, 8, 27), {"rsi14": 63.58, "ma200": None}, path=db)
    stored = load_features("SSI", date(2026, 8, 27), path=db)
    assert stored["rsi14"] == pytest.approx(63.58)
    # NULL means "computed, not enough history"; an absent row means "never
    # computed". The completeness score depends on telling them apart.
    assert "ma200" in stored and stored["ma200"] is None


def test_recomputing_a_session_overwrites_it(db):
    upsert_features("SSI", date(2026, 8, 27), {"rsi14": 10.0}, path=db)
    upsert_features("SSI", date(2026, 8, 27), {"rsi14": 20.0}, path=db)
    with connect(db) as conn:
        rows = conn.execute(
            "SELECT value FROM technical_features WHERE ticker='SSI' AND feature_name='rsi14'"
        ).fetchall()
    assert [row["value"] for row in rows] == [20.0]


def test_history_can_be_clipped_to_a_past_date(db):
    seed_prices(db, n=100, end=date(2026, 8, 27))
    clipped = load_prices("SSI", end=date(2026, 8, 20), path=db)
    assert clipped.index[-1].date() <= date(2026, 8, 20)


# -- the run -----------------------------------------------------------------

def test_run_computes_and_stores_for_the_watchlist(db):
    for ticker in ("SSI", "VIX", "VND", "VCI", "HCM"):
        seed_prices(db, ticker=ticker, n=300)
    seed_prices(db, ticker="VNINDEX", n=300, symbol_is_index=True)

    report = run_technical(path=db, now=datetime(2026, 8, 28, 16, 0, tzinfo=VN_TZ))

    # Only the seeded tickers can be computed. A watchlist member with no bars
    # is reported as a failure, not silently dropped from the run.
    seeded = {"SSI", "VIX", "VND", "VCI", "HCM"}
    assert report.ok_count == len(seeded)
    assert all(outcome.completeness == 1.0 for outcome in report.outcomes if outcome.ok)
    assert {outcome.ticker for outcome in report.failed} == set(
        load_config().watchlist.active_tickers
    ) - seeded
    assert load_features("SSI", date(2026, 8, 27), path=db)


def test_a_ticker_with_no_bars_is_reported_not_raised(db):
    report = run_technical(tickers=["SSI"], path=db)
    assert report.ok_count == 0
    assert "collect" in report.failed[0].error


def test_dry_run_computes_without_writing(db):
    seed_prices(db, n=300)
    report = run_technical(tickers=["SSI"], persist=False, path=db,
                           now=datetime(2026, 8, 28, 16, 0, tzinfo=VN_TZ))
    assert report.ok_count == 1
    assert report.outcomes[0].written == 0
    assert load_features("SSI", date(2026, 8, 27), path=db) == {}


def test_an_unfinished_session_is_excluded_and_said_so(db):
    seed_prices(db, n=300, end=date(2026, 8, 28))
    report = run_technical(tickers=["SSI"], path=db, now=datetime(2026, 8, 28, 11, 0, tzinfo=VN_TZ))
    outcome = report.outcomes[0]
    assert outcome.trade_date == date(2026, 8, 27)
    assert "unfinished" in outcome.note


def test_the_live_session_can_be_included_deliberately(db):
    seed_prices(db, n=300, end=date(2026, 8, 28))
    vector = compute_for_ticker(
        "SSI", include_partial_session=True, path=db,
        now=datetime(2026, 8, 28, 11, 0, tzinfo=VN_TZ),
    )
    assert vector.trade_date == date(2026, 8, 28)
    assert vector.partial_session_excluded is False
    # ...and the stored vector says so, so a consumer is never left guessing
    # whether the session behind these numbers had actually closed.
    assert vector.features["session_partial"] == 1.0


def test_a_settled_session_is_marked_as_settled(db):
    seed_prices(db, n=300, end=date(2026, 8, 27))
    vector = compute_for_ticker("SSI", path=db, now=datetime(2026, 8, 28, 11, 0, tzinfo=VN_TZ))
    assert vector.features["session_partial"] == 0.0
