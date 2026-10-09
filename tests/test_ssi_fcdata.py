"""SSI FastConnect Data client: data only, token handling, paging, parsing, cross-check.

Samples are the ones in SSI's documentation (guide.ssi.com.vn, read 2026-10-04)."""

from datetime import date, datetime, timezone

import pytest

from src.collectors.base import Provenance, ProviderUnavailable, SourceLevel
from src.collectors.crosscheck import crosscheck
from src.collectors.providers.dnse_provider import FORBIDDEN_METHODS
from src.collectors.providers.ssi_fcdata_provider import (
    SsiAuthError, SsiCredentials, SsiFcDataClient, SsiFcDataProvider, SsiIndexDay, SsiStockDay)
from src.collectors.ssi_collect import collect_ssi, store_index_days, store_stock_days
from src.database import connect, init_db

CREDS = SsiCredentials("c058f55761814787882b2c8df1336e25", "144cac45770949519d2dfd20edb5b6ab")
TOKEN_OK = {"message": "Success", "status": 200, "data": {"accessToken": "eyJhbGciOiJSUzI1NiIsI"}}
STOCK_SAMPLE = {   # DailyStockPrice sample from the docs, keys as SSI writes them
    "TradingDate": "19/07/2023", "PriceChange": "-150", "PerPriceChange": "-0.70", "CeilingPrice": "21550",
    "FloorPrice": "18750", "RefPrice": "20150", "OpenPrice": "20950", "HighestPrice": "20950", "LowestPrice": "20000",
    "ClosePrice": "20000", "AveragePrice": "20118", "ClosePriceAdjusted": "17392", "TotalMatchVol": "18900",
    "TotalMatchVal": "380230000", "TotalDealVal": "0", "TotalDealVol": "0", "ForeignBuyVolTotal": "0",
    "ForeignCurrentRoom": "0", "ForeignSellVolTotal": "0", "ForeignBuyValTotal": "0", "ForeignSellValTotal": "",
    "TotalTradedVol": "18900", "Symbol": "HUB", "Time": None}
INDEX_SAMPLE = {   # DailyIndex sample from the docs
    "IndexId": "HNX30", "IndexValue": "510.56", "TradingDate": "14/08/2023", "Time": None, "Change": "19.09",
    "RatioChange": "3.89", "TotalTrade": "0", "TotalMatchVol": "84693600", "TotalMatchVal": "1836008470000",
    "IndexName": "HNX30", "Advances": "21", "NoChanges": "4", "Declines": "5", "Ceilings": "2", "Floors": "0",
    "TotalDealVol": "2504000", "TotalDealVal": "60256000000", "TotalVol": "87197600", "TotalVal": "1896264470000",
    "TradingSession": "C"}


class Resp:
    def __init__(self, status, body):
        self.status_code, self._body = status, body

    def json(self):
        return self._body


class Session:
    def __init__(self, posts=(), gets=()):
        self.posts, self.gets, self.calls = list(posts), list(gets), []

    def post(self, url, json=None, timeout=None):
        self.calls.append(("POST", url))
        return self.posts.pop(0)

    def get(self, url, params=None, timeout=None, headers=None):
        self.calls.append(("GET", url, dict(params or {}), dict(headers or {})))
        return self.gets.pop(0)


def client(session, **kw):
    waits = []
    c = SsiFcDataClient(CREDS, "https://fc-data.ssi.com.vn/", ["api/v2/Market/", "v2.0/Market/"], session=session,
                        sleep=waits.append, clock=lambda: 0.0, interval_seconds=0, **kw)
    return c, waits


# ---------------------------------------------------------------- data only
def test_the_trading_host_is_refused_before_any_request():
    s = Session()
    with pytest.raises(PermissionError):
        SsiFcDataClient(CREDS, "https://fc-tradeapi.ssi.com.vn/", ["api/v2/"], session=s)
    assert s.calls == []


def test_only_token_and_market_data_reads_are_allowed():
    s = Session(posts=[Resp(200, TOKEN_OK)])
    c, _ = client(s)
    for endpoint in ("NewOrder", "CancelOrder", "ModifyOrder", "stockPosition", "AccessToken"):
        with pytest.raises(PermissionError):
            c.get(endpoint, {})
    assert s.calls == []                                   # refused before a token was even asked for
    assert not any(hasattr(c, name) for name in FORBIDDEN_METHODS)


def test_credentials_come_from_env_and_never_print(monkeypatch):
    monkeypatch.delenv("SSI_CONSUMER_ID", raising=False)
    with pytest.raises(SsiAuthError, match="SSI_CONSUMER_ID"):
        SsiCredentials.from_env()
    assert "144cac" not in repr(CREDS) and "redacted" in repr(CREDS)


# ---------------------------------------------------------------- token, paging, quota
def test_the_working_api_path_is_found_and_the_token_sent_as_bearer():
    s = Session(posts=[Resp(404, {"message": "not found"}), Resp(200, TOKEN_OK)],
                gets=[Resp(200, {"status": "Success", "message": "Success", "data": [], "totalRecord": 0})])
    c, _ = client(s)
    c.get("DailyIndex", {"indexId": "VNIndex"})
    assert [x[1] for x in s.calls] == ["https://fc-data.ssi.com.vn/api/v2/Market/AccessToken",
                                      "https://fc-data.ssi.com.vn/v2.0/Market/AccessToken",
                                      "https://fc-data.ssi.com.vn/v2.0/Market/DailyIndex"]
    assert s.calls[-1][3]["Authorization"] == "Bearer eyJhbGciOiJSUzI1NiIsI"


def test_an_expired_token_is_renewed_once():
    ok = {"status": "Success", "message": "Success", "data": [STOCK_SAMPLE], "totalRecord": 1}
    s = Session(posts=[Resp(200, TOKEN_OK), Resp(200, TOKEN_OK)], gets=[Resp(401, {"message": "Unauthorized"}), Resp(200, ok)])
    c, _ = client(s)
    assert c.get("DailyStockPrice", {})["data"][0]["Symbol"] == "HUB"
    assert [x[0] for x in s.calls] == ["POST", "GET", "POST", "GET"]


def test_quota_backs_off_then_gives_up_without_inventing_data():
    quota = Resp(200, {"status": 429, "message": "API calls quota exceeded! maximum admitted 1 per 1s"})
    ok = Resp(200, {"status": "Success", "message": "Success", "data": [], "totalRecord": 0})
    s = Session(posts=[Resp(200, TOKEN_OK)], gets=[quota, ok])
    c, waits = client(s, quota_waits=[5, 15])
    c.get("DailyIndex", {})
    assert waits == [5]
    s = Session(posts=[Resp(200, TOKEN_OK)], gets=[quota, quota, quota])
    c, waits = client(s, quota_waits=[5, 15])
    with pytest.raises(ProviderUnavailable, match="giới hạn"):
        c.get("DailyIndex", {})


def test_pages_until_every_record_is_read():
    page = lambda n: Resp(200, {"status": "Success", "message": "Success", "data": [STOCK_SAMPLE] * n, "totalRecord": 1500})  # noqa: E731
    s = Session(posts=[Resp(200, TOKEN_OK)], gets=[page(1000), page(500)])
    c, _ = client(s)
    assert len(c.pages("DailyStockPrice", {"market": "HOSE"})) == 1500
    assert [x[2]["pageIndex"] for x in s.calls if x[0] == "GET"] == [1, 2]


# ---------------------------------------------------------------- parsing
class FakeClient:
    def __init__(self, rows):
        self.rows, self.asked = rows, []

    def pages(self, endpoint, params):
        self.asked.append((endpoint, params))
        return self.rows


def test_daily_stock_price_fields_are_read_case_insensitively():
    p = SsiFcDataProvider("ssi_fcdata", 2, {"price_scale": 1.0}, client=FakeClient([{k.upper(): v for k, v in STOCK_SAMPLE.items()}]))
    r = p.daily_stock_prices(date(2023, 7, 19), "hose")[0]
    assert (r.ticker, r.trade_date, r.market) == ("HUB", date(2023, 7, 19), "HOSE")
    assert (r.ref_price, r.ceiling_price, r.floor_price, r.close, r.close_adjusted) == (20150, 21550, 18750, 20000, 17392)
    assert (r.match_volume, r.deal_volume, r.foreign_buy_volume) == (18900, 0, 0)
    assert r.foreign_sell_value is None                    # empty field stays missing, never 0
    assert p.client.asked[0] == ("DailyStockPrice", {"fromDate": "19/07/2023", "toDate": "19/07/2023", "market": "HOSE"})


def test_daily_index_splits_matched_and_put_through():
    p = SsiFcDataProvider("ssi_fcdata", 2, {"index_codes": {"VNINDEX": "VNIndex"}}, client=FakeClient([INDEX_SAMPLE]))
    r = p.daily_index("VNINDEX", date(2023, 8, 14), date(2023, 8, 14))[0]
    assert r.index_id == "VNINDEX" and p.client.asked[0][1]["indexId"] == "VNIndex"
    assert (r.value, r.match_volume, r.deal_volume, r.total_volume) == (510.56, 84693600, 2504000, 87197600)
    assert (r.advances, r.declines, r.unchanged, r.ceilings, r.floors) == (21, 5, 4, 2, 0)


# ---------------------------------------------------------------- collect + cross-check
PROV = Provenance("ssi_fcdata", SourceLevel.DATA_VENDOR, datetime(2026, 10, 2, 9, tzinfo=timezone.utc))


def sday(d, t, close, ref, vol, deal=0.0, fb=None, fs=None, adj=None, market="HOSE", avg=None):
    return SsiStockDay(date.fromisoformat(d), t, market, ref, None, None, close, close, close, close, adj or close, avg,
                       vol, None, deal, None, fb, fs, None, None, None, PROV)


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "x.db"
    init_db(path)
    with connect(path) as conn:
        conn.execute("INSERT INTO sources (source_id, kind, source_level, enabled) VALUES ('dnse_openapi','market_data',2,1)")
        for t in ("AAA", "BBB", "CCC"):
            conn.execute("INSERT INTO tickers (ticker, exchange, name, created_at, updated_at) VALUES (?, 'HOSE', ?, 'x', 'x')", (t, t))
        bars = [("AAA", "2026-10-01", 10000, 1000), ("AAA", "2026-10-02", 10500, 1100),     # 02/10 close differs from SSI
                ("BBB", "2026-10-01", 21000, 500), ("BBB", "2026-10-02", 19000, 900),       # ex-date 02/10: DNSE not adjusted
                ("CCC", "2026-10-01", 9000, 300), ("CCC", "2026-10-02", 9100, 330)]          # DNSE history adjusted
        conn.executemany("INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source_id, observed_at) "
                         "VALUES (?,?,?,?,?,?,?,'dnse_openapi','x')", [(t, d, c, c, c, c, v) for t, d, c, v in bars])
        conn.executemany("INSERT INTO flow_daily (scope, scope_key, trade_date, source_id, observed_at, foreign_buy_volume, "
                         "foreign_sell_volume) VALUES ('TICKER', ?, ?, 'dnse_openapi', 'x', ?, ?)",
                         [("AAA", "2026-10-02", 100, 50), ("BBB", "2026-10-02", 10, 0)])
        idx = [("2026-09-30", 1800, 600), ("2026-10-01", 1780, 780), ("2026-10-02", 1760, 700)]
        conn.executemany("INSERT INTO index_daily (symbol, trade_date, close, volume, source_id, observed_at) "
                         "VALUES ('VNINDEX', ?, ?, ?, 'dnse_openapi', 'x')", idx)
    store_stock_days([
        sday("2026-10-01", "AAA", 10000, 10000, 1000), sday("2026-10-02", "AAA", 10200, 10000, 1000, deal=100, fb=100, fs=50),
        sday("2026-10-01", "BBB", 21000, 21000, 500), sday("2026-10-02", "BBB", 19000, 20000, 900, fb=12, fs=0),
        sday("2026-10-01", "CCC", 9500, 9500, 300, adj=9000), sday("2026-10-02", "CCC", 9100, 9000, 330),
    ], path)
    ix = lambda d, m, t: SsiIndexDay(date.fromisoformat(d), "VNINDEX", None, None, None, m, None, t - m, None, t, None,  # noqa: E731
                                     None, None, None, None, None, None, PROV)
    store_index_days([ix("2026-09-30", 600, 640), ix("2026-10-01", 640, 780), ix("2026-10-02", 700, 760)], path)
    return path


def test_crosscheck_reads_closes_volumes_foreign_index_and_ex_dates(db):
    r = crosscheck(days=10, end=date(2026, 10, 2), path=db)
    s = r["stocks"]
    # AAA 02/10: DNSE 10,500 vs SSI 10,200 -> listed; CCC 01/10 matches SSI's adjusted close, not an error
    assert s["closes"] == {"khớp": 4, "lệch": 1, "khớp giá đã điều chỉnh của SSI": 1}
    assert s["close_mismatches"][0]["ticker"] == "AAA" and s["close_mismatches"][0]["diff_pct"] == 2.94
    assert s["volumes"]["khớp lệnh + thỏa thuận"] == 1 and s["volumes"]["khớp lệnh"] == 5
    assert r["foreign"]["result"] == {"khớp": 1, "lệch": 1}
    idx = r["index"]
    assert idx["dnse_volume_is"] == {"khớp lệnh": 2, "khớp lệnh + thỏa thuận": 1}
    # 01/10: -1.1% on 780 > 600 by DNSE's volume; SSI matched 640 > 600 too; 02/10: DNSE 700 < 780, SSI matched 700 > 640
    assert idx["distribution_days"]["theo DNSE (OHLC)"] == ["2026-10-01"]
    assert idx["distribution_days"]["theo DNSE khớp lệnh"] == []      # no market-index figures stored in this test
    assert idx["distribution_days"]["theo SSI khớp lệnh"] == ["2026-10-01", "2026-10-02"]
    acts = {a["ticker"]: a for a in r["corporate_actions"]}
    assert set(acts) == {"BBB", "CCC"}
    assert acts["BBB"]["change_pct"] == pytest.approx(-4.76, abs=0.01) and acts["BBB"]["dnse_history"].startswith("chưa điều chỉnh")
    assert acts["CCC"]["dnse_history"] == "đã điều chỉnh"


def test_collect_keeps_universe_tickers_and_reports_failures(db):
    class P:
        def daily_stock_prices(self, day, market):
            if market == "UPCOM":
                raise ProviderUnavailable("timeout")
            return [sday(day.isoformat(), "AAA", 1, 1, 1), sday(day.isoformat(), "E1VFVN30", 1, 1, 1)] if market == "HOSE" else []

        def daily_index(self, index_id, start, end):
            return []
    rep = collect_ssi(P(), days=0, end=date(2026, 10, 2), path=db)
    assert rep.stock_rows == {"2026-10-02": 1} and rep.skipped_outside_universe == 1
    assert rep.errors == ["2026-10-02 UPCOM: timeout"]
