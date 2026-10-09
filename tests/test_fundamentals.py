from datetime import date

import pytest

from src.collectors.base import ProviderDataMissing
from src.collectors.providers.cafef_financial_provider import (
    fetch_quarterly_income,
    latest_completed_quarter,
    parse_income_page,
    parse_vnd,
    validate_quarter,
)
from src.database import connect, init_db, sync_reference_data
from src.quant.fundamentals import grade, grade_ticker, log_fetch, store_income
from src.quant.screener import screen_settings

S = screen_settings()


def page(quarters, rows):
    """A CafeF-shaped income page: quarter headers, then label + one cell per quarter."""
    head = "".join(f"<td>Quý {q}- {y}</td>" for q, y in quarters)
    body = "".join("<tr><td>" + label + "</td>" + "".join(f"<td>{v}</td>" for v in values) + "<td></td></tr>"
                   for label, values in rows)
    return f"<table><tr><td>Mã</td>{head}</tr>{body}</table>"


HPG_LIKE = page([(3, 2025), (4, 2025), (1, 2026), (2, 2026)], [
    ("3. Doanh thu thuần về bán hàng và cung cấp dịch vụ (10 = 01 - 02)",
     ["36.407.416.403.924", "46.176.484.549.771", "52.900.847.302.653", "55.158.902.298.901"]),
    ("18. Lợi nhuận sau thuế thu nhập doanh nghiệp(60=50-51-52)",
     ["4.012.252.316.723", "3.888.349.733.267", "9.055.918.200.023", "6.424.474.267.094"]),
    ("19. Lợi nhuận sau thuế công ty mẹ",
     ["3.988.318.474.539", "3.860.994.446.656", "8.994.003.244.142", "6.371.019.004.563"]),
    ("20. Lợi nhuận sau thuế công ty mẹ không kiểm soát",
     ["23.933.842.184", "27.355.286.611", "61.914.955.881", "53.455.262.531"]),
])

# SSI as CafeF shows it: a broker on the manufacturing template, parent profit blank.
BROKER_LIKE = page([(3, 2025), (4, 2025), (1, 2026), (2, 2026)], [
    ("3. Doanh thu thuần về bán hàng và cung cấp dịch vụ (10 = 01 - 02)", ["5.679.144.335"] * 4),
    ("18. Lợi nhuận sau thuế thu nhập doanh nghiệp(60=50-51-52)", ["698.013.180.762"] * 4),
    ("19. Lợi nhuận sau thuế công ty mẹ", [""] * 4),
    ("20. Lợi nhuận sau thuế công ty mẹ không kiểm soát", ["63.565.395.575"] * 4),
])


def test_values_are_full_vnd_despite_the_ty_dong_label():
    assert parse_vnd("2.065.597.768.695") == 2_065_597_768_695.0
    assert parse_vnd("(1.234)") == -1234.0 and parse_vnd("") is None


def test_page_parsed_by_quarter_in_column_order():
    data = parse_income_page(HPG_LIKE)
    assert list(data) == [date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)]
    assert data[date(2026, 6, 30)]["net_income_parent"] == 6_371_019_004_563.0


def test_non_consecutive_quarter_headers_are_refused():
    with pytest.raises(ProviderDataMissing):
        parse_income_page(page([(3, 2025), (1, 2026), (2, 2026), (4, 2025)], []))


def test_accounting_check_accepts_real_rows_and_rejects_a_broker_layout():
    good = parse_income_page(HPG_LIKE)[date(2026, 6, 30)]
    assert validate_quarter(good) is None
    bad = parse_income_page(BROKER_LIKE)[date(2026, 6, 30)]
    assert "thiếu" in validate_quarter(bad)
    shifted = dict(good, net_income_parent=good["net_income_parent"] * 0.5)
    assert "≠" in validate_quarter(shifted)


def test_latest_completed_quarter():
    assert latest_completed_quarter(date(2026, 9, 27)) == (2026, 2)
    assert latest_completed_quarter(date(2026, 2, 1)) == (2025, 4)


def _q(profit, revenue):
    return {"net_income_parent": profit, "net_income": profit, "revenue_net": revenue}


def _series(profits, revenues):
    ends = [date(2024, 9, 30), date(2024, 12, 31), date(2025, 3, 31), date(2025, 6, 30),
            date(2025, 9, 30), date(2025, 12, 31), date(2026, 3, 31), date(2026, 6, 30)]
    return {d: _q(p, r) for d, p, r in zip(ends, profits, revenues)}


def test_strong_accelerating_growth_is_a_plus():
    q = _series([100, 100, 100, 100, 110, 125, 150, 200], [1000] * 4 + [1100, 1150, 1200, 1300])
    r = grade("X", q, S)
    assert r.grade == "A+" and r.profit_yoy[0] == pytest.approx(100.0)


def test_profit_collapse_is_fail_and_a_loss_is_fail():
    assert grade("X", _series([100] * 7 + [60], [1000] * 8), S).grade == "FAIL"
    assert grade("X", _series([100] * 7 + [-5], [1000] * 8), S).grade == "FAIL"


def test_growth_off_a_loss_is_not_data_not_a_pass():
    # 2025-06-30 (a year before the latest quarter) was a loss
    r = grade("X", _series([100, 100, 100, -10, 100, 100, 100, 100], [1000] * 8), S)
    assert r.grade == "NO_DATA"


def test_too_few_quarters_is_no_data():
    assert grade("X", dict(list(_series([100] * 8, [1000] * 8).items())[:3]), S).grade == "NO_DATA"


def test_rejected_layout_keeps_its_reason(tmp_path):
    db = tmp_path / "f.db"
    init_db(db)
    sync_reference_data(db)
    pages = iter([BROKER_LIKE, BROKER_LIKE])
    data = fetch_quarterly_income("SSI", date(2026, 9, 27), fetch=lambda url: next(pages))
    assert not data.quarters and data.rejected
    store_income(data, db)
    log_fetch(data, db)
    r = grade_ticker("SSI", S, db)
    assert r.grade == "NO_DATA" and "CafeF" in r.reasons[0]


def test_stored_quarters_are_marked_unverified(tmp_path):
    db = tmp_path / "f.db"
    init_db(db)
    sync_reference_data(db)
    pages = iter([HPG_LIKE, HPG_LIKE])
    store_income(fetch_quarterly_income("HPG", date(2026, 9, 27), fetch=lambda url: next(pages)), db)
    with connect(db) as conn:
        kinds = {r[0] for r in conn.execute("SELECT DISTINCT assertion_type FROM financial_metrics")}
    assert kinds == {"ESTIMATE"}
