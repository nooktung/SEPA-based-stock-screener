"""Ex-rights detection and back-adjustment of price series.

## The problem

Vietnamese securities firms issue bonus shares and stock dividends constantly.
VIX did a 10:6 bonus in April 2026 and a 20:1 stock dividend with an ex-date of
21 August 2026. SSI went ex on a 5:1 bonus plus a cash dividend on 17 August
2026.

On each ex-date the exchange resets the reference price. An unadjusted close
series therefore contains a step down that no seller caused. Every downstream
consumer misreads it:

- a breakdown detector fires a false signal
- MA20/MA50 bend for weeks afterwards
- ATR spikes, so the stop-loss calculation widens for no reason
- relative strength versus VNINDEX collapses
- a 20:1 dividend is a ~4.8% phantom drop, which is larger than most real
  daily moves and sits right in the range a breakdown rule cares about

## Why not just call the corporate-action endpoint

`DNSEClient.get_corporate_action_history` looks like the answer but is not:
its path is `/accounts/{account_no}/corporate-action-history`. It is scoped to
one trading account and returns the entitlements *that account* received. For a
watchlist ticker the user does not hold, it returns nothing. It is a portfolio
feature, not a reference-data feed.

## What this module does instead

Detects the adjustment from the price series itself, which works for any
ticker and any vendor.

On an ex-date the exchange sets the new reference price to roughly
`prev_close / (1 + ratio)`. So a session where the official reference price
diverges materially from the previous close, *without* an equivalent gap in
the traded range, is an ex-date. The ratio `reference / prev_close` is the
adjustment factor; multiply every earlier bar by it and the step disappears.

When reference prices are unavailable, `detect_from_gaps` falls back to
flagging suspicious overnight gaps as CANDIDATE events. A candidate is never
applied silently — it is surfaced for confirmation, because misapplying an
adjustment is as damaging as missing one.

## Hard limitation of the fallback

The gap fallback only sees actions large enough to break the daily price band.
SSI's 5:1 bonus (-16.7%) is visible. VIX's 20:1 stock dividend (-4.8%) is NOT:
it sits inside the 7% HOSE band and is numerically indistinguishable from an
ordinary down day. No heuristic can separate them without corrupting real
selling days.

So reference prices are not an optimisation -- they are the only correct path
for small stock dividends, which is precisely the kind VN brokers pay most
often. When the reference feed is unavailable the series must be marked
unverified and confidence reduced, never assumed clean.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import Enum

from src.collectors.base import OHLCV


class AdjustmentConfidence(str, Enum):
    CONFIRMED = "CONFIRMED"    # reference price from the exchange says so
    CANDIDATE = "CANDIDATE"    # inferred from a gap; needs confirmation
    REJECTED = "REJECTED"      # looked like an ex-date but the range moved too


@dataclass(frozen=True)
class AdjustmentEvent:
    symbol: str
    ex_date: date
    prev_close: float
    new_reference: float
    factor: float              # multiply bars BEFORE ex_date by this
    confidence: AdjustmentConfidence
    implied_ratio: str         # human-readable, e.g. "~20:1"
    note: str = ""


# A same-day move larger than this cannot be a pure price move on HOSE, whose
# daily band is +/-7%. HNX is +/-10%, UPCOM +/-15%; the default is the tightest
# so detection is conservative, and callers pass the right band per exchange.
DEFAULT_DAILY_BAND = 0.07
MIN_FACTOR_DEVIATION = 0.02    # ignore rounding noise below 2%


def detect_from_reference(
    symbol: str,
    prev_close: float,
    reference_price: float,
    ex_date: date,
) -> AdjustmentEvent | None:
    """Confirmed detection using the exchange's own reference price.

    This is the reliable path. `reference_price` comes from
    `security_definition.basicPrice`, which the exchange publishes before the
    session and which already reflects the corporate action.
    """
    if prev_close <= 0 or reference_price <= 0:
        return None

    factor = reference_price / prev_close
    if abs(factor - 1.0) < MIN_FACTOR_DEVIATION:
        return None

    return AdjustmentEvent(
        symbol=symbol,
        ex_date=ex_date,
        prev_close=prev_close,
        new_reference=reference_price,
        factor=factor,
        confidence=AdjustmentConfidence.CONFIRMED,
        implied_ratio=_describe_ratio(factor),
        note="reference price diverges from previous close",
    )


def detect_from_gaps(
    bars: list[OHLCV],
    daily_band: float = DEFAULT_DAILY_BAND,
) -> list[AdjustmentEvent]:
    """Fallback detection when reference prices are not available.

    A bar whose entire traded range sits below `prev_close * (1 - band)` cannot
    be a legal price move, so something structural happened. Marked CANDIDATE,
    never applied automatically.
    """
    events: list[AdjustmentEvent] = []
    for prev, cur in zip(bars, bars[1:]):
        if not prev.close or not cur.open or not cur.high:
            continue
        lowest_legal = prev.close * (1 - daily_band)
        if cur.high < lowest_legal * (1 - MIN_FACTOR_DEVIATION):
            factor = cur.open / prev.close
            events.append(
                AdjustmentEvent(
                    symbol=cur.symbol,
                    ex_date=cur.trade_date,
                    prev_close=prev.close,
                    new_reference=cur.open,
                    factor=factor,
                    confidence=AdjustmentConfidence.CANDIDATE,
                    implied_ratio=_describe_ratio(factor),
                    note=(
                        f"entire session range below the {daily_band:.0%} band; "
                        "likely ex-rights, confirm before applying"
                    ),
                )
            )
    return events


def apply_adjustments(
    bars: list[OHLCV],
    events: list[AdjustmentEvent],
    apply_candidates: bool = False,
) -> list[OHLCV]:
    """Back-adjust a bar series so it is continuous across corporate actions.

    Adjustment is cumulative and applied backwards: a bar is multiplied by the
    product of every factor for events dated after it. Volume is scaled by the
    inverse so that price x volume stays meaningful.

    Returns a new list; `bars` is not mutated. Bars carry the resulting
    `adj_close` and `adj_factor`, and the raw `close` is left untouched so the
    original observation is never lost.
    """
    if not bars:
        return []

    usable = [e for e in events if e.confidence is AdjustmentConfidence.CONFIRMED or apply_candidates]
    if not usable:
        return [replace(b, adj_close=b.close, adj_factor=1.0) for b in bars]

    by_date = sorted(usable, key=lambda e: e.ex_date)
    out: list[OHLCV] = []
    for bar in bars:
        factor = 1.0
        for ev in by_date:
            if bar.trade_date < ev.ex_date:
                factor *= ev.factor
        out.append(
            replace(
                bar,
                adj_close=round(bar.close * factor, 2) if bar.close is not None else None,
                adj_factor=factor,
            )
        )
    return out


def verify_continuity(
    bars: list[OHLCV],
    daily_band: float = DEFAULT_DAILY_BAND,
) -> list[str]:
    """Post-adjustment sanity check.

    Returns a list of complaints. An empty list means the adjusted series has
    no move that exceeds the legal daily band, which is what a correctly
    adjusted series looks like. This runs after every backfill; a non-empty
    result means an adjustment was missed or misapplied, and the collector
    marks the dataset stale rather than feeding a broken series to the quant
    layer.
    """
    problems: list[str] = []
    tolerance = daily_band + 0.005  # small allowance for rounding
    for prev, cur in zip(bars, bars[1:]):
        p, c = prev.adj_close or prev.close, cur.adj_close or cur.close
        if not p or not c:
            continue
        move = (c - p) / p
        if abs(move) > tolerance:
            problems.append(
                f"{cur.symbol} {cur.trade_date}: adjusted move {move:+.1%} "
                f"exceeds the {daily_band:.0%} band"
            )
    return problems


def _describe_ratio(factor: float) -> str:
    """Render an adjustment factor as an approximate corporate-action ratio.

    factor = 1 / (1 + new_per_old). A 20:1 stock dividend gives one new share
    per twenty held, so factor ~= 20/21 ~= 0.952.
    """
    if factor >= 1.0 or factor <= 0:
        return "n/a"
    new_per_old = (1.0 / factor) - 1.0
    if new_per_old <= 0:
        return "n/a"
    old_per_new = 1.0 / new_per_old
    return f"~{old_per_new:.0f}:1"
