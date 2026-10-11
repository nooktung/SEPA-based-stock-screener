from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd
import pytest

from src.collectors.base import ProviderDataMissing
from src.collectors.providers.vietstock_industry_provider import IndustryPath, fetch_industry, parse_industry_page
from src.database import init_db, sync_reference_data
from src.quant.industry import industry_strength, load_industries, needs_refresh, store_industry
from src.quant.screener import screen_settings

S = screen_settings()
ARROW = '<span class=company-general-sector-arrow><svg width=5><path d="M0"/></svg></span>'


def profile(*links: tuple[str, str], mobile_too: bool = True) -> str:
    crumbs = ARROW.join(f'<span class=company-general-sector-link> <a href=/nganh/{c}-slug-.htm>{n}</a> </span>'
                        for c, n in links)
    block = f'<span class=company-general-sector-text>Ng&#224;nh: </span> {crumbs}</div>'
    desk = f'<div class=company-general-sector id=company-general-sector-desk>{block}'
    mobi = f'<div class=company-general-sector id=company-general-sector-mobi>{block}' if mobile_too else ""
    # an unrelated sector menu link elsewhere on the page must not be picked up
    return f'<a href=/nganh/60-bat-dong-san-.htm>Bất động sản</a>{desk}</div>{mobi}'


MST = profile(("20", "C&#244;ng nghiệp"), ("2010", "H&#224;ng h&#243;a c&#244;ng nghiệp"),
              ("201030", "Kỹ thuật x&#226;y dựng"))


def test_breadcrumb_parsed_broadest_first_and_unescaped():
    assert parse_industry_page(MST) == (("20", "Công nghiệp"), ("2010", "Hàng hóa công nghiệp"),
                                        ("201030", "Kỹ thuật xây dựng"))


def test_missing_or_inconsistent_breadcrumb_is_refused_not_guessed():
    with pytest.raises(ProviderDataMissing):
        parse_industry_page("<html>no industry here</html>")
    with pytest.raises(ProviderDataMissing):
        parse_industry_page(profile(("20", "A"), ("4010", "B")))


def test_coarse_classification_is_kept_at_its_level(tmp_path):
    db = tmp_path / "i.db"
    init_db(db)
    sync_reference_data(db)
    store_industry(fetch_industry("abc", fetch=lambda url: profile(("40", "Tài chính"))), db)
    row = load_industries(db)["ABC"]
    assert row["sector"] == "Tài chính" and row["group_code"] is None and "ABC" in row["source_url"]


def _frame(slope: float, n: int = 260) -> pd.DataFrame:
    close = 10_000 * np.exp(slope * np.arange(n))
    idx = pd.bdate_range("2025-09-01", periods=n)
    return pd.DataFrame({"open": close, "high": close, "low": close, "close": close, "volume": 1e6}, index=idx)


def _row(sector, group, name):
    return {"sector_code": sector, "sector": "S" + sector, "group_code": group, "industry_group": name,
            "industry_code": None, "industry": None}


def test_industry_rs_ranks_groups_and_skips_small_ones():
    frames = {t: _frame(0.002) for t in ("A1", "A2", "A3")} | {t: _frame(-0.001) for t in ("B1", "B2", "B3")} \
        | {"C1": _frame(0.004)}
    raw = {"A1": 0.5, "A2": 0.4, "A3": 0.6, "B1": -0.2, "B2": -0.1, "B3": 0.0, "C1": 0.9}
    classes = {t: _row("20", "2010", "Up") for t in ("A1", "A2", "A3")} \
        | {t: _row("30", "3010", "Down") for t in ("B1", "B2", "B3")} | {"C1": _row("40", "4010", "Solo")}
    stats, of = industry_strength(frames, raw, {"A1": "A", "B1": "C"}, classes, S)
    assert of["A1"] == "2010" and of["C1"] == "4010"
    assert stats["2010"].rs_percentile > stats["3010"].rs_percentile
    assert stats["4010"].rs_percentile is None          # one ticker is not an industry trend
    assert stats["2010"].above_ma50_pct == 100.0 and stats["3010"].above_ma50_pct == 0.0
    assert stats["2010"].leaders == ["A1"] and stats["3010"].leaders == []


def test_unclassified_ticker_gets_no_industry():
    stats, of = industry_strength({"X": _frame(0.001)}, {"X": 0.1}, {}, {}, S)
    assert not stats and not of


def test_refresh_after_configured_days():
    now = datetime(2026, 9, 27, tzinfo=timezone.utc)
    known = {"HPG": {"observed_at": (now - timedelta(days=10)).isoformat()},
             "SSI": {"observed_at": (now - timedelta(days=40)).isoformat()}}
    assert not needs_refresh("HPG", known, 30, now)
    assert needs_refresh("SSI", known, 30, now) and needs_refresh("VCB", known, 30, now)


def test_industry_path_levels():
    p = IndustryPath("X", (("20", "a"), ("2010", "b")), "u")
    assert p.level(2) == ("2010", "b") and p.level(3) is None


def test_the_company_name_comes_from_the_page_title():
    from src.collectors.providers.vietstock_industry_provider import fetch_profile, parse_company_name
    page = ("<html><head><title>HCM: CTCP Chứng khoán Thành phố Hồ Chí Minh - HSC - Hồ sơ doanh nghiệp | "
            "VietstockFinance</title></head><body>" + MST + "</body></html>")
    assert parse_company_name(page, "HCM") == "CTCP Chứng khoán Thành phố Hồ Chí Minh - HSC"
    assert parse_company_name(page, "SSI") is None                   # another ticker's page: no name
    assert parse_company_name("<title>AAH: CTCP Hợp Nhất - - Hồ sơ doanh nghiệp</title>", "AAH") == "CTCP Hợp Nhất"
    assert fetch_industry("HCM", fetch=lambda url: page).name == "CTCP Chứng khoán Thành phố Hồ Chí Minh - HSC"
    name, path_ = fetch_profile("HCM", fetch=lambda url: "<title>HCM: CTCP Chứng khoán X - Hồ sơ doanh nghiệp</title>")
    assert name == "CTCP Chứng khoán X" and path_ is None              # no breadcrumb: the name still counts
