from datetime import date, datetime, timedelta, timezone

import pytest

from src.collectors.backfill import BackfillSettings, run_backfill
from src.collectors.base import OHLCV, Provenance, ProviderDataMissing, SourceLevel
from src.database import connect, init_db, sync_reference_data

SETTINGS = BackfillSettings(history_days=60, overlap_days=10, readjust_tolerance_pct=0.5)
END = date(2026, 9, 25)


def _prov():
    return Provenance("dnse_openapi", SourceLevel.DATA_VENDOR, datetime.now(timezone.utc))


def _sessions(start: date, end: date):
    d = start
    while d <= end:
        if d.weekday() < 5:
            yield d
        d += timedelta(days=1)


class FakeProvider:
    """Serves a flat 10,000 VND series; `scale` rescales history like an ex-date does."""

    def __init__(self, scale=1.0, jump_on=None, missing=()):
        self.scale = scale
        self.jump_on = jump_on
        self.missing = set(missing)
        self.requests = []

    def _bars(self, symbol, start, end, base):
        out = []
        for d in _sessions(start, end):
            price = base * self.scale
            if self.jump_on and d >= self.jump_on:
                price *= 0.8                     # -20% overnight: impossible on HOSE
            out.append(OHLCV(symbol, d, price, price, price, price, 1000, None, _prov()))
        return out

    def get_daily_ohlcv(self, symbol, start, end):
        self.requests.append((symbol, start, end))
        if symbol in self.missing:
            raise ProviderDataMissing(f"no bars for {symbol}")
        return self._bars(symbol, start, end, 10_000.0)

    def get_index_ohlcv(self, symbol, start, end):
        self.requests.append((symbol, start, end))
        return self._bars(symbol, start, end, 1_800.0)

    def get_session_totals(self, symbol, day=None):
        return None


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "bf.db"
    init_db(path)
    sync_reference_data(path)
    with connect(path) as conn:
        conn.execute("INSERT OR IGNORE INTO sources (source_id, kind, source_level, enabled) "
                     "VALUES ('dnse_openapi', 'market_data', 2, 1)")
        for t, ex in (("AAA", "HOSE"), ("BBB", "HNX")):
            conn.execute("INSERT OR IGNORE INTO tickers (ticker, exchange, created_at, updated_at) "
                         "VALUES (?, ?, 'x', 'x')", (t, ex))
    return path


def _run(db, provider, tickers=(("AAA", "HOSE"),), end=END):
    return run_backfill(tickers=list(tickers), benchmarks=["VNINDEX"], end=end,
                        provider=provider, settings=SETTINGS, path=db)


def _count(db, ticker):
    with connect(db) as conn:
        return conn.execute("SELECT COUNT(*) FROM prices_daily WHERE ticker=?", (ticker,)).fetchone()[0]


def test_first_run_fetches_the_configured_history(db):
    p = FakeProvider()
    report = _run(db, p)
    assert all(o.persisted for o in report.outcomes)
    aaa = [r for r in p.requests if r[0] == "AAA"][0]
    assert aaa[1] == END - timedelta(days=SETTINGS.history_days)
    assert _count(db, "AAA") == len(list(_sessions(aaa[1], END)))


def test_second_run_resumes_with_a_small_overlap(db):
    _run(db, FakeProvider())
    p = FakeProvider()
    _run(db, p, end=END + timedelta(days=3))
    aaa = [r for r in p.requests if r[0] == "AAA"][0]
    assert aaa[1] == END - timedelta(days=SETTINGS.overlap_days)


def test_rewritten_history_triggers_a_full_refetch_and_replace(db):
    _run(db, FakeProvider())
    p = FakeProvider(scale=0.8)                 # vendor re-adjusted everything after an ex-date
    report = _run(db, p, end=END + timedelta(days=3))
    aaa_requests = [r for r in p.requests if r[0] == "AAA"]
    assert len(aaa_requests) == 2               # overlap probe, then full history
    assert "re-adjusted" in [o for o in report.outcomes if o.scope == "AAA"][0].note
    with connect(db) as conn:
        closes = {r[0] for r in conn.execute("SELECT DISTINCT close FROM prices_daily WHERE ticker='AAA'")}
    assert closes == {8_000.0}                  # one consistent scale, not a splice of two


def test_a_rescaled_history_puts_old_references_on_the_new_scale(db):
    _run(db, FakeProvider())
    with connect(db) as conn:
        conn.executemany("INSERT INTO reference_prices (ticker, session_date, reference, ceiling, floor, published_at, "
                         "source_id, observed_at) VALUES ('AAA', ?, 10000, 10700, 9300, 'x', 'dnse_openapi', 'x')",
                         [(END.isoformat(),), ((END + timedelta(days=10)).isoformat(),)])
    _run(db, FakeProvider(scale=0.8), end=END + timedelta(days=3))      # vendor re-adjusted after an ex-date
    from src.collectors.refprices import lookup

    with connect(db) as conn:
        rows = [tuple(r) for r in conn.execute("SELECT through_session, factor FROM price_adjustments WHERE ticker='AAA'")]
        assert rows == [(END.isoformat(), pytest.approx(0.8))]           # the last session stored on the old scale
        assert conn.execute("SELECT reference FROM reference_prices WHERE ticker='AAA' AND session_date=?",
                            (END.isoformat(),)).fetchone()[0] == 10_000.0  # stored as the exchange published it
    got = lookup([("AAA", END.isoformat()), ("AAA", (END + timedelta(days=10)).isoformat())], db)
    assert got[("AAA", END.isoformat())]["reference"] == pytest.approx(8_000.0)            # read on the bars' scale
    assert got[("AAA", (END + timedelta(days=10)).isoformat())]["reference"] == 10_000.0   # after the ex-date: as published


def test_gap_beyond_the_band_keeps_bars_but_marks_stale(db):
    report = _run(db, FakeProvider(jump_on=date(2026, 9, 1)))
    aaa = [o for o in report.outcomes if o.scope == "AAA"][0]
    assert aaa.persisted and aaa.stale and aaa.gaps
    with connect(db) as conn:
        stale = conn.execute("SELECT is_stale FROM data_freshness WHERE dataset='prices_daily' "
                             "AND scope_key='AAA'").fetchone()[0]
    assert stale == 1


def test_old_gaps_outside_the_screening_window_are_ignored(db):
    # e.g. a stock that moved from HNX's 10% band to HOSE's 7% years ago.
    narrow = BackfillSettings(history_days=60, overlap_days=10, readjust_tolerance_pct=0.5,
                              gap_check_sessions=10)
    report = run_backfill(tickers=[("AAA", "HOSE")], benchmarks=[], end=END,
                          provider=FakeProvider(jump_on=date(2026, 8, 3)), settings=narrow, path=db)
    assert not report.outcomes[0].gaps and not report.outcomes[0].stale


def test_upcom_bounds_allow_for_a_vwap_reference():
    from src.collectors.backfill import close_to_close_bounds
    down, up = close_to_close_bounds("UPCOM")
    assert down == pytest.approx(37.17, abs=0.01) and up == pytest.approx(55.59, abs=0.01)
    assert close_to_close_bounds("HOSE") == (7.5, 7.5)


def test_upcom_move_legal_against_vwap_is_not_flagged_but_a_split_is(db):
    from src.collectors.backfill import recent_gaps
    ok = run_backfill(tickers=[("BBB", "UPCOM")], benchmarks=[], end=END,
                      provider=FakeProvider(jump_on=None), settings=SETTINGS, path=db)
    assert not ok.outcomes[0].stale
    with connect(db) as conn:
        conn.execute("UPDATE prices_daily SET close = close * 1.2 WHERE ticker='BBB' AND trade_date >= '2026-09-20'")
    assert recent_gaps("BBB", "UPCOM", 300, db) == []          # +20%: possible on UPCoM
    with connect(db) as conn:
        conn.execute("UPDATE prices_daily SET close = close * 0.4 WHERE ticker='BBB' AND trade_date >= '2026-09-23'")
    assert recent_gaps("BBB", "UPCOM", 300, db)                # -60%: not one session


def test_recheck_gaps_updates_flags_without_the_network(db):
    from src.collectors.backfill import recheck_gaps
    run_backfill(tickers=[("AAA", "HOSE")], benchmarks=[], end=END,
                 provider=FakeProvider(jump_on=date(2026, 9, 1)), settings=SETTINGS, path=db)
    with connect(db) as conn:
        conn.execute("UPDATE prices_daily SET close = 8000 WHERE ticker='AAA'")   # repaired series
    stats = recheck_gaps(SETTINGS, path=db)
    assert stats["clean"] >= 1
    with connect(db) as conn:
        assert conn.execute("SELECT is_stale FROM data_freshness WHERE scope_key='AAA'").fetchone()[0] == 0


def test_one_failing_ticker_does_not_stop_the_run(db):
    report = _run(db, FakeProvider(missing={"AAA"}), tickers=(("AAA", "HOSE"), ("BBB", "HNX")))
    by = {o.scope: o for o in report.outcomes}
    assert by["AAA"].stale and not by["AAA"].persisted
    assert by["BBB"].persisted and _count(db, "BBB") > 0


def test_benchmarks_go_to_index_daily(db):
    _run(db, FakeProvider())
    with connect(db) as conn:
        n = conn.execute("SELECT COUNT(*) FROM index_daily WHERE symbol='VNINDEX'").fetchone()[0]
    assert n > 0


def test_run_is_recorded(db):
    report = _run(db, FakeProvider())
    with connect(db) as conn:
        row = conn.execute("SELECT job_type FROM collection_runs WHERE run_id=?", (report.run_id,)).fetchone()
    assert row[0] == "BACKFILL"


# -- deepening a series stored with a shorter window (2026-10-06) ------------------------------

DEEP = BackfillSettings(history_days=200, overlap_days=10, readjust_tolerance_pct=0.5)

def test_deepen_reads_the_full_window_once_and_replaces_the_series(db, tmp_path):
    from src.collectors.backfill import deepen_history

    short = BackfillSettings(history_days=30, overlap_days=10, readjust_tolerance_pct=0.5)
    run_backfill(tickers=[("AAA", "HOSE")], benchmarks=[], end=END, provider=FakeProvider(), settings=short, path=db)
    shallow = _count(db, "AAA")
    marker = tmp_path / "depth.json"
    p = FakeProvider(scale=0.9)                 # the vendor re-adjusted since: the whole series takes the new scale
    report = deepen_history([("AAA", "HOSE")], provider=p, settings=DEEP, end=END, marker=marker, path=db)
    assert report.outcomes[0].persisted and "thêm lịch sử" in report.outcomes[0].note
    assert p.requests == [("AAA", END - timedelta(days=DEEP.history_days), END)]
    assert _count(db, "AAA") > shallow
    with connect(db) as conn:
        assert {r[0] for r in conn.execute("SELECT DISTINCT close FROM prices_daily WHERE ticker='AAA'")} == {9_000.0}
    again = FakeProvider()
    deepen_history([("AAA", "HOSE")], provider=again, settings=DEEP, end=END, marker=marker, path=db)
    assert again.requests == []                 # checked once against this window: not read again


def test_deepen_keeps_the_stored_series_when_the_read_is_short(db, tmp_path):
    from src.collectors.backfill import deepen_history

    short = BackfillSettings(history_days=30, overlap_days=10, readjust_tolerance_pct=0.5)
    run_backfill(tickers=[("AAA", "HOSE")], benchmarks=[], end=END, provider=FakeProvider(), settings=short, path=db)
    before = _count(db, "AAA")
    # The vendor's read stops two weeks early: it must not replace what is stored.
    report = deepen_history([("AAA", "HOSE")], provider=FakeProvider(), settings=DEEP, end=END - timedelta(days=14),
                            marker=tmp_path / "depth.json", path=db)
    assert report.outcomes[0].stale and "không dùng" in report.outcomes[0].note
    assert _count(db, "AAA") == before
    # A ticker with nothing stored is left to the ordinary backfill.
    report = deepen_history([("BBB", "HNX")], provider=FakeProvider(), settings=DEEP, end=END,
                            marker=tmp_path / "depth.json", path=db)
    assert not report.outcomes[0].persisted and _count(db, "BBB") == 0


def test_deepen_skips_a_series_already_as_deep_as_the_window(db, tmp_path):
    from src.collectors.backfill import deepen_history

    run_backfill(tickers=[("AAA", "HOSE")], benchmarks=[], end=END, provider=FakeProvider(), settings=DEEP, path=db)
    p = FakeProvider()
    report = deepen_history([("AAA", "HOSE")], provider=p, settings=DEEP, end=END, marker=tmp_path / "d.json", path=db)
    assert p.requests == [] and report.outcomes[0].note == "đã đủ sâu"
