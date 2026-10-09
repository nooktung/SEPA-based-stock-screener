"""One ticker, everything the deterministic layer knows about it.

Assembles the separate engines -- structure, breakouts, traps, zones,
divergence, behaviour, conflicts, changes -- into a single read for one
session. It computes nothing itself; it is the seam where the pieces meet, and
keeping that seam thin is what lets each engine stay independently testable.

`as_of` flows through every call. Asked about a date six months ago, the result
is what would have been on the screen that afternoon.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from src.config import load_config
from src.quant import indicators as ind
from src.quant.behaviour import BehaviourConfig, BehaviourRead, compute_behaviour
from src.quant.changes import ChangeReport, what_changed
from src.quant.conflicts import Conflict, ConflictConfig, EvidenceBundle, detect_conflicts
from src.quant.divergence import Divergence, latest_divergences
from src.quant.events import BREAKDOWN, BREAKOUT, ShakeoutConfig, find_shakeouts, track_break_events
from src.quant.flow import compute_for_ticker as flow_for_ticker
from src.quant.levels import Zone, build_zones, price_location
from src.quant.patterns import (
    BreakoutRead,
    PatternConfig,
    StructureRead,
    TrapRead,
    bear_trap,
    bull_trap,
    read_breakout,
    read_structure,
)
from src.quant.regime import assess_regime
from src.quant.store import load_index, load_prices, price_series
from src.quant.technical import TechnicalConfig, compute_for_ticker, drop_partial_session

log = logging.getLogger(__name__)


@dataclass
class TickerAnalysis:
    ticker: str
    trade_date: date | None = None
    features: dict[str, float | None] = field(default_factory=dict)
    structure: StructureRead | None = None
    breakouts: dict[int, BreakoutRead] = field(default_factory=dict)
    bull_trap: TrapRead | None = None
    bear_trap: TrapRead | None = None
    zones: list[Zone] = field(default_factory=list)
    location: str = "UNKNOWN"
    divergences: list[Divergence] = field(default_factory=list)
    behaviour: BehaviourRead | None = None
    shakeouts: list[Any] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    changes: ChangeReport | None = None
    regime: str | None = None
    regime_blocks_buying: bool = False
    foreign_net_5d: float | None = None
    partial_session_excluded: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "ticker": self.ticker,
            "trade_date": self.trade_date.isoformat() if self.trade_date else None,
            "structure": {
                "structure": self.structure.structure if self.structure else None,
                "sequence": self.structure.swing_sequence if self.structure else [],
                "break_of_structure": self.structure.break_of_structure if self.structure else None,
                "change_of_character": self.structure.change_of_character if self.structure else None,
            },
            "breakouts": {w: {"state": r.state, "level": r.level, "distance_pct": r.distance_pct}
                          for w, r in self.breakouts.items()},
            "bull_trap": {"score": self.bull_trap.score, "status": self.bull_trap.status}
                         if self.bull_trap else None,
            "bear_trap": {"score": self.bear_trap.score, "status": self.bear_trap.status}
                         if self.bear_trap else None,
            "zones": [z.as_dict() for z in self.zones],
            "location": self.location,
            "divergences": [d.as_dict() for d in self.divergences],
            "behaviour": self.behaviour.as_dict() if self.behaviour else None,
            "conflicts": [c.as_dict() for c in self.conflicts],
            "regime": self.regime,
            "regime_blocks_buying": self.regime_blocks_buying,
            "foreign_net_5d": self.foreign_net_5d,
        }


def analyse(
    ticker: str,
    as_of: date | None = None,
    include_partial_session: bool = False,
    now: datetime | None = None,
    path: Path | None = None,
) -> TickerAnalysis:
    """Everything knowable about one ticker on one session."""
    analysis = TickerAnalysis(ticker=ticker.upper())
    patterns = PatternConfig.from_thresholds()

    prices = load_prices(ticker, end=as_of, path=path)
    if prices.empty:
        raise ValueError(f"{ticker.upper()}: no bars in prices_daily. Run `collect` first.")

    if not include_partial_session:
        prices, analysis.partial_session_excluded = drop_partial_session(prices, now=now)
        if prices.empty:
            raise ValueError(f"{ticker.upper()}: only an unfinished session is available.")

    session = prices.index[-1].date()
    analysis.trade_date = session

    vector = compute_for_ticker(
        ticker, as_of=session, include_partial_session=True, now=now, path=path
    )
    analysis.features = vector.features

    analysis.structure = read_structure(prices, patterns, as_of=session)
    for window in (20, 60):
        analysis.breakouts[window] = read_breakout(prices, window, patterns)

    volume_ratio = analysis.features.get("vol_ratio_20")
    analysis.bull_trap = bull_trap(prices, patterns, window=60, volume_ratio=volume_ratio)
    analysis.bear_trap = bear_trap(prices, patterns, window=60, volume_ratio=volume_ratio)

    analysis.zones = build_zones(prices, as_of=session, pattern_config=patterns)
    analysis.location = price_location(analysis.zones, float(price_series(prices).iloc[-1]))

    analysis.divergences = latest_divergences(prices, as_of=session, within_sessions=20,
                                              pattern_config=patterns)

    volume = prices["volume"].astype("float64")
    ratios = (volume / ind.sma(volume, 20)).where(ind.sma(volume, 20) > 0)
    analysis.shakeouts = [
        s for s in find_shakeouts(prices, as_of=session, pattern_config=patterns, volume_ratios=ratios)
        if (session - s.setup_date).days <= 10
    ]

    failed_up = any(
        e.failure_date and (session - e.failure_date).days <= 20
        for e in track_break_events(prices, 60, BREAKOUT, as_of=session, config=patterns)
    )
    failed_down = any(
        e.failure_date and (session - e.failure_date).days <= 20
        for e in track_break_events(prices, 60, BREAKDOWN, as_of=session, config=patterns)
    )
    analysis.behaviour = compute_behaviour(
        prices,
        zones=analysis.zones,
        relative_strength=analysis.features.get("rs_vnindex_20"),
        failed_breakdown=failed_down,
        failed_breakout=failed_up,
        config=BehaviourConfig.from_thresholds(),
    )

    try:
        flow = flow_for_ticker(ticker, as_of=session, now=now, path=path)
        analysis.foreign_net_5d = flow.features.get("foreign_net_5d")
    except ValueError:
        analysis.foreign_net_5d = None

    try:
        assessment = assess_regime(as_of=session, now=now, path=path)
        analysis.regime = assessment.regime
        analysis.regime_blocks_buying = assessment.blocks_buying
    except ValueError:
        analysis.regime = None

    analysis.conflicts = detect_conflicts(
        EvidenceBundle(
            features=analysis.features,
            structure=analysis.structure,
            bull_trap=analysis.bull_trap,
            zones=analysis.zones,
            behaviour=analysis.behaviour,
            divergences=analysis.divergences,
            regime=analysis.regime,
            regime_blocks_buying=analysis.regime_blocks_buying,
            foreign_net_5d=analysis.foreign_net_5d,
        ),
        ConflictConfig.from_thresholds(),
    )

    analysis.changes = what_changed(ticker, session, path=path)
    return analysis
