"""VN-Index bars checked against HOSE's published values (report only)."""
from datetime import date

import numpy as np
import pandas as pd
import pytest

from src.collectors.index_check import collect_hose, compare, report_path, write_report
from src.collectors.providers.hose_index_provider import ALLOWED_URL, HoseIndexProvider, parse_points
from src.database import connect, init_db
from src.quant.screener import screen_settings

CFG = {"symbols": ["VNINDEX"], "sessions": 60, "status_sessions": 400, "tolerance_points": 0.011,
       "history_from": "2015-01-01", "overlap_days": 30}

# 08/10/2026 as api.hsx.vn returned it (time = midnight UTC of the session)
POINT = {"stockSymbol": "VNINDEX", "time": 1791417600, "openPrice": 1753.39, "closePrice": 1738.97,
         "highPrice": 1764.45, "lowPrice": 1722.27, "totalShare": 0, "totalValue": 0}


class Resp:
    def __init__(self, body, status=200):
        self.body, self.status_code, self.text = body, status, str(body)

    def json(self):
        return self.body


class Session:
    def __init__(self, body, status=200):
        self.calls, self.body, self.status = [], body, status

    def get(self, url, params=None, timeout=None):
        self.calls.append((url, dict(params or {})))
        return Resp(self.body, self.status)


def test_hose_points_are_read_as_sessions():
    rows = parse_points({"data": [{"chartPoints": [POINT, {**POINT, "stockSymbol": "VN30"}]}]}, "VNINDEX")
    assert rows == [{"trade_date": "2026-10-08", "open": 1753.39, "high": 1764.45, "low": 1722.27, "close": 1738.97}]


def test_the_provider_reads_one_url_only():
    session = Session({"data": [{"chartPoints": [POINT]}]})
    p = HoseIndexProvider("hose_index_chart", 1, {}, session=session)
    assert p.index_bars("vnindex", date(2026, 10, 1), date(2026, 10, 9))[0]["low"] == 1722.27
    url, params = session.calls[0]
    assert url == ALLOWED_URL and params == {"fromDate": "2026-10-01", "toDate": "2026-10-09", "symbolString": "VNINDEX|"}
    with pytest.raises(PermissionError):
        HoseIndexProvider("hose_index_chart", 1, {"base_url": "https://api.hsx.vn/other/orders"})


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "c.db"
    init_db(path)
    days = pd.bdate_range(end="2026-10-09", periods=260)
    closes = np.linspace(1500, 1800, 260)
    with connect(path) as conn:
        conn.executemany(
            "INSERT INTO index_daily (symbol, trade_date, open, high, low, close, volume, source_id, observed_at) "
            "VALUES ('VNINDEX', ?, ?, ?, ?, ?, 1000, 'dnse_openapi', 'x')",
            [(d.date().isoformat(), c, c + 2, c - 2, c) for d, c in zip(days, closes)])
    return path, [d.date().isoformat() for d in days], list(closes)


def hose_from(days, closes, low_shift=None, skip=()):
    out = []
    for d, c in zip(days, closes):
        if d in skip:
            continue
        lo = c - 2 - (low_shift or {}).get(d, 0)
        out.append({"trade_date": d, "open": c, "high": c + 2, "low": lo, "close": c})
    return out


def test_first_read_takes_the_whole_history_then_an_overlap(db):
    path, days, closes = db
    asked = []

    def fetch(symbol, start, end):
        asked.append(start)
        return hose_from(days, closes)

    collect_hose(fetch, "VNINDEX", CFG, "hose_index_chart", date(2026, 10, 9), path)
    collect_hose(fetch, "VNINDEX", CFG, "hose_index_chart", date(2026, 10, 9), path)
    assert asked == [date(2015, 1, 1), date(2026, 9, 9)]


def test_the_report_counts_agreement_and_lists_what_differs(db, tmp_path):
    path, days, closes = db
    collect_hose(lambda s, a, b: hose_from(days, closes, low_shift={days[-1]: 15.0}, skip={days[-5]})
                 or [], "VNINDEX", CFG, "hose_index_chart", date(2026, 10, 9), path)
    with connect(path) as conn:                         # the market-index extremes, stored for the last session
        conn.execute("UPDATE index_daily SET session_high = ?, session_low = ? WHERE trade_date = ?",
                     (closes[-1] + 2, closes[-1] - 17, days[-1]))
    with connect(path) as conn:                         # a session HOSE has and DNSE lacks
        conn.execute("INSERT INTO hose_index_daily VALUES ('VNINDEX', '2015-12-31', 1, 1, 1, 1, 'h', 'x')")
        conn.execute("INSERT INTO hose_index_daily VALUES ('VNINDEX', ?, 1, 1, 1, 1, 'h', 'x')",
                     ((pd.Timestamp(days[0]) + pd.Timedelta(days=-1)).date().isoformat(),))
    r = compare("VNINDEX", CFG, screen_settings(), path)
    a = r["agreement_recent"]["dnse_ohlc"]
    assert a["compared"] == 59 and a["low"] == 58 and a["high"] == 59 and a["close"] == 59
    assert r["agreement_recent"]["dnse_market_index"] == {"high": 1, "low": 1, "compared": 1}
    assert r["differ_recent"] == [{"date": days[-1], "dnse": [closes[-1] + 2, closes[-1] - 2, closes[-1]],
                                   "hose": [closes[-1] + 2, closes[-1] - 17, closes[-1]],
                                   "market_index": [closes[-1] + 2, closes[-1] - 17]}]
    assert r["history"]["missing_in_dnse"] == [] and r["history"]["low_differs"] == 1
    assert set(r["rules"]) == {"as_stored", "with_hose_high_low", "changed"}
    out = write_report(r, tmp_path)
    assert out == report_path(days[-1], tmp_path) and out.exists()


def test_a_lower_official_low_can_change_the_ftd_and_the_report_says_so(db):
    path, days, closes = db
    # a decline, then a rally attempt; HOSE's low on the latest session undercuts the rally low
    c = [1800.0] * 240 + [1780, 1760, 1740, 1700, 1705, 1710, 1715, 1720, 1722, 1725,
                           1728, 1730, 1732, 1733, 1734, 1735, 1736, 1737, 1738, 1739]
    with connect(path) as conn:
        conn.executemany("UPDATE index_daily SET open = ?, high = ?, low = ?, close = ? WHERE trade_date = ?",
                         [(x, x + 1, x - 1, x, d) for d, x in zip(days, c)])
    bars = [{"trade_date": d, "open": x, "high": x + 1, "low": x - 1, "close": x} for d, x in zip(days, c)]
    bars[-1]["low"] = 1690.0                                   # official low below the 1699 rally low
    collect_hose(lambda s, a, b: bars, "VNINDEX", CFG, "hose_index_chart", date(2026, 10, 9), path)
    r = compare("VNINDEX", CFG, screen_settings(), path)
    assert r["rules"]["as_stored"]["rally_start"] is not None
    assert r["rules"]["with_hose_high_low"]["rally_start"] is None
    assert "rally_start" in r["rules"]["changed"]
