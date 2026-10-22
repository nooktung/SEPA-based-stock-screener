"""'Danh sách của tôi' with its master on Cloudflare KV: read first, written after, never half-done."""

import json

import numpy as np
import pandas as pd
import pytest

from src.collectors import cloudflare_kv as kv
from src.database import connect, init_db
from src.quant import user_watchlist as uw
from src.web import server


@pytest.fixture
def db(tmp_path):
    path = tmp_path / "mine.db"
    init_db(path)
    days = pd.bdate_range(end="2026-10-09", periods=30)
    with connect(path) as conn:
        conn.execute("INSERT INTO sources (source_id, kind, source_level, enabled) VALUES ('dnse_openapi','market_data',2,1)")
        for t in ("PNJ", "ABB", "FPT", "ZZZ"):
            conn.execute("INSERT INTO tickers (ticker, exchange, name, created_at, updated_at) VALUES (?, 'HOSE', ?, 'x', 'x')",
                         (t, f"CTCP {t}"))
        for t in ("PNJ", "ABB", "FPT"):                    # ZZZ is listed but has no prices
            conn.executemany("INSERT INTO prices_daily (ticker, trade_date, open, high, low, close, volume, source_id, "
                             "observed_at) VALUES (?, ?, 1, 1, 1, 1, 100, 'dnse_openapi', 'x')",
                             [(t, d.date().isoformat()) for d in days])
    return path


def test_mirror_makes_the_local_list_equal_to_the_master(db):
    uw.add("ABB", "cũ", db)
    out = uw.mirror([{"ticker": "PNJ", "added_at": "2026-10-13T07:00:00+00:00", "note": "web"},
                     {"ticker": "zzz"}, {"ticker": "XYZ1"}], db)
    assert out["added"] == ["PNJ"] and out["removed"] == ["ABB"]
    assert [t for t, _ in out["skipped"]] == ["ZZZ", "XYZ1"]
    assert uw.as_items(db) == [{"ticker": "PNJ", "added_at": "2026-10-13T07:00:00+00:00", "note": "web"}]


class FakeKv:
    """Cloudflare KV with one list per account ("mylist:<account>")."""

    def __init__(self, lists=None, fail_write=False):
        self.lists = {a: list(items) for a, items in (lists or {}).items()}   # an account absent = never written
        self.fail_write, self.writes = fail_write, []

    def read(self, account):
        if account not in self.lists:
            return {"items": [], "updated_at": None, "missing": True}
        return {"items": list(self.lists[account]), "updated_at": "2026-10-13T07:00:00+00:00"}

    def write(self, account, items):
        if self.fail_write:
            raise kv.KvError("Cloudflare KV trả 500 khi ghi danh sách")
        self.writes.append((account, items))
        self.lists[account] = items
        return {"items": items, "updated_at": "2026-10-13T08:00:00+00:00"}


@pytest.fixture
def web(monkeypatch):
    def use(fake):
        monkeypatch.setattr(server, "_web_list_enabled", lambda path: True)
        monkeypatch.setattr(kv, "read_list", lambda account, session=None: fake.read(account))
        monkeypatch.setattr(kv, "write_list", lambda account, items, session=None: fake.write(account, items))
        return fake
    return use


def item(t, at="2026-10-13T07:00:00+00:00"):
    return {"ticker": t, "added_at": at, "note": None}


def test_an_add_here_writes_the_local_accounts_list_and_keeps_what_was_added_on_the_web(db, web):
    fake = web(FakeKv({"admin": [item("PNJ")], "ban": [item("FPT")]}))
    res = server.mylist_write({"action": "add", "ticker": "abb"}, db)
    assert res["ok"] and res["web"] and "refreshing" not in res          # a test database never spawns
    assert fake.writes[-1][0] == "admin" and [i["ticker"] for i in fake.writes[-1][1]] == ["PNJ", "ABB"]
    assert uw.tickers(db) == ["PNJ", "ABB"]
    assert [i["ticker"] for i in fake.lists["ban"]] == ["FPT"]           # another account's list untouched


def test_the_local_list_holds_at_most_the_configured_number(db, monkeypatch):
    for t in ("PNJ", "ABB"):
        uw.add(t, None, db, max_items=2)
    with pytest.raises(uw.WatchlistError, match="tối đa 2 mã"):
        uw.add("FPT", None, db, max_items=2)
    uw.add("PNJ", "đã có", db, max_items=2)                              # an existing ticker still updates
    assert uw.tickers(db) == ["PNJ", "ABB"]


def test_a_failed_write_to_the_web_changes_nothing(db, web):
    uw.add("FPT", None, db)
    web(FakeKv({"admin": [item("FPT", "2026-10-01T00:00:00+00:00")]}, fail_write=True))
    with pytest.raises(ValueError, match="danh sách không đổi"):
        server.mylist_write({"action": "add", "ticker": "PNJ"}, db)
    assert uw.tickers(db) == ["FPT"]
    with pytest.raises(ValueError, match="danh sách không đổi"):
        server.mylist_write({"action": "remove", "ticker": "FPT"}, db)
    assert uw.tickers(db) == ["FPT"]


def test_a_web_list_never_written_gets_this_machines_list_instead_of_emptying_it(db, web):
    uw.add("FPT", None, db)
    uw.add("ABB", None, db)
    fake = web(FakeKv())
    server.mylist_write({"action": "add", "ticker": "PNJ"}, db)
    assert fake.writes[0][0] == "admin" and [i["ticker"] for i in fake.writes[0][1]] == ["FPT", "ABB"]   # seeded first
    assert uw.tickers(db) == ["FPT", "ABB", "PNJ"]
    assert [i["ticker"] for i in fake.lists["admin"]] == ["FPT", "ABB", "PNJ"]


@pytest.fixture
def sync(db, web, monkeypatch, tmp_path):
    """`mylist-sync` against the test database, with accounts from a test site_users.json."""
    from src import main
    from src.web import site_users

    monkeypatch.setattr(main, "init_db", lambda *a, **k: None)
    monkeypatch.setattr("src.env.load_dotenv", lambda *a, **k: None)      # never the real .env in a test
    monkeypatch.setattr(kv, "configured", lambda: True)
    monkeypatch.setattr(uw, "as_items", lambda path=None, _orig=uw.as_items: _orig(db))
    monkeypatch.setattr(uw, "mirror", lambda items, path=None, _orig=uw.mirror: _orig(items, db))
    monkeypatch.setattr(uw, "mirror_web", lambda lists, cap, path=None, _orig=uw.mirror_web: _orig(lists, cap, db))
    users = tmp_path / "site_users.json"
    monkeypatch.setattr(site_users, "USERS_FILE", users)
    refreshed = []
    monkeypatch.setattr(main, "mylist_refresh", lambda tickers: refreshed.append(list(tickers)) or 0)

    def run(fake, accounts=("admin",), args=()):
        web(fake)
        users.write_text(json.dumps({a: {"role": "owner" if a == "admin" else "viewer"} for a in accounts}), encoding="utf-8")
        return main.mylist_sync(list(args))
    return run, refreshed


def test_mylist_sync_seeds_a_new_namespace_and_keeps_the_list(db, sync):
    run, _ = sync
    uw.add("FPT", None, db)
    fake = FakeKv()
    assert run(fake) == 0                                                   # nothing changed here
    assert [i["ticker"] for i in fake.lists["admin"]] == ["FPT"] and uw.tickers(db) == ["FPT"]


def test_every_accounts_list_is_monitored_and_each_stays_its_own(db, sync):
    from src.quant.monitor import monitored_tickers

    run, refreshed = sync
    uw.add("FPT", None, db)
    fake = FakeKv({"admin": [item("FPT")], "ban": [item("PNJ"), item("ZZZ"), item("FPT")], "khach": [item("ABB")]})
    assert run(fake, ("admin", "ban", "khach", "chua-mo"), ("--refresh",)) == 10
    assert uw.tickers(db) == ["FPT"]                                        # the local list is admin's only
    assert uw.web_tickers(db) == ["ABB", "FPT", "PNJ"]                      # ZZZ has no prices: skipped
    assert refreshed == [["ABB", "FPT", "PNJ"]]
    assert {"ABB", "FPT", "PNJ"} <= set(monitored_tickers(db)[0])
    rows = {r["ticker"]: r for r in server.api_stocks({}, db)}               # published for the web copy
    assert {"ABB", "PNJ", "FPT"} <= set(rows) and rows["FPT"]["mine"] and not rows["PNJ"]["mine"]
    assert run(fake, ("admin", "ban", "khach")) == 0                        # read again: nothing new
    fake.lists["ban"] = []
    assert run(fake, ("admin", "ban", "khach")) == 10                       # PNJ left every list
    assert uw.web_tickers(db) == ["ABB"] and "PNJ" not in monitored_tickers(db)[0]
    assert run(fake, ("admin", "ban")) == 10                                # khach's account removed
    assert uw.web_tickers(db) == []


def test_an_accounts_list_past_the_cap_keeps_its_oldest(db):
    lists = {"ban": [item("PNJ", "2026-10-03"), item("ABB", "2026-10-01"), item("FPT", "2026-10-02")]}
    out = uw.mirror_web(lists, 2, db)
    assert uw.web_tickers(db) == ["ABB", "FPT"] and out["skipped"] == [("ban", "PNJ", "quá 2 mã")]


def test_without_the_web_list_the_local_list_works_as_before(db):
    assert server._web_list_enabled(db) is False
    assert server.mylist_write({"action": "add", "ticker": "PNJ"}, db)["web"] is False
    assert uw.tickers(db) == ["PNJ"]


class Resp:
    def __init__(self, status, text=""):
        self.status_code, self.text = status, text


class Session:
    def __init__(self, get=None, put=None):
        self._get, self._put, self.calls = get, put, []

    def get(self, url, headers, timeout):
        self.calls.append(("GET", url, headers))
        return self._get

    def put(self, url, data, headers, timeout):
        self.calls.append(("PUT", url, headers, data))
        return self._put


def test_the_kv_client_reads_writes_and_refuses_what_it_cannot_trust(monkeypatch):
    for k, v in (("CLOUDFLARE_API_TOKEN", "tok"), ("CLOUDFLARE_ACCOUNT_ID", "acc"), ("CLOUDFLARE_KV_MYLIST_ID", "ns")):
        monkeypatch.setenv(k, v)
    assert kv.configured()
    s = Session(get=Resp(200, json.dumps({"items": [{"ticker": "PNJ"}], "updated_at": "x"})))
    assert kv.read_list("ban", s)["items"] == [{"ticker": "PNJ"}]
    assert s.calls[0][1].endswith("/accounts/acc/storage/kv/namespaces/ns/values/mylist:ban")
    assert kv.read_list("ban", Session(get=Resp(404))) == {"items": [], "updated_at": None, "missing": True}
    seed = Session(get=Resp(404), put=Resp(200))                               # never written yet
    assert kv.read_or_seed("admin", [{"ticker": "FPT"}], seed)["items"] == [{"ticker": "FPT"}]
    assert [c[0] for c in seed.calls] == ["GET", "PUT"] and seed.calls[1][1].endswith("/values/mylist:admin")
    kept = Session(get=Resp(200, json.dumps({"items": [], "updated_at": "x"})))
    assert kv.read_or_seed("admin", [{"ticker": "FPT"}], kept)["items"] == [] and len(kept.calls) == 1
    for bad in (Resp(500), Resp(200, "not json"), Resp(200, '{"items": 3}')):
        with pytest.raises(kv.KvError):
            kv.read_list("ban", Session(get=bad))
    w = Session(put=Resp(200))
    kv.write_list("ban", [{"ticker": "ABB"}], w)
    assert json.loads(w.calls[0][3].decode("utf-8"))["items"] == [{"ticker": "ABB"}]
    with pytest.raises(kv.KvError):
        kv.write_list("ban", [], Session(put=Resp(403)))

def test_mylist_refresh_runs_every_step_for_the_new_tickers_only(monkeypatch):
    from src import main

    calls = []

    def step(name, fail=False):
        def run(args):
            calls.append((name, list(args)))
            if fail:
                raise RuntimeError("boom")
            return 0
        return run
    monkeypatch.setattr("src.env.load_dotenv", lambda *a, **k: None)
    monkeypatch.setattr(main, "init_db", lambda *a, **k: None)
    monkeypatch.setattr(uw, "refresh_requirements", lambda tickers, session: (tickers, tickers))
    for name in ("backfill", "flow", "news", "logos"):
        monkeypatch.setitem(main.COMMANDS, name, step(name))
    monkeypatch.setitem(main.COMMANDS, "monitor", step("monitor", fail=True))
    assert main.mylist_refresh(["pnj"]) == 1                               # reported, not raised
    assert [c[0] for c in calls] == ["backfill", "monitor", "flow", "news", "logos"]
    assert calls[1][1] == ["--no-fetch", "--keep-events", "--tickers", "PNJ"]   # only the new ticker


def test_refresh_reuses_current_prices_and_only_computes_missing_states(db):
    with connect(db) as conn:
        conn.execute("INSERT INTO monitor_state VALUES ('PNJ', '2026-10-09', ?, 'now')",
                     (json.dumps({"tech": {"rsi": 55}}),))
        conn.execute("INSERT INTO monitor_state VALUES ('ABB', '2026-10-08', ?, 'now')",
                     (json.dumps({"tech": {"rsi": 50}}),))
    assert uw.refresh_requirements(["PNJ", "ABB", "FPT", "ZZZ"], "2026-10-09", db) == (["ZZZ"], ["ABB", "FPT", "ZZZ"])
    assert uw.refresh_requirements(["PNJ", "ABB"], "2026-10-12", db) == (["PNJ", "ABB"], ["PNJ", "ABB"])
