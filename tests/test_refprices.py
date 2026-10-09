"""Reference prices: which session a DNSE definition belongs to, how it is stored and used."""

from datetime import date, datetime, timezone

import pytest

from src.collectors import refprices as rp
from src.database import connect, init_db
from src.quant.monitor import compute_state, monitor_settings
from src.quant.screener import screen_settings

CFG = {"next_session_after": "15:00"}


@pytest.mark.parametrize("published, session", [
    ("2026-10-05 20:46:29.147", date(2026, 10, 6)),     # Monday evening -> Tuesday (seen live 05/10/2026)
    ("2026-10-02 20:46:00", date(2026, 10, 5)),         # Friday evening -> Monday
    ("2026-10-03 09:00:00", date(2026, 10, 5)),         # Saturday -> Monday
    ("2026-10-06 08:30:00", date(2026, 10, 6)),         # before the open -> that day
    ("2026-10-06 10:15:00", date(2026, 10, 6)),         # an intraday update -> still that day
])
def test_a_definition_belongs_to_the_next_session_after_its_publication(published, session):
    assert rp.session_for(rp.parse_published(published), CFG) == session


def test_unreadable_timestamps_are_none():
    assert rp.parse_published(None) is None and rp.parse_published("hôm qua") is None
    assert rp.parse_published("2026-10-05T20:46:29") == datetime(2026, 10, 5, 20, 46, 29)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "ref.db"
    init_db(path)
    with connect(path) as conn:
        # Sessions: Thu 01/10, Fri 02/10, (Mon 05/10 a holiday in this test), Tue 06/10.
        conn.executemany("INSERT INTO index_daily (symbol, trade_date, close, source_id, observed_at) "
                         "VALUES ('VNINDEX', ?, 1700, 'dnse_openapi', 'x')",
                         [("2026-10-01",), ("2026-10-02",), ("2026-10-06",)])
    return path


def defs(**by_ticker):
    def fetch(t):
        v = by_ticker[t]
        if isinstance(v, Exception):
            raise v
        return v
    return fetch


def test_collect_stores_valid_definitions_and_names_what_it_skipped(db):
    fetch = defs(
        PHP={"reference": 47300.0, "ceiling": 54300.0, "floor": 40300.0, "published_at": "2026-10-01 20:46:00"},
        BAD={"reference": 10000.0, "ceiling": 9000.0, "floor": 8000.0, "published_at": "2026-10-01 20:46:00"},
        NOTIME={"reference": 10000.0, "ceiling": 10700.0, "floor": 9300.0, "published_at": None},
        DOWN=RuntimeError("timeout"),
    )
    report = rp.collect_reference_prices(["php", "BAD", "NOTIME", "DOWN"], fetch, CFG, "dnse_openapi", db,
                                         now=datetime(2026, 10, 2, 0, 30, tzinfo=timezone.utc))
    assert report.stored == {"PHP": date(2026, 10, 2)}
    assert set(report.failed) == {"BAD", "NOTIME", "DOWN"} and "trần / sàn" in report.failed["BAD"]
    with connect(db) as conn:
        row = conn.execute("SELECT * FROM reference_prices").fetchone()
    assert (row["ticker"], row["session_date"], row["reference"], row["source_id"]) == ("PHP", "2026-10-02", 47300.0, "dnse_openapi")
    # A republished definition for the same session replaces the stored one.
    fetch2 = defs(PHP={"reference": 47400.0, "ceiling": 54500.0, "floor": 40300.0, "published_at": "2026-10-01 21:10:00"})
    rp.collect_reference_prices(["PHP"], fetch2, CFG, "dnse_openapi", db)
    with connect(db) as conn:
        assert conn.execute("SELECT COUNT(*), MAX(reference) FROM reference_prices").fetchone()[:] == (1, 47400.0)


def store(db, ticker, session, ref):
    with connect(db) as conn:
        conn.execute("INSERT INTO reference_prices (ticker, session_date, reference, ceiling, floor, published_at, source_id, observed_at) VALUES (?, ?, ?, ?, ?, 'x', 'dnse_openapi', 'x')",
                     (ticker, session, ref, ref * 1.15, ref * 0.85))


def test_lookup_uses_the_sessions_own_value_and_bridges_a_holiday(db):
    store(db, "PHP", "2026-10-02", 47300.0)
    store(db, "PHP", "2026-10-05", 49100.0)      # published Friday evening for Monday -- a holiday here
    store(db, "OLD", "2026-09-20", 10000.0)      # a stale definition never serves a later session
    got = rp.lookup([("PHP", "2026-10-02"), ("PHP", "2026-10-06"), ("PHP", "2026-10-01"), ("OLD", "2026-10-02")], db)
    assert got[("PHP", "2026-10-02")]["reference"] == 47300.0
    assert got[("PHP", "2026-10-06")]["reference"] == 49100.0
    assert ("PHP", "2026-10-01") not in got and ("OLD", "2026-10-02") not in got


def test_change_follows_the_boards_and_names_its_basis():
    # PHP 05/10/2026: close 49,500, previous close 46,700, reference (previous average price) 47,300.
    ref = {"reference": 47300.0, "ceiling": 54300.0, "floor": 40300.0}
    assert rp.change(49500.0, 46700.0, ref)["pct"] == 4.65
    assert rp.change(49500.0, 46700.0, ref)["basis"] == "ref"
    assert rp.change(49500.0, 46700.0, None) == {"pct": 6.0, "basis": "close", "reference": None, "prev_close": 46700.0}


def test_the_published_ceiling_decides_at_the_ceiling():
    import numpy as np
    import pandas as pd

    closes = list(np.linspace(20_000, 40_000, 260)) + [42_000.0]
    idx = pd.bdate_range(end="2026-10-02", periods=len(closes))
    f = pd.DataFrame({"open": closes, "high": closes, "low": closes, "close": closes, "volume": 1e6}, index=idx)
    screen, mon = screen_settings(), monitor_settings()
    # +5% from the previous close is not at UPCoM's +15% band from that close ...
    assert not compute_state("AAA", "UPCOM", f, None, 90.0, screen, mon).at_ceiling
    # ... but it is when the exchange's published ceiling (from a lower average price) is 42,000.
    assert compute_state("AAA", "UPCOM", f, None, 90.0, screen, mon, ceiling=42_000.0).at_ceiling
    assert not compute_state("AAA", "UPCOM", f, None, 90.0, screen, mon, ceiling=42_100.0).at_ceiling


# -- UPCoM: the previous session's average (2026-10-06) ----------------------------------------

@pytest.mark.parametrize("average, ref, ceiling, floor", [
    (47_275.0, 47_300.0, 54_300.0, 40_300.0),    # PHP 02/10 -> the board's TC / Trần / Sàn for 05/10
    (49_145.0, 49_100.0, 56_400.0, 41_800.0),    # PHP 05/10 -> DNSE's published values for 06/10
    (9_795.0, 9_800.0, 11_200.0, 8_400.0),       # AAS 05/10 -> 9.8 published
    (51_986.0, 52_000.0, 59_800.0, 44_200.0),    # QNS 05/10 -> 52.0 published
])
def test_upcom_limits_follow_the_exchange_rule(average, ref, ceiling, floor):
    assert rp.upcom_limits(average, 15.0) == {"reference": ref, "ceiling": ceiling, "floor": floor}


@pytest.fixture
def upcom(db):
    with connect(db) as conn:
        conn.execute("INSERT INTO sources (source_id, kind, source_level, enabled) VALUES ('dnse_openapi','market_data',2,1)")
        for t, ex in (("PHP", "UPCOM"), ("ABB", "UPCOM"), ("VIC", "HOSE")):
            conn.execute("INSERT INTO tickers (ticker, exchange, created_at, updated_at) VALUES (?, ?, 'x', 'x')", (t, ex))
            conn.executemany("INSERT INTO prices_daily (ticker, trade_date, close, volume, source_id, observed_at) "
                             "VALUES (?, ?, 10, 1, 'dnse_openapi', 'x')", [(t, d) for d in ("2026-10-01", "2026-10-02", "2026-10-06")])
    return db


AVERAGES = {("PHP", "2026-10-01"): 46_120.0, ("PHP", "2026-10-02"): 47_275.0, ("PHP", "2026-10-06"): 49_145.0,
            ("ABB", "2026-10-02"): 17_684.0, ("ABB", "2026-10-06"): 17_652.0}


def fetch_avg(t, d):
    return AVERAGES.get((t, d.isoformat()))


def test_the_rule_is_checked_against_published_references(upcom):
    store(upcom, "PHP", "2026-10-07", 49_100.0)          # published for the next session: 49,145 -> 49,100
    store(upcom, "ABB", "2026-10-07", 18_000.0)          # an ex-date reset, say: the rule does not reproduce it
    check = rp.check_average_rule(["PHP", "ABB"], fetch_avg, 15.0, upcom)
    assert check.checked == {"PHP": (49_100.0, 49_100.0), "ABB": (17_700.0, 18_000.0)}
    assert check.mismatched == {"ABB"} and check.share == 0.5


def test_missing_upcom_sessions_are_filled_from_the_previous_average(upcom):
    store(upcom, "PHP", "2026-10-02", 46_100.0)          # published: never overwritten by the rule
    targets = rp.fill_targets(["PHP", "ABB", "VIC"], 5, path=upcom)
    assert targets == [("PHP", "2026-10-01"), ("PHP", "2026-10-06"), ("ABB", "2026-10-01"), ("ABB", "2026-10-02"),
                       ("ABB", "2026-10-06")]           # HOSE is never filled
    cfg = {"fill_max_gap_days": 14}
    report = rp.fill_from_averages(targets, fetch_avg, cfg, 15.0, "dnse_openapi", upcom)
    got = rp.lookup([("PHP", "2026-10-06"), ("PHP", "2026-10-02"), ("ABB", "2026-10-02")], upcom)
    assert got[("PHP", "2026-10-06")]["reference"] == 47_300.0 and got[("PHP", "2026-10-06")]["method"] == "avg_price"
    assert got[("PHP", "2026-10-06")]["averaged"] == "2026-10-02"
    assert got[("PHP", "2026-10-02")]["reference"] == 46_100.0 and got[("PHP", "2026-10-02")]["method"] == "secdef"
    # ABB did not trade 01/10 in the averages table, and nothing precedes 01/10: both left empty, said why.
    assert ("ABB", "2026-10-02") not in got and "không có lệnh khớp" in report.failed["ABB"]
    assert ("PHP", "2026-10-01") not in report.stored.items()
    # A published definition read later replaces the filled value.
    rp.collect_reference_prices(["PHP"], defs(PHP={"reference": 47_300.0, "ceiling": 54_300.0, "floor": 40_300.0,
                                                   "published_at": "2026-10-02 20:46:00"}), CFG, "dnse_openapi", upcom)
    assert rp.lookup([("PHP", "2026-10-06")], upcom)[("PHP", "2026-10-06")]["method"] == "secdef"


def test_latest_only_targets_one_session_per_ticker(upcom):
    assert rp.fill_targets(["PHP", "ABB", "VIC"], 5, latest_only=True, path=upcom) == [("PHP", "2026-10-06"), ("ABB", "2026-10-06")]


def test_a_reference_on_another_price_scale_is_not_used():
    # GMD 06/10: reference 77,800 published that morning, bar rescaled to 52,000 that evening.
    ref = {"reference": 77_800.0, "ceiling": 83_200.0, "floor": 72_400.0}
    got = rp.change(52_000.0, 51_870.0, ref, "HOSE")
    assert got["basis"] == "close" and got["pct"] == 0.25
    assert rp.plausible(52_000.0, 51_866.67, "HOSE") and not rp.plausible(52_000.0, 77_800.0, "HOSE")
    assert rp.plausible(56_000.0, 49_100.0, "UPCOM")            # +14% is inside UPCoM's 15% band


def test_old_scale_references_are_read_through_the_recorded_adjustment(upcom):
    # GMD-like: rescaled by 2/3 the evening of 06/10 (ex-date 07/10); 06/10's reference was published
    # at 77,800 on the old scale, 07/10's at 52,000 on the new one.
    store(upcom, "PHP", "2026-10-06", 77_800.0)
    store(upcom, "PHP", "2026-10-07", 52_000.0)
    rp.record_adjustment("PHP", 2 / 3, date(2026, 10, 6), "dnse_openapi", upcom)
    with connect(upcom) as conn:
        conn.execute("INSERT INTO index_daily (symbol, trade_date, close, source_id, observed_at) "
                     "VALUES ('VNINDEX', '2026-10-07', 1700, 'dnse_openapi', 'x')")
    got = rp.lookup([("PHP", "2026-10-06"), ("PHP", "2026-10-07")], upcom)
    assert got[("PHP", "2026-10-06")]["reference"] == pytest.approx(51_866.67, abs=0.01)
    assert got[("PHP", "2026-10-07")]["reference"] == 52_000.0
    # The board showed +0.26% on 06/10 (78,000 / 77,800); on the new scale: 52,000 / 51,866.67.
    assert rp.change(52_000.0, 51_870.0, got[("PHP", "2026-10-06")], "HOSE")["pct"] == 0.26


def test_an_upcom_ex_date_is_filled_on_the_new_scale(upcom):
    # The tape's average of the last old-scale session, put on the new scale before rounding.
    rp.record_adjustment("ABB", 0.9, date(2026, 10, 2), "dnse_openapi", upcom)
    rp.fill_from_averages([("ABB", "2026-10-06")], fetch_avg, {"fill_max_gap_days": 14}, 15.0, "dnse_openapi", upcom)
    got = rp.lookup([("ABB", "2026-10-06")], upcom)[("ABB", "2026-10-06")]
    assert got["reference"] == 15_900.0 and got["method"] == "avg_price_adj"     # 17,684 x 0.9 = 15,915.6 -> 15,900


def test_an_old_scale_session_reproduces_the_board_exactly():
    # TV1 (UPCoM) 06/10/2026: traded 20,600 against a 20,500 reference (+0.49% on the board); DNSE
    # stored the bar rescaled by 0.92786 and rounded to 19,110.
    ref = {"reference": 20_500 * 0.92786, "published_reference": 20_500.0, "scaled": 0.92786,
           "ceiling": 0.0, "floor": 0.0}
    assert rp.board_pct(19_110.0, ref, "UPCOM") == 0.49
    # GMD (HOSE): 52,000 / (2/3) = 78,000 against 77,800 published -> +0.26%
    gmd = {"reference": 77_800 * 2 / 3, "published_reference": 77_800.0, "scaled": 2 / 3, "ceiling": 0.0, "floor": 0.0}
    assert rp.board_pct(52_000.0, gmd, "HOSE") == 0.26
