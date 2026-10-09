from datetime import date, datetime, timedelta

import pytest

from src.collectors.base import (
    AssertionType, ProviderDataMissing, Provenance, SourceLevel,
)
from src.collectors.providers.fixture_provider import (
    FixtureFinancialProvider, FixtureFlowProvider, FixtureMacroProvider,
    FixtureMarketProvider, FixtureNewsProvider,
)
from src.collectors.registry import fetch_with_fallback, instantiate
from src.config import load_config


def _market():
    return FixtureMarketProvider("fixture_market", 2, {"capabilities": ["ohlcv_daily"]})


def test_provenance_requires_source_id():
    with pytest.raises(ValueError):
        Provenance(source_id="", source_level=SourceLevel.DATA_VENDOR, observed_at=datetime.now())


def test_every_bar_carries_provenance():
    bars = _market().get_daily_ohlcv("SSI", date(2026, 8, 1), date(2026, 8, 26))
    assert bars
    for b in bars:
        assert b.provenance.source_id == "fixture_market"
        assert b.provenance.observed_at is not None


def test_daily_bars_skip_weekends_and_are_ascending():
    bars = _market().get_daily_ohlcv("SSI", date(2026, 8, 1), date(2026, 8, 26))
    dates = [b.trade_date for b in bars]
    assert all(d.weekday() < 5 for d in dates)
    assert dates == sorted(dates)


def test_ohlc_internally_consistent():
    for b in _market().get_daily_ohlcv("VIX", date(2026, 8, 1), date(2026, 8, 26)):
        assert b.low <= b.open <= b.high
        assert b.low <= b.close <= b.high


def test_fixture_is_deterministic():
    a = _market().get_daily_ohlcv("SSI", date(2026, 8, 1), date(2026, 8, 10))
    b = _market().get_daily_ohlcv("SSI", date(2026, 8, 1), date(2026, 8, 10))
    assert [x.close for x in a] == [x.close for x in b]


def test_capability_gate_rejects_unsupported():
    p = _market()
    assert p.supports("ohlcv_daily")
    assert not p.supports("intraday")


def test_intraday_raises_rather_than_inventing():
    with pytest.raises(ProviderDataMissing):
        _market().get_intraday_ohlcv("SSI", 15)


def test_social_level_forces_rumour_only():
    p = FixtureNewsProvider("some_forum", 5, {})
    assert p.rumour_only is True
    assert p.event_authority is False


def test_news_provider_returns_near_duplicates_for_dedup_testing():
    arts = FixtureNewsProvider("fixture_news", 3, {}).fetch_articles(
        ["SSI"], datetime.now() - timedelta(hours=24)
    )
    assert len(arts) == 2
    assert arts[0].title == arts[1].title


def test_securities_profile_yields_sector_metrics():
    p = FixtureFinancialProvider("fixture_financial", 2, {})
    metrics = p.get_sector_specific_metrics("SSI", "securities", periods=4)
    names = {m.metric_name for m in metrics}
    assert "margin_balance" in names
    assert "prop_trading_pnl" in names
    assert all(m.assertion_type == AssertionType.FACT for m in metrics)


def test_generic_profile_gets_no_securities_metrics():
    p = FixtureFinancialProvider("fixture_financial", 2, {})
    assert p.get_sector_specific_metrics("ABC", "generic") == []


def test_breadth_components_sum_consistently():
    b = FixtureFlowProvider("fixture_flow", 2, {}).get_market_breadth(date(2026, 8, 26))
    assert b["advancers"] + b["decliners"] + b["unchanged"] == 450


def test_registry_instantiates_every_enabled_provider():
    cfg = load_config()
    for spec in cfg.sources.specs:
        if spec.enabled:
            provider = instantiate(spec)
            assert provider.source_id == spec.id


def test_fetch_with_fallback_returns_result_and_flags_fixture():
    res = fetch_with_fallback(
        "market_data",
        lambda p: p.get_daily_ohlcv("SSI", date(2026, 8, 1), date(2026, 8, 5)),
    )
    assert res.ok
    assert res.from_fixture_only is True


def test_fetch_with_fallback_marks_stale_when_all_fail():
    def boom(p):
        from src.collectors.base import ProviderUnavailable
        raise ProviderUnavailable("simulated outage")

    res = fetch_with_fallback("market_data", boom)
    assert res.stale is True
    assert res.value is None
    assert res.errors
