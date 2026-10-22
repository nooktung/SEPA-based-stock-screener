"""Fundamental gate: growth grade per stock from quarterly income statements.

SEPA wants earnings that grow strongly and accelerate (Minervini's "Code 33":
three quarters of accelerating EPS, sales and margins). This module turns the
stored quarterly figures into a grade, and nothing more:

    A+       strong profit and revenue growth, profit growth accelerating
    A        strong profit growth with decent revenue growth
    B        growing, but not strongly
    WEAK     flat or mixed
    FAIL     profits clearly deteriorating: a loss, a big YoY drop, or both
             profit and revenue falling
    NO_DATA  too few trustworthy quarters -- never treated as a pass or a fail

Inputs are tier-A (aggregator, unverified) figures; the grade says so. Net
profit to the parent stands in for EPS because CafeF leaves EPS blank.

Banks and securities firms (2026-10-12) come from VNDirect's finfo API on their own
templates (src/collectors/providers/vndirect_finfo_provider.py); CafeF shows them on
the manufacturing template and their quarters are rejected there. The grade is the
same, with the template's own top line as "revenue": total operating income (TOI) for
a bank, total operating revenue for a securities firm. A broker's proprietary trading
(FVTPL gains) is shown beside it -- it is why VIX's Q2 2026 profit fell 94% while its
revenue held (CLAUDE.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

from src.collectors.providers.cafef_financial_provider import SOURCE_ID, QuarterlyIncome
from src.collectors.providers.vndirect_finfo_provider import REVENUE_TEXT, TEMPLATE_TEXT
from src.collectors.providers.vndirect_finfo_provider import SOURCE_ID as VNDIRECT_ID
from src.database import connect

GRADES = ("A+", "A", "B", "WEAK", "FAIL", "NO_DATA")
GRADE_SCORE = {"A+": 100.0, "A": 80.0, "B": 55.0, "WEAK": 30.0, "FAIL": 0.0}
GRADE_TEXT = {"A+": "A+ tăng trưởng mạnh, tăng tốc", "A": "A tăng trưởng mạnh", "B": "B có tăng trưởng",
              "WEAK": "Yếu", "FAIL": "Fail — lợi nhuận suy giảm", "NO_DATA": "Chưa có dữ liệu"}


@dataclass
class FundamentalRead:
    ticker: str
    grade: str
    latest_quarter: date | None = None
    profit_yoy: list[float | None] = field(default_factory=list)    # newest first
    revenue_yoy: list[float | None] = field(default_factory=list)
    margin_change: float | None = None                              # pp, latest vs a year earlier
    code33: bool = False
    reasons: list[str] = field(default_factory=list)
    template: str | None = None                 # None (ordinary company) | bank | securities
    notes: list[str] = field(default_factory=list)

    @property
    def score(self) -> float | None:
        return GRADE_SCORE.get(self.grade)

    def as_dict(self) -> dict[str, Any]:
        r = lambda v: None if v is None else round(v, 1)  # noqa: E731
        return {"grade": self.grade, "grade_text": GRADE_TEXT[self.grade],
                "latest_quarter": self.latest_quarter.isoformat() if self.latest_quarter else None,
                "profit_yoy": [r(v) for v in self.profit_yoy], "revenue_yoy": [r(v) for v in self.revenue_yoy],
                "margin_change": r(self.margin_change), "code33": self.code33, "reasons": list(self.reasons),
                "source": ("VNDirect (tổng hợp, chưa đối chiếu BCTC gốc)" if self.template
                           else "CafeF (tổng hợp, chưa đối chiếu BCTC gốc)"),
                "eps_proxy": "LNST công ty mẹ", "template": self.template,
                "template_text": TEMPLATE_TEXT.get(self.template or "", None),
                "revenue_text": REVENUE_TEXT.get(self.template or "", "doanh thu thuần"), "notes": list(self.notes)}


def _yoy(now: float | None, before: float | None) -> float | None:
    if now is None or before is None or before <= 0:
        return None                     # growth off a loss or zero is undefined, not huge
    return (now / before - 1) * 100.0


def grade(ticker: str, quarters: Mapping[date, Mapping[str, float | None]], settings: Mapping[str, Any]) -> FundamentalRead:
    cfg = settings["fundamentals"]
    q = sorted(quarters)
    read = FundamentalRead(ticker, "NO_DATA")
    if len(q) < int(cfg["min_quarters"]):
        read.reasons.append(f"chỉ có {len(q)} quý dữ liệu tin cậy (cần ≥ {cfg['min_quarters']})")
        return read
    by = {d: quarters[d] for d in q}

    def year_before(d: date) -> date | None:
        target = date(d.year - 1, d.month, d.day)
        return target if target in by else None

    for d in reversed(q[-3:]):
        prev = year_before(d)
        read.profit_yoy.append(_yoy(by[d].get("net_income_parent"), by[prev].get("net_income_parent")) if prev else None)
        read.revenue_yoy.append(_yoy(by[d].get("revenue_net"), by[prev].get("revenue_net")) if prev else None)
    latest = q[-1]
    read.latest_quarter = latest
    prev = year_before(latest)
    if prev is None:
        read.reasons.append("thiếu quý cùng kỳ năm trước")
        return read

    def margin(d: date) -> float | None:
        rev, ni = by[d].get("revenue_net"), by[d].get("net_income_parent")
        return None if not rev or ni is None else ni / rev * 100.0

    m_now, m_before = margin(latest), margin(prev)
    read.margin_change = None if m_now is None or m_before is None else m_now - m_before

    p0, r0 = read.profit_yoy[0], read.revenue_yoy[0]
    parent_now = by[latest].get("net_income_parent")
    accel = [v for v in read.profit_yoy if v is not None]
    accelerating = len(accel) == 3 and accel[0] > accel[1] > accel[2]
    rev_accel = [v for v in read.revenue_yoy if v is not None]
    margins = [margin(d) for d in reversed(q[-3:])]
    read.code33 = (accelerating and len(rev_accel) == 3 and rev_accel[0] > rev_accel[1] > rev_accel[2]
                   and None not in margins and margins[0] > margins[1] > margins[2])

    if parent_now is not None and parent_now < 0:
        read.grade = "FAIL"
        read.reasons.append("lỗ trong quý gần nhất")
    elif p0 is not None and p0 <= float(cfg["fail_profit_decline_pct"]):
        read.grade = "FAIL"
        read.reasons.append(f"LNST công ty mẹ giảm {abs(p0):.0f}% so với cùng kỳ")
    elif p0 is not None and r0 is not None and p0 < 0 and r0 < 0:
        read.grade = "FAIL"
        read.reasons.append(f"cả LNST ({p0:.0f}%) và doanh thu ({r0:.0f}%) cùng giảm")
    elif p0 is None:
        read.grade = "NO_DATA"
        read.reasons.append("cùng kỳ năm trước lỗ hoặc bằng 0: tăng trưởng không xác định")
    elif (p0 >= float(cfg["strong_profit_growth_pct"]) and r0 is not None
          and r0 >= float(cfg["strong_revenue_growth_pct"]) and accelerating):
        read.grade = "A+"
    elif p0 >= float(cfg["strong_profit_growth_pct"]) and r0 is not None and r0 >= float(cfg["good_revenue_growth_pct"]):
        read.grade = "A"
    elif p0 >= float(cfg["some_growth_pct"]) or (r0 is not None and r0 >= float(cfg["some_growth_pct"])):
        read.grade = "B"
    else:
        read.grade = "WEAK"
    return read


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def store_income(data: QuarterlyIncome, path: Path | None = None, source_id: str = SOURCE_ID) -> int:
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    url = data.source_urls[0] if data.source_urls else None
    n = 0
    with connect(path) as conn:
        for d, values in data.quarters.items():
            conn.execute(
                "INSERT INTO financial_periods (ticker, period_end, period_type, is_consolidated, is_audited, "
                "source_id, source_url, observed_at) VALUES (?, ?, 'Q', 1, 0, ?, ?, ?) "
                "ON CONFLICT(ticker, period_end, period_type, is_consolidated) DO UPDATE SET "
                "source_id=excluded.source_id, source_url=excluded.source_url, observed_at=excluded.observed_at",
                (data.ticker, d.isoformat(), source_id, url, now))
            for name, value in values.items():
                conn.execute(
                    "INSERT INTO financial_metrics (ticker, period_end, period_type, metric_name, value, unit, "
                    "assertion_type, source_id, source_url, observed_at) VALUES (?, ?, 'Q', ?, ?, 'VND', 'ESTIMATE', ?, ?, ?) "
                    "ON CONFLICT(ticker, period_end, period_type, metric_name) DO UPDATE SET value=excluded.value, "
                    "source_id=excluded.source_id, source_url=excluded.source_url, observed_at=excluded.observed_at",
                    (data.ticker, d.isoformat(), name, value, source_id, url, now))
                n += 1
    return n


def log_fetch(data: QuarterlyIncome, path: Path | None = None, source_id: str = SOURCE_ID) -> None:
    reasons = sorted(set(data.rejected.values()))
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO financial_fetch_log (ticker, source_id, checked_at, accepted_quarters, latest_quarter, "
            "rejected_reason) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(ticker) DO UPDATE SET "
            "source_id=excluded.source_id, checked_at=excluded.checked_at, accepted_quarters=excluded.accepted_quarters, "
            "latest_quarter=excluded.latest_quarter, rejected_reason=excluded.rejected_reason",
            (data.ticker, source_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), len(data.quarters),
             max(data.quarters).isoformat() if data.quarters else None, "; ".join(reasons) or None))


def log_reason(ticker: str, source_id: str, reason: str, path: Path | None = None) -> None:
    """Record a read that gave no usable quarter (so it is not retried every run)."""
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO financial_fetch_log (ticker, source_id, checked_at, accepted_quarters, latest_quarter, "
            "rejected_reason) VALUES (?, ?, ?, 0, NULL, ?) ON CONFLICT(ticker) DO UPDATE SET "
            "source_id=excluded.source_id, checked_at=excluded.checked_at, accepted_quarters=0, latest_quarter=NULL, "
            "rejected_reason=excluded.rejected_reason",
            (ticker.upper(), source_id, datetime.now(timezone.utc).isoformat(timespec="seconds"), reason))


def fetch_log(ticker: str, path: Path | None = None) -> dict[str, Any] | None:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM financial_fetch_log WHERE ticker = ?", (ticker.upper(),)).fetchone()
    return dict(row) if row else None


def fetch_skip_reason(log: Mapping[str, Any] | None, wanted: date, now: datetime,
                      refresh: bool = False) -> str | None:
    """Cache a completed check for one Vietnam calendar day, never a partial/failed read."""
    if refresh or not log:
        return None
    try:
        latest = date.fromisoformat(str(log.get("latest_quarter")))
    except ValueError:
        latest = None
    if latest and latest >= wanted and log.get("accepted_quarters", 0) > 0:
        return "quarter"
    try:
        checked = datetime.fromisoformat(str(log.get("checked_at")).replace("Z", "+00:00"))
    except ValueError:
        return None
    checked = checked if checked.tzinfo else checked.replace(tzinfo=timezone.utc)
    vn = timezone(timedelta(hours=7))
    if checked > now or checked.astimezone(vn).date() != now.astimezone(vn).date():
        return None
    # CafeF can log an unusable template before the VNDirect fallback fails. Such a
    # partial attempt must stay retryable, unlike a successful read of older quarters.
    complete = (log.get("accepted_quarters", 0) > 0 or log.get("source_id") == VNDIRECT_ID
                or "VNDirect:" in (log.get("rejected_reason") or ""))
    return "today" if complete else None


def history_pages(quarters: Mapping[date, Any], wanted: date, refresh: bool = False) -> int:
    """One current page suffices when the seven preceding quarters are already stored.

    Keep the entire prior-year comparison window for the three growth readings;
    a sparse/new history or an explicit refresh still downloads both pages.
    """
    from src.collectors.providers.cafef_financial_provider import quarter_end

    index = wanted.year * 4 + (wanted.month - 1) // 3
    preceding = set()
    for offset in range(1, 8):
        year, quarter = divmod(index - offset, 4)
        preceding.add(quarter_end(quarter + 1, year))
    return 1 if not refresh and preceding.issubset(quarters) else 2


def grade_ticker(ticker: str, settings: Mapping[str, Any], path: Path | None = None) -> FundamentalRead:
    """Grade from stored quarters; a rejected layout keeps its reason."""
    quarters = load_quarters(ticker, path)
    read = grade(ticker, quarters, settings)
    read.template = template_of(quarters)
    if read.template:
        read.notes = financial_notes(read.template, quarters)
    if read.grade == "NO_DATA":
        log = fetch_log(ticker, path)
        if log is None:
            read.reasons = ["chưa tải BCTC (chạy: python -m src.main fundamentals)"]
        elif log.get("rejected_reason") and not log.get("accepted_quarters"):
            read.reasons = [f"CafeF: {log['rejected_reason']}"]
    return read


def load_quarters(ticker: str, path: Path | None = None) -> dict[date, dict[str, float | None]]:
    """The ticker's stored quarters: VNDirect's bank / securities template when it has one
    (CafeF's rows for those firms are misplaced), else CafeF's."""
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT period_end, metric_name, value, source_id FROM financial_metrics WHERE ticker = ? "
            "AND period_type = 'Q' AND source_id IN (?, ?)", (ticker.upper(), SOURCE_ID, VNDIRECT_ID)).fetchall()
    use = VNDIRECT_ID if any(r[3] == VNDIRECT_ID for r in rows) else SOURCE_ID
    out: dict[date, dict[str, float | None]] = {}
    for d, name, value, source in rows:
        if source == use:
            out.setdefault(date.fromisoformat(d), {})[name] = value
    return out


def template_of(quarters: Mapping[date, Mapping[str, float | None]]) -> str | None:
    """bank / securities from the metrics only those templates carry; None for an ordinary company."""
    names = {k for v in quarters.values() for k in v}
    if "net_interest_income" in names:
        return "bank"
    if "fvtpl_gain" in names:
        return "securities"
    return None


def financial_notes(template: str, quarters: Mapping[date, Mapping[str, float | None]]) -> list[str]:
    """What drives a bank's or a broker's quarter, beside the grade (display only)."""
    from src.quant.vnfmt import num  # noqa: PLC0415

    if not quarters:
        return []
    latest = max(quarters)
    v = quarters[latest]
    prev = quarters.get(date(latest.year - 1, latest.month, latest.day), {})
    rev = v.get("revenue_net")
    out: list[str] = []

    def yoy(key: str) -> str:
        a, b = v.get(key), prev.get(key)
        return "" if a is None or not b or b <= 0 else f" ({'+' if a >= b else ''}{num((a / b - 1) * 100, 0)}% so với cùng kỳ)"

    if template == "bank":
        if v.get("net_interest_income") is not None and rev:
            out.append(f"Thu nhập lãi thuần {num(v['net_interest_income'] / 1e9)} tỷ đồng, "
                       f"{num(v['net_interest_income'] / rev * 100, 0)}% tổng thu nhập hoạt động{yoy('net_interest_income')}")
        if v.get("provision_expense") is not None:
            out.append(f"Chi phí dự phòng rủi ro tín dụng {num(abs(v['provision_expense']) / 1e9)} tỷ đồng{yoy('provision_expense')}")
    else:
        if v.get("fvtpl_gain") is not None and rev:
            out.append(f"Lãi tài sản FVTPL (tự doanh) {num(v['fvtpl_gain'] / 1e9)} tỷ đồng = "
                       f"{num(v['fvtpl_gain'] / rev * 100, 0)}% doanh thu hoạt động{yoy('fvtpl_gain')} — "
                       "phần biến động mạnh nhất của lợi nhuận công ty chứng khoán")
        for key, text in (("brokerage_revenue", "Doanh thu môi giới"), ("lending_income", "Lãi cho vay (ký quỹ)")):
            if v.get(key) is not None:
                out.append(f"{text} {num(v[key] / 1e9)} tỷ đồng{yoy(key)}")
    return out


def latest_stored_quarter(ticker: str, path: Path | None = None) -> date | None:
    q = load_quarters(ticker, path)
    return max(q) if q else None
