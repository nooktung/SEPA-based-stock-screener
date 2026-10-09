"""Conflict detection (§28) and change reporting (§39)."""

from datetime import date

import pytest

from src.collectors.market_store import upsert_daily_bars
from src.database import init_db, sync_reference_data
from src.quant.behaviour import ACCUMULATION_LIKE, DISTRIBUTION_LIKE, NEUTRAL, BehaviourRead
from src.quant.changes import (
    APPEARED,
    DISAPPEARED,
    DOWN,
    SAME,
    UP,
    Change,
    ChangeReport,
    compare_feature_sets,
    compare_values,
    describe,
    previous_feature_date,
    what_changed,
)
from src.quant.conflicts import (
    SEVERITY_CRITICAL,
    SEVERITY_HIGH,
    SEVERITY_LOW,
    SEVERITY_MEDIUM,
    ConflictConfig,
    EvidenceBundle,
    detect_conflicts,
    highest_severity,
)
from src.quant.divergence import BEARISH, BULLISH, Divergence
from src.quant.levels import SIDE_RESISTANCE, SIDE_SUPPORT, Zone
from src.quant.patterns import (
    BEARISH_STRUCTURE,
    BULLISH_STRUCTURE,
    StructureRead,
    TrapRead,
)
from src.quant.store import upsert_features


@pytest.fixture
def config():
    return ConflictConfig.from_thresholds()


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = tmp_path / "test.db"
    import src.database as database

    monkeypatch.setattr(database, "db_path", lambda: path)
    init_db(path)
    sync_reference_data(path)
    return path


def bullish_bundle(**overrides):
    bundle = EvidenceBundle(
        features={
            "close": 100.0, "rsi14": 62.0, "macd_hist_delta": 5.0,
            "vol_ratio_20": 1.8, "roc_20d": 12.0, "price_vs_ma20_pct": 6.0,
        },
        structure=StructureRead(structure=BULLISH_STRUCTURE),
    )
    for key, value in overrides.items():
        setattr(bundle, key, value)
    return bundle


def resistance_at(distance_pct, touches=4):
    return Zone(
        zone_low=101.0, zone_high=103.0, center=102.0, source="SWING_HIGH",
        sources=["SWING_HIGH"], touch_count=touches,
        distance_from_price=distance_pct, side=SIDE_RESISTANCE,
    )


# -- conflicts ---------------------------------------------------------------

def test_strength_arriving_at_resistance_is_a_conflict(config):
    bundle = bullish_bundle(zones=[resistance_at(2.0)])
    conflicts = detect_conflicts(bundle, config)
    kinds = {c.conflict_type for c in conflicts}
    assert "TREND_VS_RESISTANCE" in kinds

    conflict = next(c for c in conflicts if c.conflict_type == "TREND_VS_RESISTANCE")
    assert conflict.bullish_evidence and conflict.bearish_evidence
    assert conflict.explanation


def test_trap_risk_raises_the_severity(config):
    calm = detect_conflicts(bullish_bundle(zones=[resistance_at(2.0)]), config)
    risky = detect_conflicts(
        bullish_bundle(zones=[resistance_at(2.0)], bull_trap=TrapRead(kind="BULL", score=80.0)),
        config,
    )
    assert highest_severity(risky) in (SEVERITY_HIGH, SEVERITY_CRITICAL)
    assert _severity_of(risky, "TREND_VS_RESISTANCE") != _severity_of(calm, "TREND_VS_RESISTANCE")


def test_an_extended_oscillator_at_resistance_is_critical(config):
    bundle = bullish_bundle(
        zones=[resistance_at(1.0)],
        bull_trap=TrapRead(kind="BULL", score=80.0),
    )
    bundle.features["rsi14"] = 78.0
    conflicts = detect_conflicts(bundle, config)
    assert _severity_of(conflicts, "TREND_VS_RESISTANCE") == SEVERITY_CRITICAL


def test_a_rally_on_contracting_volume_is_flagged(config):
    bundle = bullish_bundle()
    bundle.features["vol_ratio_20"] = 0.5
    conflicts = detect_conflicts(bundle, config)
    assert "PRICE_VS_VOLUME" in {c.conflict_type for c in conflicts}


def test_fading_momentum_under_intact_structure_is_flagged(config):
    bundle = bullish_bundle()
    bundle.features["macd_hist_delta"] = -12.0
    assert "TREND_VS_MOMENTUM" in {c.conflict_type for c in detect_conflicts(bundle, config)}


def test_a_bearish_divergence_against_a_rising_price_is_flagged(config):
    divergence = Divergence(
        kind=BEARISH, indicator="rsi", price_pivot_1=100.0, price_pivot_2=110.0,
        pivot_1_date=date(2026, 7, 1), pivot_2_date=date(2026, 8, 1),
        indicator_value_1=72.0, indicator_value_2=61.0,
        trade_date=date(2026, 8, 1), confirmed_on=date(2026, 8, 6), strength=60.0,
    )
    conflicts = detect_conflicts(bullish_bundle(divergences=[divergence]), config)
    conflict = next(c for c in conflicts if c.conflict_type == "PRICE_VS_OSCILLATOR")
    assert conflict.severity == SEVERITY_HIGH


def test_a_strong_stock_in_a_hostile_regime_is_flagged(config):
    bundle = bullish_bundle(regime="DISTRIBUTION", regime_blocks_buying=True)
    conflict = next(c for c in detect_conflicts(bundle, config) if c.conflict_type == "STOCK_VS_MARKET")
    assert conflict.severity == SEVERITY_HIGH
    assert "DISTRIBUTION" in conflict.bearish_evidence[0]


def test_distribution_like_behaviour_under_a_bullish_structure_is_flagged(config):
    behaviour = BehaviourRead(
        accumulation_like_score=20.0, distribution_like_score=70.0, verdict=DISTRIBUTION_LIKE
    )
    conflicts = detect_conflicts(bullish_bundle(behaviour=behaviour), config)
    assert "TREND_VS_BEHAVIOUR" in {c.conflict_type for c in conflicts}


def test_buying_that_does_not_move_price_is_flagged(config):
    """§9: net foreign buying is not automatically bullish."""
    bundle = bullish_bundle(foreign_net_5d=500e9)
    bundle.features["roc_20d"] = -3.0
    conflict = next(c for c in detect_conflicts(bundle, config) if c.conflict_type == "FLOW_VS_PRICE")
    assert "supply is meeting it" in conflict.explanation


def test_agreeing_evidence_produces_no_conflict(config):
    quiet = bullish_bundle(zones=[
        Zone(zone_low=79, zone_high=81, center=80, source="SWING_LOW",
             distance_from_price=-20.0, side=SIDE_SUPPORT)
    ])
    assert detect_conflicts(quiet, config) == []


def test_conflicts_are_never_averaged_into_a_single_number(config):
    """The output is a list of disagreements, not a net score."""
    bundle = bullish_bundle(
        zones=[resistance_at(1.5)],
        bull_trap=TrapRead(kind="BULL", score=80.0),
        behaviour=BehaviourRead(accumulation_like_score=10.0, distribution_like_score=65.0,
                                verdict=DISTRIBUTION_LIKE),
    )
    conflicts = detect_conflicts(bundle, config)
    assert len(conflicts) >= 2
    for conflict in conflicts:
        assert conflict.bullish_evidence
        assert conflict.bearish_evidence


def test_conflicts_are_ordered_most_severe_first(config):
    bundle = bullish_bundle(
        zones=[resistance_at(1.0)],
        bull_trap=TrapRead(kind="BULL", score=80.0),
        regime="BEAR", regime_blocks_buying=True,
    )
    bundle.features["vol_ratio_20"] = 0.5
    conflicts = detect_conflicts(bundle, config)
    from src.quant.conflicts import _SEVERITY_ORDER

    ranks = [_SEVERITY_ORDER[c.severity] for c in conflicts]
    assert ranks == sorted(ranks, reverse=True)


def test_an_empty_bundle_produces_nothing(config):
    assert detect_conflicts(EvidenceBundle(), config) == []


def _severity_of(conflicts, kind):
    for conflict in conflicts:
        if conflict.conflict_type == kind:
            return conflict.severity
    return None


# -- what changed ------------------------------------------------------------

def test_a_rising_feature_reads_as_up():
    change = compare_values("rsi14", 64.0, 81.0)
    assert change.direction == UP
    assert change.absolute_change == pytest.approx(17.0)
    assert change.percent_change == pytest.approx(26.5625)


def test_a_falling_feature_reads_as_down():
    assert compare_values("rsi14", 81.0, 64.0).direction == DOWN


def test_an_unchanged_feature_reads_as_flat():
    assert compare_values("rsi14", 64.0, 64.0).direction == SAME


def test_a_feature_that_did_not_exist_before_appeared():
    # Not "rose from zero": it was not computable on the earlier date.
    change = compare_values("ma200", None, 32_000.0)
    assert change.direction == APPEARED
    assert change.percent_change is None


def test_a_feature_that_stopped_being_computable_disappeared():
    assert compare_values("ma200", 32_000.0, None).direction == DISAPPEARED


def test_comparing_sets_covers_the_requested_names():
    changes = compare_feature_sets({"a": 1.0, "b": 2.0}, {"a": 2.0, "b": 2.0}, names=["a", "b"])
    assert [c.name for c in changes] == ["a", "b"]
    assert changes[0].direction == UP and changes[1].direction == SAME


def test_no_earlier_analysis_produces_no_invented_changes(db):
    upsert_features("SSI", date(2026, 8, 27), {"rsi14": 63.0}, path=db)
    report = what_changed("SSI", date(2026, 8, 27), path=db)
    assert report.available is False
    assert report.changes == []
    assert "no earlier analysis" in report.note


def test_changes_are_read_from_two_stored_sessions(db):
    upsert_features("SSI", date(2026, 8, 26), {"rsi14": 55.0, "vol_ratio_20": 1.1}, path=db)
    upsert_features("SSI", date(2026, 8, 27), {"rsi14": 63.0, "vol_ratio_20": 2.2}, path=db)

    report = what_changed("SSI", date(2026, 8, 27), names=["rsi14", "vol_ratio_20"], path=db)
    assert report.available is True
    assert report.previous_date == date(2026, 8, 26)
    directions = {c.name: c.direction for c in report.changes}
    assert directions == {"rsi14": UP, "vol_ratio_20": UP}


def test_the_previous_session_is_the_most_recent_one_before(db):
    for day, value in ((date(2026, 8, 20), 40.0), (date(2026, 8, 25), 50.0), (date(2026, 8, 27), 60.0)):
        upsert_features("SSI", day, {"rsi14": value}, path=db)
    assert previous_feature_date("SSI", date(2026, 8, 27), path=db) == date(2026, 8, 25)


def test_a_session_with_no_stored_features_says_so(db):
    report = what_changed("SSI", date(2026, 8, 27), path=db)
    assert report.available is False
    assert "no stored features" in report.note


def test_only_the_features_that_moved_are_surfaced(db):
    upsert_features("SSI", date(2026, 8, 26), {"rsi14": 60.0, "close": 100.0}, path=db)
    upsert_features("SSI", date(2026, 8, 27), {"rsi14": 60.0, "close": 110.0}, path=db)
    report = what_changed("SSI", date(2026, 8, 27), names=["rsi14", "close"], path=db)
    assert [c.name for c in report.moved()] == ["close"]


def test_a_change_describes_itself_in_one_line():
    assert "->" in describe(compare_values("rsi14", 64.0, 81.0))
    assert "new" in describe(compare_values("ma200", None, 5.0))
