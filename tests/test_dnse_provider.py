"""DNSE market provider, using payload shapes observed on the live API (docs/DNSE.md)."""
from datetime import date

import pytest

from src.collectors.base import ProviderDataMissing, ProviderUnavailable
from src.collectors.providers.dnse_provider import (
    FORBIDDEN_METHODS,
    DnseMarketProvider,
    _GuardedClient,
    epoch_to_vn_date,
    normalize_price,
    vn_day_bounds,
)

CFG = {
    "capabilities": ["ohlcv_daily", "index_ohlcv"],
    "price_scale": 1000.0,
    "index_scale": 1.0,
    "price_series": "adjusted",
    "index_symbol_aliases": {"HNXINDEX": "HNX"},
}


class RecordingClient:
    """Stands in for DnseHttpClient: records every GET, answers from a table."""

    def __init__(self, responses=None):
        self.calls = []
        self.responses = responses or {}

    def get(self, path, query=None):
        self.calls.append((path, dict(query or {})))
        answer = self.responses.get(path, COLUMN_PAYLOAD)
        return answer(query) if callable(answer) else answer

    def health_check(self):
        return True


def provider(client=None, **overrides):
    return DnseMarketProvider("dnse_openapi", 2, {**CFG, **overrides}, client=client or RecordingClient())


# -- unit normalisation -----------------------------------------------------

def test_stock_price_scaled_to_vnd():
    # HPG matchPrice 24.35 in the docs means 24,350 VND.
    assert normalize_price(24.35, 1000.0) == 24350.0


def test_index_points_not_scaled():
    assert normalize_price(1669.38, 1.0) == 1669.38


def test_none_price_stays_none():
    assert normalize_price(None, 1000.0) is None


# -- dates at UTC+7 -----------------------------------------------------------

def test_epoch_is_dated_in_vietnam_not_utc():
    # 2026-09-25 00:30 at UTC+7 is still 2026-09-24 in UTC.
    assert epoch_to_vn_date(1790271000) == date(2026, 9, 25)


def test_day_bounds_cover_the_vietnamese_day():
    start, end = vn_day_bounds(date(2026, 9, 25))
    assert end - start == 86399
    assert epoch_to_vn_date(start) == date(2026, 9, 25)
    assert epoch_to_vn_date(end) == date(2026, 9, 25)
    assert epoch_to_vn_date(start - 1) == date(2026, 9, 24)


# -- OHLC parsing -----------------------------------------------------------

COLUMN_PAYLOAD = {
    "t": [1789956000, 1790042400],   # 2026-09-21, 2026-09-22 at 09:00 +07
    "o": [21.55, 20.95], "h": [21.60, 21.35],
    "l": [20.75, 20.90], "c": [20.90, 20.95],
    "v": [33_510_000, 12_109_000],
    "nextTime": 0,
}


def test_column_oriented_payload_parsed():
    bars = provider()._parse_bars(COLUMN_PAYLOAD, "SSI", 1000.0)
    assert len(bars) == 2
    assert bars[0].close == 20900.0
    assert bars[1].trade_date == date(2026, 9, 22)


def test_bars_returned_in_ascending_date_order():
    scrambled = {**COLUMN_PAYLOAD, "t": list(reversed(COLUMN_PAYLOAD["t"]))}
    bars = provider()._parse_bars(scrambled, "SSI", 1000.0)
    assert bars[0].trade_date < bars[1].trade_date


def test_row_oriented_payload_also_accepted():
    payload = {"data": [{"time": 1789956000, "open": 21.3, "high": 21.6,
                         "low": 21.2, "close": 21.4, "volume": 100}]}
    assert provider()._parse_bars(payload, "SSI", 1000.0)[0].close == 21400.0


def test_every_bar_carries_provenance():
    for b in provider()._parse_bars(COLUMN_PAYLOAD, "SSI", 1000.0):
        assert b.provenance.source_id == "dnse_openapi"


def test_turnover_is_never_reconstructed():
    assert all(b.value_vnd is None for b in provider()._parse_bars(COLUMN_PAYLOAD, "SSI", 1000.0))


def test_adj_close_only_when_series_measured_adjusted():
    adjusted = provider()._parse_bars(COLUMN_PAYLOAD, "SSI", 1000.0)
    unknown = provider(price_series="unknown")._parse_bars(COLUMN_PAYLOAD, "SSI", 1000.0)
    assert adjusted[0].adj_close == adjusted[0].close
    assert unknown[0].adj_close is None


def test_empty_payload_raises_rather_than_returning_nothing():
    with pytest.raises(ProviderDataMissing):
        provider()._parse_bars({}, "SSI", 1000.0)


def test_unknown_shape_raises():
    with pytest.raises(ProviderDataMissing):
        provider()._parse_bars({"weird": 1}, "SSI", 1000.0)


def test_rows_without_timestamp_are_skipped_not_guessed():
    payload = {"data": [{"open": 1, "close": 2}, {"time": 1789956000, "close": 21.4}]}
    assert len(provider()._parse_bars(payload, "SSI", 1000.0)) == 1


# -- requests ---------------------------------------------------------------

def test_stock_request_sends_type_stock():
    # Without `type` DNSE answers 400 INVALID_BAR_TYPE (observed 2026-09-26).
    client = RecordingClient()
    provider(client).get_daily_ohlcv("ssi", date(2026, 9, 21), date(2026, 9, 22))
    path, query = client.calls[0]
    assert path == "/price/ohlc"
    assert query["type"] == "STOCK"
    assert query["symbol"] == "SSI"
    assert query["resolution"] == "1D"


def test_index_request_uses_dnse_alias_but_labels_with_project_name():
    client = RecordingClient()
    bars = provider(client).get_index_ohlcv("HNXINDEX", date(2026, 9, 21), date(2026, 9, 22))
    assert client.calls[0][1]["type"] == "INDEX"
    assert client.calls[0][1]["symbol"] == "HNX"
    assert bars[0].symbol == "HNXINDEX"
    assert bars[0].close == 20.90          # points, not scaled


def test_long_ranges_are_chunked_and_merged():
    client = RecordingClient()
    provider(client, chunk_days=365).get_daily_ohlcv("SSI", date(2022, 1, 1), date(2026, 9, 25))
    assert len(client.calls) == 5
    froms = [q["from"] for _, q in client.calls]
    assert froms == sorted(froms)


def test_duplicate_bars_across_chunks_are_merged_by_date():
    client = RecordingClient()
    bars = provider(client, chunk_days=10).get_daily_ohlcv("SSI", date(2026, 9, 1), date(2026, 9, 25))
    assert len(bars) == 2                  # every chunk returned the same two bars


def test_truncated_window_is_split_not_stored_short():
    def answer(query):
        # Pretend the vendor truncates any window longer than 100 days.
        span_days = (query["to"] - query["from"]) / 86400
        return {**COLUMN_PAYLOAD, "nextTime": 1 if span_days > 100 else 0}

    client = RecordingClient({"/price/ohlc": answer})
    provider(client, chunk_days=4000).get_daily_ohlcv("SSI", date(2026, 1, 1), date(2026, 9, 25))
    spans = [(q["to"] - q["from"]) / 86400 for _, q in client.calls]
    assert spans[0] > 100                       # the first attempt was truncated...
    assert all(s <= 100 for s in spans[-2:])    # ...and split until windows fit


def test_next_time_on_an_empty_window_means_no_data_not_truncation():
    # Observed for X77: five years of t=[] with nextTime pointing at 2021.
    client = RecordingClient({"/price/ohlc": {"t": [], "nextTime": 1632243600}})
    with pytest.raises(ProviderDataMissing, match="no bars"):
        provider(client, chunk_days=4000).get_daily_ohlcv("X77", date(2021, 9, 26), date(2026, 9, 25))
    assert len(client.calls) == 1                # no pointless splitting


def test_empty_window_is_tolerated_but_empty_range_is_not():
    empty = RecordingClient({"/price/ohlc": {"t": [], "nextTime": 0}})
    with pytest.raises(ProviderDataMissing):
        provider(empty).get_daily_ohlcv("SSI", date(2026, 9, 1), date(2026, 9, 25))


# -- reference prices and tape ------------------------------------------------

SECDEF = [{"symbol": "SSI", "basicPrice": 20.7, "ceilingPrice": 22.1, "floorPrice": 19.3,
           "time": "2026-10-05 20:46:29.147"}]


def test_reference_prices_scaled_to_vnd():
    client = RecordingClient({"/price/SSI/secdef": SECDEF})
    ref = provider(client).get_reference_prices("SSI")
    assert ref == {"reference": 20700.0, "ceiling": 22100.0, "floor": 19300.0,
                   "published_at": "2026-10-05 20:46:29.147"}


def test_session_totals_read_from_newest_trade():
    trades = {"trades": [{"matchPrice": 20.7, "highestPrice": 21.05, "lowestPrice": 20.7,
                          "openPrice": 20.85, "totalVolumeTraded": 1168950}]}
    client = RecordingClient({"/price/SSI/trades": trades})
    totals = provider(client).get_session_totals("SSI", date(2026, 9, 25))
    assert (totals.open, totals.high, totals.low, totals.close) == (20850.0, 21050.0, 20700.0, 20700.0)
    query = client.calls[0][1]
    assert query["limit"] == 1 and query["order"] == "DESC"


def test_session_totals_none_when_no_trades():
    client = RecordingClient({"/price/SSI/trades": {"trades": []}})
    assert provider(client).get_session_totals("SSI", date(2026, 9, 26)) is None


# -- execution guard --------------------------------------------------------

class _FakeClient:
    def post_order(self, *a, **k): return "ORDER PLACED"
    def cancel_order(self, *a, **k): return "CANCELLED"
    def get(self, *a, **k): return COLUMN_PAYLOAD


@pytest.mark.parametrize("method", sorted(FORBIDDEN_METHODS))
def test_every_execution_method_is_blocked(method):
    guarded = _GuardedClient(_FakeClient())
    with pytest.raises(PermissionError):
        getattr(guarded, method)


def test_read_methods_pass_through_the_guard():
    assert _GuardedClient(_FakeClient()).get("/price/ohlc") == COLUMN_PAYLOAD


def test_injected_client_is_wrapped_by_the_guard():
    p = provider(_FakeClient())
    with pytest.raises(PermissionError):
        p.client.post_order()


def test_missing_credentials_raise_unavailable_not_silent_failure(monkeypatch):
    monkeypatch.delenv("DNSE_API_KEY", raising=False)
    monkeypatch.delenv("DNSE_API_SECRET", raising=False)
    p = DnseMarketProvider("dnse_openapi", 2, CFG)
    with pytest.raises(ProviderUnavailable):
        p.get_daily_ohlcv("SSI", date(2026, 8, 1), date(2026, 8, 26))


def test_health_check_never_raises(monkeypatch):
    monkeypatch.delenv("DNSE_API_KEY", raising=False)
    assert DnseMarketProvider("dnse_openapi", 2, CFG).health_check() is False
