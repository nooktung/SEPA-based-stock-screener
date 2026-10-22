"""Financial refresh caching must save requests without freezing missing/new quarters."""
from datetime import date, datetime, timedelta, timezone

import pytest

from src import main, pipeline
from src.collectors.base import ProviderError
from src.collectors.providers import cafef_financial_provider as cafef
from src.database import connect, init_db, sync_reference_data
from src.quant import fundamentals as fund
from src.quant.screener import screen_settings

VN = timezone(timedelta(hours=7))
NOW = datetime(2026, 10, 21, 19, tzinfo=VN)
WANTED = date(2026, 9, 30)


@pytest.mark.parametrize("latest,checked,accepted,reason,expected", [
    ("2026-09-30", "2026-10-01T00:00:00Z", 8, None, "quarter"),
    ("2026-06-30", "2026-10-21T02:00:00Z", 8, None, "today"),
    ("2026-06-30", "2026-10-20T18:00:00Z", 8, None, "today"),  # 01:00 in Vietnam
    ("2026-06-30", "2026-10-20T02:00:00Z", 8, None, None),
    (None, "2026-10-21T02:00:00Z", 0, "bad template", None),  # incomplete fallback
    (None, "2026-10-21T02:00:00Z", 0, "bad template; VNDirect: no model", "today"),
    (None, "2026-10-20T02:00:00Z", 0, "bad template; VNDirect: no model", None),
    ("2026-06-30", "broken", 8, None, None),
    ("2026-06-30", "2026-10-22T02:00:00Z", 8, None, None),
])
def test_skip_policy(latest, checked, accepted, reason, expected):
    log = dict(latest_quarter=latest, checked_at=checked, accepted_quarters=accepted,
               rejected_reason=reason, source_id=cafef.SOURCE_ID)
    assert fund.fetch_skip_reason(log, WANTED, NOW) == expected
    assert fund.fetch_skip_reason(log, WANTED, NOW, refresh=True) is None


def test_new_quarter_reopens_fetching_and_new_ticker_is_not_cached():
    log = dict(latest_quarter="2026-09-30", checked_at="2026-12-31T12:00:00Z", accepted_quarters=8)
    assert fund.fetch_skip_reason(log, date(2026, 12, 31), datetime(2027, 1, 1, 8, tzinfo=VN)) is None
    assert fund.fetch_skip_reason(None, WANTED, NOW) is None


def old_history():
    return {cafef.quarter_end(index % 4 + 1, index // 4):
            {"revenue_net": 1000.0 + n * 100, "net_income_parent": 100.0 + n * 15}
            for n, index in enumerate(range(2024 * 4 + 2, 2026 * 4 + 2))}


def test_incremental_fetch_requires_complete_prior_year_history():
    history = old_history()
    assert fund.history_pages(history, WANTED) == 1
    assert fund.history_pages(history, WANTED, refresh=True) == 2
    assert fund.history_pages({}, WANTED) == 2
    history.pop(date(2025, 3, 31))
    assert fund.history_pages(history, WANTED) == 2  # keep all three YoY readings assessable


@pytest.fixture
def cli(tmp_path, monkeypatch):
    db = tmp_path / "fund.db"
    init_db(db)
    sync_reference_data(db)
    data = cafef.QuarterlyIncome("HPG", old_history(), {}, ["https://example.test/history"])
    fund.store_income(data, db)
    fund.log_fetch(data, db)
    with connect(db) as c:
        c.execute("UPDATE financial_fetch_log SET checked_at='2026-10-20T02:00:00Z' WHERE ticker='HPG'")
    clock = [NOW]
    monkeypatch.setattr(pipeline, "vn_now", lambda: clock[0])
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(main, "sync_reference_data", lambda: None)
    monkeypatch.setattr("src.quant.screener.liquid_tickers", lambda: ["HPG"])
    monkeypatch.setattr(fund, "connect", lambda path=None: connect(db))
    original_log = fund.log_fetch

    def log(data, **kwargs):
        original_log(data, path=db, **kwargs)
        with connect(db) as c:
            c.execute("UPDATE financial_fetch_log SET checked_at=? WHERE ticker=?", (clock[0].isoformat(), data.ticker))

    monkeypatch.setattr(fund, "log_fetch", log)
    return db, clock


def test_cli_reuses_history_checks_once_per_day_and_picks_up_new_quarter(cli, monkeypatch):
    db, clock = cli
    calls = []
    before = fund.grade_ticker("HPG", screen_settings()).as_dict()

    def fetch(ticker, as_of, pages, fetch):
        calls.append(pages)
        recent = dict(sorted(old_history().items())[-4:])
        if clock[0].date() > NOW.date():
            recent[WANTED] = {"revenue_net": 1900.0, "net_income_parent": 250.0}
        return cafef.QuarterlyIncome(ticker, recent, {}, ["https://example.test/latest"])

    monkeypatch.setattr(cafef, "fetch_quarterly_income", fetch)
    assert main.fundamentals([]) == 0
    assert main.fundamentals([]) == 0
    assert calls == [1]
    assert fund.grade_ticker("HPG", screen_settings()).as_dict() == before
    assert len(fund.load_quarters("HPG")) == 8
    clock[0] += timedelta(days=1)
    assert main.fundamentals([]) == 0
    assert calls == [1, 1]
    assert fund.grade_ticker("HPG", screen_settings()).latest_quarter == WANTED
    assert len(fund.load_quarters("HPG")) == 9
    clock[0] += timedelta(days=1)
    assert main.fundamentals([]) == 0
    assert calls == [1, 1]  # complete quarter stays cached on later days
    assert main.fundamentals(["--refresh"]) == 0
    assert calls == [1, 1, 2]


def test_network_failure_does_not_cache_the_day(cli, monkeypatch):
    calls = []

    def fail(*args, **kwargs):
        calls.append(1)
        raise ProviderError("temporary timeout")

    monkeypatch.setattr(cafef, "fetch_quarterly_income", fail)
    main.fundamentals([])
    main.fundamentals([])
    assert len(calls) == 2


def test_empty_incremental_page_falls_back_to_full_history(cli, monkeypatch):
    calls = []

    def fetch(ticker, as_of, pages, fetch):
        calls.append(pages)
        quarters = {} if pages == 1 else old_history()
        return cafef.QuarterlyIncome(ticker, quarters, {}, ["https://example.test/history"])

    monkeypatch.setattr(cafef, "fetch_quarterly_income", fetch)
    assert main.fundamentals([]) == 0
    assert calls == [1, 2]
    assert len(fund.load_quarters("HPG")) == 8
