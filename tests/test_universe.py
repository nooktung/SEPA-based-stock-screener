import pytest

from src.collectors.base import ProviderDataMissing
from src.collectors.universe import fetch_instruments, filter_instruments, sync_universe
from src.config import UniverseConfig, load_universe
from src.database import connect, init_db


def _row(symbol, market="STO", group="ST", name=None, indices=None):
    return {"symbol": symbol, "marketId": market, "securityGroupId": group,
            "symbolType": "", "listedDate": "2020-01-02", "shortName": symbol,
            "name": name or symbol, "indexName": indices}


# Real shapes observed on /market/instruments, one per exclusion rule.
LISTING = [
    _row("SSI", "STO", indices=["VN100", "VN30"]),
    _row("PVS", "STX"),
    _row("ACV", "UPX"),
    _row("PC1", "STO"),                       # genuine share with a digit
    _row("41I1GA000", "DVX", "FU"),           # VN30 futures
    _row("CACB2601", "STO", "EW"),            # covered warrant
    _row("ETF", "STO", "EF"),                 # ETF with a 3-char code
    _row("E1VFVN30", "STO", "EF"),
    _row("VN30F2305", "STX", "ST"),           # futures mislabelled as ST
    _row("SGBTB", "UPX", "ST"),               # 5-char code tagged ST
    _row("HNX", "STX", ""),                   # index
    _row("BVB", "HCX", "BS"),                 # bond market
]


@pytest.fixture
def cfg():
    return load_universe()


def test_config_limits_universe_to_three_exchanges(cfg):
    assert set(cfg.markets.values()) == {"HOSE", "HNX", "UPCOM"}
    assert cfg.security_groups == ("ST",)
    assert cfg.symbol_length == 3


def test_filter_keeps_only_three_char_shares_on_three_exchanges(cfg):
    members = filter_instruments(LISTING, cfg)
    assert [(m.ticker, m.exchange) for m in members] == [
        ("ACV", "UPCOM"), ("PC1", "HOSE"), ("PVS", "HNX"), ("SSI", "HOSE"),
    ]


def test_letters_only_mode_drops_digit_codes(cfg):
    strict = UniverseConfig(cfg.markets, cfg.security_groups, cfg.symbol_length, allow_digits=False)
    assert "PC1" not in {m.ticker for m in filter_instruments(LISTING, strict)}


def test_members_carry_provenance_and_index_membership(cfg):
    ssi = next(m for m in filter_instruments(LISTING, cfg, observed_at="t0") if m.ticker == "SSI")
    assert ssi.source_id == "dnse"
    assert ssi.observed_at == "t0"
    assert ssi.index_membership == ("VN100", "VN30")


class _PagedClient:
    def __init__(self, pages, total):
        self.pages, self.total, self.calls = pages, total, []

    def get(self, path, query=None):
        self.calls.append(query["page"])
        data = self.pages[query["page"] - 1] if query["page"] <= len(self.pages) else []
        return {"data": data, "total": self.total, "page": query["page"], "pageSize": 100}


def test_fetch_walks_every_page():
    client = _PagedClient([LISTING[:5], LISTING[5:10], LISTING[10:]], total=len(LISTING))
    assert fetch_instruments(client) == LISTING
    assert client.calls == [1, 2, 3]


def test_fetch_refuses_a_partial_listing():
    client = _PagedClient([LISTING[:5]], total=len(LISTING))
    with pytest.raises(ProviderDataMissing):
        fetch_instruments(client)


def test_sync_upserts_and_deactivates_delisted(tmp_path, cfg):
    db = tmp_path / "u.db"
    init_db(db)
    first = sync_universe(filter_instruments(LISTING, cfg), db)
    assert first == {"listed": 4, "added": 4, "deactivated": 0}

    second = sync_universe(filter_instruments([r for r in LISTING if r["symbol"] != "ACV"], cfg), db)
    assert second == {"listed": 3, "added": 0, "deactivated": 1}
    with connect(db) as conn:
        assert conn.execute("SELECT active FROM tickers WHERE ticker='ACV'").fetchone()[0] == 0
        assert conn.execute("SELECT exchange FROM tickers WHERE ticker='PVS'").fetchone()[0] == "HNX"


def test_sync_never_overwrites_curated_sector_profile(tmp_path, cfg):
    db = tmp_path / "u.db"
    init_db(db)
    with connect(db) as conn:
        conn.execute("INSERT INTO tickers (ticker, exchange, sector_profile, created_at, updated_at) "
                     "VALUES ('SSI', 'HOSE', 'securities', 'x', 'x')")
    sync_universe(filter_instruments(LISTING, cfg), db)
    with connect(db) as conn:
        assert conn.execute("SELECT sector_profile FROM tickers WHERE ticker='SSI'").fetchone()[0] == "securities"


def test_sync_refuses_empty_universe(tmp_path):
    db = tmp_path / "u.db"
    init_db(db)
    with pytest.raises(ProviderDataMissing):
        sync_universe([], db)
