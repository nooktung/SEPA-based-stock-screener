"""Market regime classification: the axes, the decision table, confidence."""

import json
from datetime import date, datetime, timezone

import pytest

from src.collectors.base import OHLCV, FlowRecord, Provenance, SourceLevel
from src.collectors.market_store import update_breadth, upsert_flows, upsert_index_bars
from src.config import load_config
from src.database import connect, init_db, sync_reference_data
from src.market_calendar import VN_TZ
from src.quant.regime import (
    BEAR,
    BULL,
    DISTRIBUTION,
    EARLY_BEAR,
    EARLY_BULL,
    RECOVERY,
    SIDEWAYS,
    STRONG_BULL,
    MOMENTUM_FLAT,
    MOMENTUM_NEGATIVE,
    MOMENTUM_POSITIVE,
    PARTICIPATION_BROAD,
    PARTICIPATION_NARROW,
    PARTICIPATION_WEAK,
    TREND_DOWN,
    TREND_MIXED,
    TREND_UP,
    RegimeConfig,
    RegimeInputs,
    assess_regime,
    classify,
    classify_momentum,
    classify_participation,
    classify_trend,
    gather_inputs,
    store_regime,
)

TRADE_DATE = date(2026, 8, 27)


@pytest.fixture
def config():
    return RegimeConfig.from_thresholds()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    import src.database as database

    monkeypatch.setattr(database, "db_path", lambda: path)
    init_db(path)
    sync_reference_data(path)
    return path


def inputs(**overrides) -> RegimeInputs:
    """A complete, unremarkable uptrend unless told otherwise."""
    base = dict(
        close=1800.0, ma50=1750.0, ma200=1700.0, roc_20d=5.0,
        breadth_ratio=1.0, volume_vs_ma20=1.2, atr_percentile=40.0,
        realized_vol_20d_pct=18.0, foreign_net_20d=None,
    )
    base.update(overrides)
    return RegimeInputs(**base)


def verdict(config, **overrides) -> str:
    return classify(inputs(**overrides), config, TRADE_DATE).regime


# -- configuration -----------------------------------------------------------

def test_thresholds_drive_the_classifier(config):
    configured = load_config().thresholds["market_regime"]
    assert config.breadth_bullish_ratio == configured["breadth_bullish_ratio"]
    assert config.breadth_bearish_ratio == configured["breadth_bearish_ratio"]
    assert config.momentum_flat_band_pct == configured["momentum_flat_band_pct"]


def test_every_reachable_state_is_declared_in_config(config):
    reachable = {STRONG_BULL, BULL, EARLY_BULL, RECOVERY, SIDEWAYS, DISTRIBUTION, EARLY_BEAR, BEAR}
    assert reachable == set(config.states)


def test_every_state_has_a_risk_multiplier(config):
    for state in config.states:
        assert state in config.risk_multiplier_by_regime


# -- the axes ----------------------------------------------------------------

def test_uptrend_needs_price_and_the_long_average_to_agree():
    assert classify_trend(1800, 1750, 1700) == TREND_UP


def test_price_above_a_falling_long_average_is_not_an_uptrend():
    # A rally inside a downtrend. Calling it UP would let the table reach
    # STRONG_BULL from a bear market bounce.
    assert classify_trend(1800, 1650, 1700) == TREND_MIXED


def test_downtrend_needs_both_too():
    assert classify_trend(1600, 1650, 1700) == TREND_DOWN


def test_trend_is_unknown_without_the_averages():
    assert classify_trend(1800, None, 1700) is None


def test_participation_reads_the_breadth_thresholds(config):
    assert classify_participation(2.0, config) == PARTICIPATION_BROAD
    assert classify_participation(1.0, config) == PARTICIPATION_NARROW
    assert classify_participation(0.5, config) == PARTICIPATION_WEAK
    assert classify_participation(None, config) is None


def test_momentum_has_a_dead_band(config):
    # Without it, a 0.3% drift over a month would be called a direction.
    assert classify_momentum(0.5, config) == MOMENTUM_FLAT
    assert classify_momentum(5.0, config) == MOMENTUM_POSITIVE
    assert classify_momentum(-5.0, config) == MOMENTUM_NEGATIVE


# -- the decision table ------------------------------------------------------

def test_broad_uptrend_with_momentum_is_a_strong_bull(config):
    assert verdict(config, breadth_ratio=2.0, roc_20d=8.0) == STRONG_BULL


def test_uptrend_without_broad_participation_is_only_a_bull(config):
    assert verdict(config, breadth_ratio=1.0, roc_20d=8.0) == BULL


def test_a_rising_index_on_a_falling_market_is_distribution(config):
    # The case the whole participation axis exists for: four large caps hold
    # the index up while most stocks fall.
    assert verdict(config, breadth_ratio=0.4, roc_20d=8.0) == DISTRIBUTION


def test_an_uptrend_that_has_stopped_advancing_is_distribution(config):
    assert verdict(config, breadth_ratio=1.2, roc_20d=-6.0) == DISTRIBUTION


def test_downtrend_with_negative_momentum_is_a_bear(config):
    assert verdict(config, close=1600.0, ma50=1650.0, ma200=1700.0, roc_20d=-8.0) == BEAR


def test_a_broad_rally_inside_a_downtrend_is_a_recovery(config):
    assert verdict(
        config, close=1600.0, ma50=1650.0, ma200=1700.0, roc_20d=8.0, breadth_ratio=2.0
    ) == RECOVERY


def test_a_drifting_downtrend_is_an_early_bear(config):
    assert verdict(config, close=1600.0, ma50=1650.0, ma200=1700.0, roc_20d=0.5) == EARLY_BEAR


def test_crossed_averages_turning_up_is_an_early_bull(config):
    assert verdict(
        config, close=1800.0, ma50=1650.0, ma200=1700.0, roc_20d=8.0, breadth_ratio=1.2
    ) == EARLY_BULL


def test_crossed_averages_deteriorating_is_an_early_bear(config):
    assert verdict(
        config, close=1800.0, ma50=1650.0, ma200=1700.0, roc_20d=-8.0, breadth_ratio=0.4
    ) == EARLY_BEAR


def test_disagreement_is_sideways_not_a_guess(config):
    assert verdict(
        config, close=1800.0, ma50=1650.0, ma200=1700.0, roc_20d=0.0, breadth_ratio=1.0
    ) == SIDEWAYS


def test_without_a_trend_it_makes_no_directional_call(config):
    assessment = classify(inputs(ma200=None), config, TRADE_DATE)
    assert assessment.regime == SIDEWAYS
    assert "unavailable" in assessment.reasons[0]


def test_the_verdict_carries_its_reasons(config):
    assessment = classify(inputs(breadth_ratio=0.4), config, TRADE_DATE)
    assert assessment.reasons
    assert any("falling" in reason for reason in assessment.reasons)


# -- confidence --------------------------------------------------------------

def test_agreement_raises_confidence_and_conflict_lowers_it(config):
    agreeing = classify(inputs(breadth_ratio=2.0, roc_20d=8.0), config, TRADE_DATE)
    conflicting = classify(inputs(breadth_ratio=0.4, roc_20d=-8.0), config, TRADE_DATE)
    assert agreeing.confidence > conflicting.confidence


def test_missing_inputs_cost_confidence(config):
    complete = classify(inputs(), config, TRADE_DATE)
    partial = classify(inputs(breadth_ratio=None), config, TRADE_DATE)
    assert partial.confidence < complete.confidence
    assert partial.inputs.missing == ["breadth_ratio"]


def test_high_volatility_shortens_the_shelf_life_of_the_call(config):
    calm = classify(inputs(atr_percentile=30.0), config, TRADE_DATE)
    stormy = classify(inputs(atr_percentile=95.0), config, TRADE_DATE)
    assert stormy.confidence < calm.confidence
    # The state is the same call; only the firmness changed.
    assert stormy.regime == calm.regime


def test_thin_volume_lowers_confidence(config):
    healthy = classify(inputs(volume_vs_ma20=1.4), config, TRADE_DATE)
    thin = classify(inputs(volume_vs_ma20=0.5), config, TRADE_DATE)
    assert thin.confidence < healthy.confidence


def test_confidence_stays_inside_its_bounds(config):
    empty = classify(RegimeInputs(), config, TRADE_DATE)
    assert 0.0 <= empty.confidence <= 100.0


# -- consequences ------------------------------------------------------------

def test_hostile_regimes_block_buying(config):
    blocked = load_config().thresholds["recommendation_gates"]["block_buy_in_regimes"]
    bear = classify(inputs(close=1600.0, ma50=1650.0, ma200=1700.0, roc_20d=-8.0), config, TRADE_DATE)
    assert bear.regime in blocked
    assert bear.blocks_buying is True


def test_a_bull_does_not_block_buying(config):
    assert classify(inputs(breadth_ratio=2.0, roc_20d=8.0), config, TRADE_DATE).blocks_buying is False


def test_the_risk_multiplier_comes_from_config(config):
    assessment = classify(inputs(breadth_ratio=2.0, roc_20d=8.0), config, TRADE_DATE)
    assert assessment.risk_multiplier == config.risk_multiplier_by_regime[STRONG_BULL]


# -- persistence -------------------------------------------------------------

def _index_bar(day, close, volume=200_000_000):
    return OHLCV(
        symbol="VNINDEX", trade_date=day, open=close, high=close * 1.005, low=close * 0.995,
        close=close, volume=volume, value_vnd=None,
        provenance=Provenance("dnse_openapi", SourceLevel.DATA_VENDOR, datetime.now(timezone.utc)),
    )


def test_the_full_input_vector_is_kept_for_audit(db, config):
    assessment = classify(inputs(breadth_ratio=0.4), config, TRADE_DATE)
    store_regime(assessment, path=db)

    with connect(db) as conn:
        row = conn.execute("SELECT * FROM market_regime_history").fetchone()
    assert row["regime"] == DISTRIBUTION
    stored = json.loads(row["inputs_json"])
    # A classification that cannot be reconstructed later is an opinion, not
    # evidence -- so the inputs and the reasoning travel with the verdict.
    assert stored["inputs"]["breadth_ratio"] == pytest.approx(0.4)
    assert stored["trend"] == TREND_UP
    assert stored["reasons"]


def test_reclassifying_a_session_overwrites_it(db, config):
    store_regime(classify(inputs(), config, TRADE_DATE), path=db)
    store_regime(classify(inputs(breadth_ratio=2.0, roc_20d=9.0), config, TRADE_DATE), path=db)
    with connect(db) as conn:
        rows = conn.execute("SELECT regime FROM market_regime_history").fetchall()
    assert [row["regime"] for row in rows] == [STRONG_BULL]


# -- gathering ---------------------------------------------------------------

def _seed_index(db, days, start=1700.0, step=2.0):
    bars = [_index_bar(day, start + index * step) for index, day in enumerate(days)]
    upsert_index_bars(bars, path=db)


def test_gathering_excludes_an_unfinished_session(db):
    import pandas as pd

    days = [stamp.date() for stamp in pd.bdate_range(end=pd.Timestamp("2026-08-28"), periods=260)]
    _seed_index(db, days)

    _, trade_date, dropped = gather_inputs(
        path=db, now=datetime(2026, 8, 28, 11, 0, tzinfo=VN_TZ)
    )
    assert trade_date == date(2026, 8, 27)
    assert dropped is True


def test_breadth_is_read_from_the_session_being_classified(db):
    import pandas as pd

    days = [stamp.date() for stamp in pd.bdate_range(end=pd.Timestamp("2026-08-27"), periods=260)]
    _seed_index(db, days)
    update_breadth("VNINDEX", date(2026, 8, 27), 200, 100, 50, path=db)

    gathered, _, _ = gather_inputs(path=db, now=datetime(2026, 8, 28, 16, 0, tzinfo=VN_TZ))
    assert gathered.breadth_ratio == pytest.approx(2.0)


def test_watchlist_flow_is_not_passed_off_as_market_flow(db):
    import pandas as pd

    days = [stamp.date() for stamp in pd.bdate_range(end=pd.Timestamp("2026-08-27"), periods=260)]
    _seed_index(db, days)
    # Plenty of per-ticker flow, but nothing at MARKET scope. Summing five
    # brokers would describe the watchlist, not the market.
    upsert_flows(
        [
            FlowRecord(
                scope="TICKER", scope_key="SSI", trade_date=day,
                foreign_buy_value=100.0, foreign_sell_value=50.0, foreign_net_value=50.0,
                provenance=Provenance("dnse_foreign_flow", SourceLevel.DATA_VENDOR,
                                      datetime.now(timezone.utc)),
            )
            for day in days[-25:]
        ],
        path=db,
    )
    gathered, _, _ = gather_inputs(path=db, now=datetime(2026, 8, 28, 16, 0, tzinfo=VN_TZ))
    assert gathered.foreign_net_20d is None


def test_assessment_without_any_index_history_says_what_to_run(db):
    with pytest.raises(ValueError) as exc:
        assess_regime(path=db)
    assert "collect" in str(exc.value)


def test_a_real_looking_series_classifies_end_to_end(db):
    import pandas as pd

    days = [stamp.date() for stamp in pd.bdate_range(end=pd.Timestamp("2026-08-27"), periods=260)]
    # Steep enough to clear the momentum dead band: a 2.0-per-session drift on
    # a 2000-point index is under 2% over 20 sessions, which is FLAT by design.
    _seed_index(db, days, step=8.0)
    update_breadth("VNINDEX", date(2026, 8, 27), 220, 90, 60, path=db)

    assessment = assess_regime(path=db, now=datetime(2026, 8, 28, 16, 0, tzinfo=VN_TZ))
    # A steadily rising index with broad participation.
    assert assessment.regime == STRONG_BULL
    assert assessment.trend == TREND_UP
    assert assessment.participation == PARTICIPATION_BROAD
    assert assessment.momentum == MOMENTUM_POSITIVE
