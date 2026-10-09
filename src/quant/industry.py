"""Industry strength: is a candidate in a leading group?

Minervini and O'Neil both prefer leaders in leading industries. This module
measures it deterministically from data already in the screen, and nothing
more:

    industry RS   mean raw RS score of the industry's liquid tickers, ranked
                  as a percentile (1..99) among industries with enough members
    breadth       % of those tickers closing above their MA50 / MA200
    leaders       how many of them are in groups A/B of this screen

Classification comes from Vietstock's sector tree (tier A, unverified). A
ticker without a stored classification gets no industry -- it is never
assigned one by name or guess. Industry strength has weight 0 in the
composite score until a backtest shows it adds edge in this market.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Mapping

import pandas as pd

from src.collectors.providers.vietstock_industry_provider import SOURCE_ID, IndustryPath
from src.database import connect
from src.quant import indicators as ind


@dataclass
class IndustryStat:
    code: str
    name: str
    members: list[str] = field(default_factory=list)       # liquid tickers in this industry
    mean_rs_raw: float | None = None
    rs_percentile: float | None = None                      # None: fewer than min_members
    above_ma50_pct: float | None = None
    above_ma200_pct: float | None = None
    leaders: list[str] = field(default_factory=list)        # members in groups A/B

    def as_dict(self, leading_percentile: float | None = None) -> dict[str, Any]:
        r = lambda v: None if v is None else round(v, 1)  # noqa: E731
        return {"code": self.code, "name": self.name, "count": len(self.members),
                "rs_percentile": self.rs_percentile, "above_ma50_pct": r(self.above_ma50_pct),
                "above_ma200_pct": r(self.above_ma200_pct), "leaders": list(self.leaders),
                "leading": (self.rs_percentile is not None and leading_percentile is not None
                            and self.rs_percentile >= leading_percentile)}


def _level(row: Mapping[str, Any], depth: int) -> tuple[str, str] | None:
    keys = {1: ("sector_code", "sector"), 2: ("group_code", "industry_group"), 3: ("industry_code", "industry")}
    code_key, name_key = keys[depth]
    if row.get(code_key):
        return str(row[code_key]), str(row[name_key])
    # A company classified only to a coarser level stays at that level.
    return _level(row, depth - 1) if depth > 1 else None


def industry_strength(frames: Mapping[str, pd.DataFrame], raw_rs: Mapping[str, float],
                      groups: Mapping[str, str | None], classes: Mapping[str, Mapping[str, Any]],
                      settings: Mapping[str, Any]) -> tuple[dict[str, IndustryStat], dict[str, str]]:
    """({industry code: stat}, {ticker: industry code}) over the liquid universe given."""
    cfg = settings["industry"]
    depth = int(cfg["level"])
    stats: dict[str, IndustryStat] = {}
    of: dict[str, str] = {}
    for t, frame in frames.items():
        row = classes.get(t)
        lv = _level(row, depth) if row else None
        if lv is None:
            continue
        code, name = lv
        of[t] = code
        stats.setdefault(code, IndustryStat(code, name)).members.append(t)

    means: dict[str, float] = {}
    for code, s in stats.items():
        scores = [raw_rs[t] for t in s.members if t in raw_rs]
        closes = [frames[t]["close"].astype("float64") for t in s.members]
        above50 = [bool(c.iloc[-1] > v) for c in closes if len(c) >= 50 and (v := ind.sma(c, 50).iloc[-1]) == v]
        above200 = [bool(c.iloc[-1] > v) for c in closes if len(c) >= 200 and (v := ind.sma(c, 200).iloc[-1]) == v]
        s.above_ma50_pct = 100.0 * sum(above50) / len(above50) if above50 else None
        s.above_ma200_pct = 100.0 * sum(above200) / len(above200) if above200 else None
        s.leaders = sorted(t for t in s.members if groups.get(t) in ("A", "B"))
        if scores:
            s.mean_rs_raw = sum(scores) / len(scores)
        if len(scores) >= int(cfg["min_members"]):
            means[code] = s.mean_rs_raw
    if means:
        pct = pd.Series(means, dtype="float64").rank(pct=True, method="average") * 100.0
        for code, v in pct.items():
            stats[code].rs_percentile = float(min(99.0, max(1.0, round(v))))
    return stats, of


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def store_industry(path_: IndustryPath, db: Path | None = None) -> None:
    lv = [path_.level(d) for d in (1, 2, 3)]
    with connect(db) as conn:
        conn.execute(
            "INSERT INTO ticker_industry (ticker, sector_code, sector, group_code, industry_group, industry_code, "
            "industry, source_id, source_url, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(ticker) DO UPDATE SET sector_code=excluded.sector_code, sector=excluded.sector, "
            "group_code=excluded.group_code, industry_group=excluded.industry_group, "
            "industry_code=excluded.industry_code, industry=excluded.industry, source_id=excluded.source_id, "
            "source_url=excluded.source_url, observed_at=excluded.observed_at",
            (path_.ticker, lv[0][0], lv[0][1], *(lv[1] or (None, None)), *(lv[2] or (None, None)),
             SOURCE_ID, path_.source_url, datetime.now(timezone.utc).isoformat(timespec="seconds")))


def load_industries(db: Path | None = None) -> dict[str, dict[str, Any]]:
    with connect(db) as conn:
        return {r["ticker"]: dict(r) for r in conn.execute("SELECT * FROM ticker_industry")}


def needs_refresh(ticker: str, known: Mapping[str, Mapping[str, Any]], refresh_days: int,
                  now: datetime | None = None) -> bool:
    row = known.get(ticker)
    if not row:
        return True
    now = now or datetime.now(timezone.utc)
    return datetime.fromisoformat(row["observed_at"]) < now - timedelta(days=refresh_days)
