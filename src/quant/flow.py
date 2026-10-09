"""Capital flow features.

Foreign investors are a minority of Vietnamese market turnover but their flow
is watched closely, partly because it is one of the few positions that can be
observed daily and partly because sustained selling into a rising index has
historically preceded a stall. What matters is rarely one session -- it is the
run, the size relative to what the stock normally trades, and whether the
direction just changed.

So the features here are about persistence and scale rather than a single day:
a streak length, a 5- and 20-session net, and the net expressed as a share of
the stock's own turnover. A net of 40 billion dong means nothing until you know
whether the stock trades 40 billion or 400 billion in a day.

Nothing here is persisted. There is no flow_features table, and reusing
`technical_features` for something that is not technical would make the schema
lie. These are computed when a report is built, from the raw daily records that
*are* stored.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pandas as pd

from src.market_calendar import session_is_complete
from src.quant.store import load_flows, load_prices, price_series

log = logging.getLogger(__name__)

# Windows the flow read is built on. Short enough to still be a signal about
# now, long enough that one unusual session does not dominate.
SHORT_WINDOW = 5
LONG_WINDOW = 20


@dataclass
class FlowVector:
    ticker: str
    trade_date: date | None = None
    features: dict[str, float | None] = field(default_factory=dict)
    sessions_available: int = 0
    partial_session_excluded: bool = False

    @property
    def completeness(self) -> float:
        if not self.features:
            return 0.0
        return sum(1 for value in self.features.values() if value is not None) / len(self.features)

    def compact(self, digits: int = 4) -> dict[str, float]:
        return {
            name: round(value, digits)
            for name, value in self.features.items()
            if value is not None
        }


def net_streak(net_values: pd.Series) -> int | None:
    """Consecutive sessions on the same side, signed.

    +3 means three straight sessions of net buying, -2 two of net selling. A
    zero-net session ends the run rather than extending it: no position changed
    hands on balance, so nothing was continued.
    """
    values = net_values.dropna()
    if values.empty:
        return None

    last = float(values.iloc[-1])
    if last == 0:
        return 0
    direction = 1 if last > 0 else -1

    streak = 0
    for value in reversed(values.tolist()):
        if (value > 0 and direction > 0) or (value < 0 and direction < 0):
            streak += 1
        else:
            break
    return streak * direction


def compute_flow_features(
    flows: pd.DataFrame,
    prices: pd.DataFrame | None = None,
    ticker: str = "",
) -> FlowVector:
    """Build the flow feature set from stored daily records.

    Pure: no database, no clock. `prices` is optional and only used to express
    flow as a share of the stock's own turnover.
    """
    vector = FlowVector(ticker=ticker.upper())
    if flows.empty:
        return vector

    net = flows["foreign_net_value"].astype("float64")
    buy = flows["foreign_buy_value"].astype("float64")
    sell = flows["foreign_sell_value"].astype("float64")

    vector.trade_date = flows.index[-1].date()
    vector.sessions_available = len(flows)
    out: dict[str, float | None] = {}

    out["foreign_net_1d"] = _last(net)
    for window in (SHORT_WINDOW, LONG_WINDOW):
        # A window with fewer sessions than asked for is not that window. Summing
        # 6 sessions and calling it a 20-session net understates every comparison
        # made against it later.
        out[f"foreign_net_{window}d"] = (
            float(net.iloc[-window:].sum()) if net.notna().sum() >= window else None
        )

    out["foreign_streak_days"] = _as_float(net_streak(net))

    window_buy = buy.iloc[-LONG_WINDOW:].sum() if buy.notna().sum() >= LONG_WINDOW else None
    window_sell = sell.iloc[-LONG_WINDOW:].sum() if sell.notna().sum() >= LONG_WINDOW else None
    if window_buy is None or window_sell is None or (window_buy + window_sell) == 0:
        out[f"foreign_buy_share_{LONG_WINDOW}d"] = None
    else:
        # 0.5 is balanced, above is net accumulation. Kept as a share rather
        # than a buy/sell ratio so the number stays bounded and comparable.
        out[f"foreign_buy_share_{LONG_WINDOW}d"] = float(window_buy / (window_buy + window_sell))

    out[f"foreign_net_pct_turnover_{LONG_WINDOW}d"] = _net_against_turnover(net, prices)

    vector.features = out
    return vector


def _net_against_turnover(net: pd.Series, prices: pd.DataFrame | None) -> float | None:
    """Net foreign flow as a percentage of the stock's own traded value.

    Turnover is estimated as close x volume -- the OHLC feed carries no matched
    value. That makes this a ratio of a measured numerator to an estimated
    denominator, good for ranking and for order of magnitude, not for a precise
    claim about ownership change.
    """
    if prices is None or prices.empty or net.notna().sum() < LONG_WINDOW:
        return None

    turnover = price_series(prices) * prices["volume"].astype("float64")
    aligned = turnover.reindex(net.index).iloc[-LONG_WINDOW:]
    if aligned.isna().any():
        return None

    total = float(aligned.sum())
    if total <= 0:
        return None
    return float(net.iloc[-LONG_WINDOW:].sum() / total * 100.0)


def compute_for_ticker(
    ticker: str,
    as_of: date | None = None,
    include_partial_session: bool = False,
    now: datetime | None = None,
    path: Path | None = None,
) -> FlowVector:
    """Load stored flow (and prices, for scale) and build the vector."""
    flows = load_flows(ticker, end=as_of, path=path)
    if flows.empty:
        raise ValueError(
            f"{ticker.upper()}: no rows in flow_daily"
            f"{' up to ' + as_of.isoformat() if as_of else ''}. Run `collect` first."
        )

    partial_dropped = False
    if not include_partial_session:
        last = flows.index[-1].date()
        if not session_is_complete(last, now=now):
            # Today's flow record is a running total that will keep moving
            # until the close. Including it would report a fraction of the
            # day's flow as the day's flow.
            flows = flows.iloc[:-1]
            partial_dropped = True
        if flows.empty:
            raise ValueError(
                f"{ticker.upper()}: the only flow record available is an unfinished session."
            )

    prices = load_prices(ticker, end=flows.index[-1].date(), path=path)
    vector = compute_flow_features(flows, prices=prices, ticker=ticker)
    vector.partial_session_excluded = partial_dropped
    return vector


def _last(series: pd.Series) -> float | None:
    if series.empty or pd.isna(series.iloc[-1]):
        return None
    return float(series.iloc[-1])


def _as_float(value: int | float | None) -> float | None:
    return None if value is None else float(value)
