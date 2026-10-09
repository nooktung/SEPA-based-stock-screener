import pytest
from src.config import ConfigError, load_config, load_thresholds, load_watchlist, load_sources


def test_watchlist_loads_expected_tickers():
    wl = load_watchlist()
    assert set(wl.active_tickers) == {"SSI", "VIX", "VND", "VCI", "HCM"}
    assert "VNINDEX" in wl.benchmarks


def test_all_watchlist_tickers_use_securities_profile():
    wl = load_watchlist()
    for t in wl.active_tickers:
        assert wl.profile_of(t) == "securities"


def test_peer_lookup_excludes_self():
    wl = load_watchlist()
    peers = wl.peers_of("SSI")
    assert "SSI" not in peers
    assert "VCI" in peers


def test_scoring_weights_sum_to_100():
    th = load_thresholds()
    assert sum(th.scoring_weights.values()) == 100


def test_recommendation_bands_cover_full_range_without_gaps():
    th = load_thresholds()
    for score in range(0, 101):
        assert th.label_for_score(score)


def test_band_labels_at_boundaries():
    th = load_thresholds()
    assert th.label_for_score(90) == "STRONG_BUY"
    assert th.label_for_score(89) == "BUY"
    assert th.label_for_score(0) == "SELL_AVOID"


def test_source_ids_are_globally_unique():
    reg = load_sources()
    ids = [s.id for s in reg.specs]
    assert len(ids) == len(set(ids)), f"duplicate source ids: {ids}"


def test_every_kind_has_at_least_one_enabled_source():
    reg = load_sources()
    for kind in ("market_data", "news", "financial", "macro", "flow"):
        assert reg.for_kind(kind), f"no enabled source for {kind}"


def test_sources_sorted_by_priority():
    reg = load_sources()
    news = reg.for_kind("news")
    assert [s.priority for s in news] == sorted(s.priority for s in news)
