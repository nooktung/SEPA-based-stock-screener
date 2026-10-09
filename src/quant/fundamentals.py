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
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from src.collectors.providers.cafef_financial_provider import SOURCE_ID, QuarterlyIncome
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

    @property
    def score(self) -> float | None:
        return GRADE_SCORE.get(self.grade)

    def as_dict(self) -> dict[str, Any]:
        r = lambda v: None if v is None else round(v, 1)  # noqa: E731
        return {"grade": self.grade, "grade_text": GRADE_TEXT[self.grade],
                "latest_quarter": self.latest_quarter.isoformat() if self.latest_quarter else None,
                "profit_yoy": [r(v) for v in self.profit_yoy], "revenue_yoy": [r(v) for v in self.revenue_yoy],
                "margin_change": r(self.margin_change), "code33": self.code33, "reasons": list(self.reasons),
                "source": "CafeF (tổng hợp, chưa đối chiếu BCTC gốc)", "eps_proxy": "LNST công ty mẹ"}


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
        read.reasons.append(f"LNST công ty mẹ giảm {p0:.0f}% so với cùng kỳ")
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

def store_income(data: QuarterlyIncome, path: Path | None = None) -> int:
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
                (data.ticker, d.isoformat(), SOURCE_ID, url, now))
            for name, value in values.items():
                conn.execute(
                    "INSERT INTO financial_metrics (ticker, period_end, period_type, metric_name, value, unit, "
                    "assertion_type, source_id, source_url, observed_at) VALUES (?, ?, 'Q', ?, ?, 'VND', 'ESTIMATE', ?, ?, ?) "
                    "ON CONFLICT(ticker, period_end, period_type, metric_name) DO UPDATE SET value=excluded.value, "
                    "source_id=excluded.source_id, source_url=excluded.source_url, observed_at=excluded.observed_at",
                    (data.ticker, d.isoformat(), name, value, SOURCE_ID, url, now))
                n += 1
    return n


def log_fetch(data: QuarterlyIncome, path: Path | None = None) -> None:
    reasons = sorted(set(data.rejected.values()))
    with connect(path) as conn:
        conn.execute(
            "INSERT INTO financial_fetch_log (ticker, source_id, checked_at, accepted_quarters, latest_quarter, "
            "rejected_reason) VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(ticker) DO UPDATE SET "
            "checked_at=excluded.checked_at, accepted_quarters=excluded.accepted_quarters, "
            "latest_quarter=excluded.latest_quarter, rejected_reason=excluded.rejected_reason",
            (data.ticker, SOURCE_ID, datetime.now(timezone.utc).isoformat(timespec="seconds"), len(data.quarters),
             max(data.quarters).isoformat() if data.quarters else None, "; ".join(reasons) or None))


def fetch_log(ticker: str, path: Path | None = None) -> dict[str, Any] | None:
    with connect(path) as conn:
        row = conn.execute("SELECT * FROM financial_fetch_log WHERE ticker = ?", (ticker.upper(),)).fetchone()
    return dict(row) if row else None


def grade_ticker(ticker: str, settings: Mapping[str, Any], path: Path | None = None) -> FundamentalRead:
    """Grade from stored quarters; a rejected layout keeps its reason."""
    read = grade(ticker, load_quarters(ticker, path), settings)
    if read.grade == "NO_DATA":
        log = fetch_log(ticker, path)
        if log is None:
            read.reasons = ["chưa tải BCTC (chạy: python -m src.main fundamentals)"]
        elif log.get("rejected_reason") and not log.get("accepted_quarters"):
            read.reasons = [f"CafeF: {log['rejected_reason']}"]
    return read


def load_quarters(ticker: str, path: Path | None = None) -> dict[date, dict[str, float | None]]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT period_end, metric_name, value FROM financial_metrics WHERE ticker = ? AND period_type = 'Q' "
            "AND source_id = ?", (ticker.upper(), SOURCE_ID)).fetchall()
    out: dict[date, dict[str, float | None]] = {}
    for d, name, value in rows:
        out.setdefault(date.fromisoformat(d), {})[name] = value
    return out


def latest_stored_quarter(ticker: str, path: Path | None = None) -> date | None:
    q = load_quarters(ticker, path)
    return max(q) if q else None
