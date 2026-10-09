"""What changed since the last analysis (§39).

A report that repeats forty unchanged indicators every morning trains its
reader to skim it. What is worth reading is the handful of things that moved:
volume went from 1.1x to 2.2x, foreign flow flipped from selling to buying,
trap risk climbed from 15 to 28.

Two rules keep this honest.

**Nothing is invented when there is no comparison.** If no earlier analysis
exists, the report says so and returns nothing. Manufacturing "changes" against
a blank baseline would put a made-up number in the most prominent position of
the report.

**A change is a fact about two stored readings**, not a re-derivation. It reads
what was written on both dates, so the comparison survives a later change to
how a feature is computed -- and if a feature did not exist on the earlier
date, it is reported as having appeared rather than as having risen from zero.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence

from src.database import connect
from src.quant.store import load_features

log = logging.getLogger(__name__)

UP, DOWN, SAME, APPEARED, DISAPPEARED = "UP", "DOWN", "SAME", "APPEARED", "DISAPPEARED"

# The features worth leading a report with, in the order a reader wants them.
HEADLINE_FEATURES: tuple[str, ...] = (
    "close",
    "rsi14",
    "macd_hist",
    "price_vs_ma20_pct",
    "price_vs_ma200_pct",
    "rs_vnindex_20",
    "vol_ratio_20",
    "atr14_pct",
    "bb_percent_b",
    "pct_from_52w_high",
)


@dataclass
class Change:
    name: str
    previous: float | None
    current: float | None
    direction: str
    absolute_change: float | None = None
    percent_change: float | None = None

    @property
    def arrow(self) -> str:
        return {UP: "up", DOWN: "down", SAME: "flat", APPEARED: "new", DISAPPEARED: "gone"}[self.direction]

    def as_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "previous": self.previous,
            "current": self.current,
            "direction": self.direction,
            "absolute_change": None if self.absolute_change is None else round(self.absolute_change, 4),
            "percent_change": None if self.percent_change is None else round(self.percent_change, 2),
        }


@dataclass
class ChangeReport:
    ticker: str
    current_date: date | None = None
    previous_date: date | None = None
    changes: list[Change] = field(default_factory=list)
    note: str = ""

    @property
    def available(self) -> bool:
        return self.previous_date is not None

    def moved(self, minimum_pct: float = 0.0) -> list[Change]:
        """Changes worth printing: everything that actually moved."""
        return [
            change
            for change in self.changes
            if change.direction in (APPEARED, DISAPPEARED)
            or (
                # An unchanged feature is never "moved", whatever the floor is:
                # with a floor of zero, `abs(0) >= 0` would let every one
                # through and the section would be as long as the full report.
                change.direction != SAME
                and (change.percent_change is None or abs(change.percent_change) >= minimum_pct)
            )
        ]

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "current_date": self.current_date.isoformat() if self.current_date else None,
            "previous_date": self.previous_date.isoformat() if self.previous_date else None,
            "available": self.available,
            "note": self.note,
            "changes": [c.as_dict() for c in self.changes],
        }


def compare_values(name: str, previous: float | None, current: float | None) -> Change:
    """One feature across two readings."""
    if previous is None and current is None:
        return Change(name, None, None, SAME)
    if previous is None:
        return Change(name, None, current, APPEARED)
    if current is None:
        return Change(name, previous, None, DISAPPEARED)

    delta = current - previous
    if delta == 0:
        direction = SAME
    else:
        direction = UP if delta > 0 else DOWN

    percent = (delta / abs(previous) * 100.0) if previous not in (0, None) else None
    return Change(name, previous, current, direction, absolute_change=delta, percent_change=percent)


def compare_feature_sets(
    previous: Mapping[str, float | None],
    current: Mapping[str, float | None],
    names: Iterable[str] | None = None,
) -> list[Change]:
    keys = list(names) if names is not None else sorted(set(previous) | set(current))
    return [compare_values(key, previous.get(key), current.get(key)) for key in keys]


def previous_feature_date(ticker: str, before: date, path: Path | None = None) -> date | None:
    """The most recent stored session before `before`, or None."""
    with connect(path) as conn:
        row = conn.execute(
            """
            SELECT MAX(trade_date) AS d FROM technical_features
             WHERE ticker = ? AND trade_date < ?
            """,
            (ticker.upper(), before.isoformat()),
        ).fetchone()
    if not row or row["d"] is None:
        return None
    return date.fromisoformat(row["d"])


def what_changed(
    ticker: str,
    as_of: date,
    previous: date | None = None,
    names: Sequence[str] | None = HEADLINE_FEATURES,
    path: Path | None = None,
) -> ChangeReport:
    """Compare a session's stored features against the previous stored session."""
    report = ChangeReport(ticker=ticker.upper(), current_date=as_of)

    current_features = load_features(ticker, as_of, path=path)
    if not current_features:
        report.note = f"no stored features for {ticker.upper()} on {as_of}"
        return report

    earlier = previous or previous_feature_date(ticker, as_of, path=path)
    if earlier is None:
        # First analysis of this ticker. Saying so is the honest output; the
        # alternative is inventing a baseline of zero and reporting every
        # feature as a dramatic increase.
        report.note = "no earlier analysis to compare against"
        return report

    previous_features = load_features(ticker, earlier, path=path)
    if not previous_features:
        report.note = f"no stored features for {ticker.upper()} on {earlier}"
        return report

    report.previous_date = earlier
    report.changes = compare_feature_sets(previous_features, current_features, names)
    return report


def describe(change: Change, digits: int = 2) -> str:
    """One line a report can print."""
    if change.direction == APPEARED:
        return f"{change.name}: (none) -> {change.current:,.{digits}f} (new)"
    if change.direction == DISAPPEARED:
        return f"{change.name}: {change.previous:,.{digits}f} -> (none)"
    if change.direction == SAME:
        return f"{change.name}: {change.current:,.{digits}f} (unchanged)"
    marker = "up" if change.direction == UP else "down"
    return f"{change.name}: {change.previous:,.{digits}f} -> {change.current:,.{digits}f} ({marker})"
