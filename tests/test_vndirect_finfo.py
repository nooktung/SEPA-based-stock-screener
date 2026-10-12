"""Banks and securities firms on their own templates (VNDirect finfo), beside CafeF's TT200 statements."""

from datetime import date

import pytest

from src.collectors.base import ProviderDataMissing
from src.collectors.providers import vndirect_finfo_provider as vnd
from src.database import connect, init_db
from src.quant import fundamentals as fu

# VCB Q2/2026 and Q2/2025 as VNDirect served them on 2026-10-12 (model 102, full VND).
VCB = {"2026-06-30": {421701: 26371601e6, 421900: 19141761e6, 422900: 502625e6, 23800: 17419725e6,
                      23003: 14550959e6, 23000: 14542276e6, 23500: 8683e6},
       "2025-06-30": {421701: 17868237e6, 421900: 14160192e6, 422900: 809590e6, 23800: 11033915e6,
                      23003: 8837371e6, 23000: 8831885e6, 23500: 5486e6}}


def rows(data, model=102):
    return [{"code": "VCB", "itemCode": float(code), "reportType": "QUARTER", "modelType": float(model),
             "numericValue": value, "fiscalDate": day} for day, items in data.items() for code, value in items.items()]


def models(rename=None):
    names = {102: {421701: "Tổng thu nhập hoạt động", 421900: "Thu nhập lãi thuần",
                   422900: "Chi phí dự phòng rủi ro tín dụng", 23800: "Lợi nhuận kế toán trước thuế",
                   23003: "Lợi nhuận sau thuế thu nhập doanh nghiệp", 23000: "Lợi nhuận sau thuế của Công ty mẹ",
                   23500: "Lợi ích của cổ đông thiểu số"},
             90: {21000: "Tổng doanh thu hoạt động kinh doanh", 700039: "Lãi từ các tài sản tài chính ( FVTPL)",
                  621110: "Doanh thu môi giới chứng khoán cho người đầu tư", 700044: "Lãi từ các khoản cho vay và phải thu",
                  23800: "Lợi nhuận kế toán trước thuế", 23003: "Lợi nhuận sau thuế thu nhập doanh nghiệp",
                  23000: "Lợi nhuận sau thuế của Công ty mẹ", 23500: "Lợi ích của cổ đông thiểu số"}}
    if rename:
        names[102][rename] = "Một chỉ tiêu khác"
    return {m: [{"itemCode": float(c), "itemVnName": n} for c, n in items.items()] for m, items in names.items()}


def test_a_bank_template_is_read_by_item_code():
    template, out = vnd.parse_statements(rows(VCB))
    assert template == "bank"
    q = out[date(2026, 6, 30)]
    assert q["revenue_net"] == 26371601e6 and q["net_income_parent"] == 14542276e6
    assert q["net_income"] == q["net_income_parent"] + q["net_income_minority"]       # the identity the check uses


def test_an_item_that_changed_its_name_is_refused_not_mapped_by_position():
    vnd.check_items(models())
    with pytest.raises(ProviderDataMissing):
        vnd.check_items(models(rename=23000))


def test_an_ordinary_company_has_no_bank_or_securities_template_here():
    with pytest.raises(ProviderDataMissing):
        vnd.parse_statements(rows(VCB, model=2))


def test_a_quarter_whose_profit_lines_disagree_is_rejected():
    """SSI Q2/2025: net profit 967.6 bn, parent 862.3 bn + minority -1.9 bn (VNDirect, 2026-10-12)."""
    bad = {"2025-06-30": {21000: 2993036e6, 700039: 1e12, 23003: 967599e6, 23000: 862323e6, 23500: -1907e6}}
    _, data = vnd.fetch_quarterly_income("SSI", date(2026, 10, 12), lambda url: {"data": rows(bad, model=90)})
    assert not data.quarters and "LNST" in data.rejected[date(2025, 6, 30)]


def test_quarter_ends_count_back_from_the_last_completed_quarter():
    assert vnd.quarter_ends(date(2026, 10, 12), 3) == [date(2026, 9, 30), date(2026, 6, 30), date(2026, 3, 31)]


def test_the_bank_is_graded_on_its_own_top_line_and_vndirect_wins_over_cafef(tmp_path):
    path = tmp_path / "f.db"
    init_db(path)
    with connect(path) as conn:
        for sid in ("cafef_financial", "vndirect_finfo"):
            conn.execute("INSERT INTO sources (source_id, kind, source_level, enabled) VALUES (?, 'financial', 3, 1)", (sid,))
    quarters = {}
    for year in (2024, 2025, 2026):
        for month, day in ((3, 31), (6, 30), (9, 30), (12, 31)):
            d = date(year, month, day)
            if d > date(2026, 6, 30):
                continue
            k = (year - 2024) * 4 + month // 3
            parent = 5e12 * (1.25 ** (k / 4))
            quarters[d] = {"revenue_net": 15e12 * (1.2 ** (k / 4)), "net_interest_income": 11e12 * (1.15 ** (k / 4)),
                           "provision_expense": 1e12, "net_income": parent + 1e9, "net_income_parent": parent,
                           "net_income_minority": 1e9}
    from src.collectors.providers.cafef_financial_provider import QuarterlyIncome
    fu.store_income(QuarterlyIncome("VCB", {date(2026, 6, 30): {"revenue_net": 1.0, "net_income_parent": 1.0}}, {}, ["c"]), path)
    fu.store_income(QuarterlyIncome("VCB", quarters, {}, ["v"]), path, source_id=vnd.SOURCE_ID)
    fu.log_fetch(QuarterlyIncome("VCB", quarters, {}, ["v"]), path, source_id=vnd.SOURCE_ID)
    assert fu.load_quarters("VCB", path)[date(2026, 6, 30)]["revenue_net"] > 1e12       # not CafeF's misplaced row
    from src.quant.screener import screen_settings
    read = fu.grade_ticker("VCB", screen_settings(), path).as_dict()
    assert read["template"] == "bank" and read["revenue_text"].startswith("tổng thu nhập hoạt động")
    assert read["source"].startswith("VNDirect") and any("Thu nhập lãi thuần" in n for n in read["notes"])
    assert fu.fetch_log("VCB", path)["source_id"] == vnd.SOURCE_ID


def test_a_brokers_note_names_its_proprietary_trading():
    q = {date(2026, 6, 30): {"revenue_net": 1349e9, "fvtpl_gain": 929e9, "brokerage_revenue": 39e9},
         date(2025, 6, 30): {"revenue_net": 1975e9, "fvtpl_gain": 1690e9, "brokerage_revenue": 42e9}}
    assert fu.template_of(q) == "securities"
    notes = fu.financial_notes("securities", q)
    assert notes[0].startswith("Lãi tài sản FVTPL (tự doanh) 929 tỷ đồng = 69% doanh thu hoạt động (-45%")
