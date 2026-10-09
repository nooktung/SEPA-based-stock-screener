"""Conflict detection (§28).

The point of this module is to refuse to average disagreement away.

A stock can have a strong trend, strong momentum, expanding volume -- and sit
directly under a level it has failed at twice, with a trap score of 80. Rolled
into a single number those cancel, and the number that comes out looks calm.
The disagreement was the most useful thing in the data and the averaging
destroyed it.

So each conflict is reported as a structured object naming the bullish evidence
on one side, the bearish evidence on the other, and a severity chosen by rule.
Nothing here resolves anything. Resolution is a decision, and decisions belong
to a layer that does not exist yet.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from src.config import load_config
from src.quant.behaviour import BehaviourRead, DISTRIBUTION_LIKE
from src.quant.divergence import BEARISH, BULLISH, Divergence
from src.quant.levels import SIDE_RESISTANCE, SIDE_SUPPORT, Zone, nearest_zone
from src.quant.patterns import BEARISH_STRUCTURE, BULLISH_STRUCTURE, StructureRead, TrapRead

log = logging.getLogger(__name__)

SEVERITY_LOW = "LOW"
SEVERITY_MEDIUM = "MEDIUM"
SEVERITY_HIGH = "HIGH"
SEVERITY_CRITICAL = "CRITICAL"

_SEVERITY_ORDER = {SEVERITY_LOW: 0, SEVERITY_MEDIUM: 1, SEVERITY_HIGH: 2, SEVERITY_CRITICAL: 3}


@dataclass(frozen=True)
class ConflictConfig:
    trap_risk_high: float
    rsi_extended: float
    rsi_oversold: float
    near_resistance_pct: float
    near_support_pct: float
    volume_contraction_ratio: float

    @classmethod
    def from_thresholds(cls, thresholds: Mapping[str, Any] | None = None) -> "ConflictConfig":
        raw = dict((thresholds or load_config().thresholds["patterns"])["conflicts"])
        return cls(
            trap_risk_high=float(raw["trap_risk_high"]),
            rsi_extended=float(raw["rsi_extended"]),
            rsi_oversold=float(raw["rsi_oversold"]),
            near_resistance_pct=float(raw["near_resistance_pct"]),
            near_support_pct=float(raw["near_support_pct"]),
            volume_contraction_ratio=float(raw["volume_contraction_ratio"]),
        )


@dataclass
class Conflict:
    conflict_type: str
    severity: str
    bullish_evidence: list[str] = field(default_factory=list)
    bearish_evidence: list[str] = field(default_factory=list)
    explanation: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "conflict_type": self.conflict_type,
            "severity": self.severity,
            "bullish_evidence": list(self.bullish_evidence),
            "bearish_evidence": list(self.bearish_evidence),
            "explanation": self.explanation,
        }


@dataclass
class EvidenceBundle:
    """Everything the detector compares. Any part may be absent."""

    features: dict[str, float | None] = field(default_factory=dict)
    structure: StructureRead | None = None
    bull_trap: TrapRead | None = None
    zones: list[Zone] = field(default_factory=list)
    behaviour: BehaviourRead | None = None
    divergences: list[Divergence] = field(default_factory=list)
    regime: str | None = None
    regime_blocks_buying: bool = False
    foreign_net_5d: float | None = None

    def feature(self, name: str) -> float | None:
        return self.features.get(name)


def detect_conflicts(bundle: EvidenceBundle, config: ConflictConfig | None = None) -> list[Conflict]:
    """Structured disagreements between independent lines of evidence."""
    settings = config or ConflictConfig.from_thresholds()
    conflicts: list[Conflict] = []

    price = bundle.feature("close")
    rsi = bundle.feature("rsi14")
    macd_delta = bundle.feature("macd_hist_delta")
    volume_ratio = bundle.feature("vol_ratio_20")
    roc_20 = bundle.feature("roc_20d")
    above_ma20 = (bundle.feature("price_vs_ma20_pct") or 0) > 0
    structure_bullish = bundle.structure is not None and bundle.structure.structure == BULLISH_STRUCTURE
    structure_bearish = bundle.structure is not None and bundle.structure.structure == BEARISH_STRUCTURE

    resistance = nearest_zone(bundle.zones, SIDE_RESISTANCE)
    support = nearest_zone(bundle.zones, SIDE_SUPPORT)
    near_resistance = (
        resistance is not None
        and resistance.distance_from_price is not None
        and 0 <= resistance.distance_from_price <= settings.near_resistance_pct
    )

    # 1. Strength running into a level that has turned price away before.
    if (structure_bullish or above_ma20) and near_resistance:
        severity = SEVERITY_MEDIUM
        if bundle.bull_trap and bundle.bull_trap.score >= settings.trap_risk_high:
            severity = SEVERITY_HIGH
        if rsi is not None and rsi >= settings.rsi_extended and severity == SEVERITY_HIGH:
            severity = SEVERITY_CRITICAL
        conflicts.append(
            Conflict(
                conflict_type="TREND_VS_RESISTANCE",
                severity=severity,
                bullish_evidence=_trend_evidence(bundle, structure_bullish, above_ma20),
                bearish_evidence=[
                    f"resistance zone {resistance.zone_low:,.0f}-{resistance.zone_high:,.0f} "
                    f"is {resistance.distance_from_price:.1f}% away, tested {resistance.touch_count}x"
                ]
                + ([f"bull trap score {bundle.bull_trap.score:.0f}"] if bundle.bull_trap and bundle.bull_trap.score else []),
                explanation=(
                    "Trend and location disagree: the move is intact but price is arriving "
                    "at a level it has been turned away from before, which is where entries "
                    "have the worst risk/reward regardless of how strong the trend looks."
                ),
            )
        )

    # 2. Price advancing while participation in that advance shrinks.
    if (roc_20 or 0) > 0 and volume_ratio is not None and volume_ratio < settings.volume_contraction_ratio:
        conflicts.append(
            Conflict(
                conflict_type="PRICE_VS_VOLUME",
                severity=SEVERITY_MEDIUM,
                bullish_evidence=[f"20-session return {roc_20:+.1f}%"],
                bearish_evidence=[f"volume {volume_ratio:.2f}x its 20-session average"],
                explanation=(
                    "Price is rising on contracting volume. The advance is real but fewer "
                    "participants are behind each step of it."
                ),
            )
        )

    # 3. Trend still up, momentum already rolling over.
    if structure_bullish and macd_delta is not None and macd_delta < 0:
        conflicts.append(
            Conflict(
                conflict_type="TREND_VS_MOMENTUM",
                severity=SEVERITY_MEDIUM,
                bullish_evidence=["price structure is making higher highs and higher lows"],
                bearish_evidence=[f"MACD histogram falling ({macd_delta:+.1f} on the session)"],
                explanation=(
                    "The structure has not broken but the force behind it is fading. This "
                    "resolves either way and is worth watching rather than acting on."
                ),
            )
        )

    # 4. An oscillator disagreeing with the price it is measuring.
    for divergence in bundle.divergences:
        if divergence.kind == BEARISH and (structure_bullish or above_ma20):
            conflicts.append(
                Conflict(
                    conflict_type="PRICE_VS_OSCILLATOR",
                    severity=SEVERITY_MEDIUM if divergence.strength < 50 else SEVERITY_HIGH,
                    bullish_evidence=["price is making higher highs"],
                    bearish_evidence=[
                        f"bearish {divergence.indicator} divergence "
                        f"({divergence.pivot_1_date} to {divergence.pivot_2_date}), strength {divergence.strength:.0f}"
                    ],
                    explanation=(
                        "Price made a higher high while the oscillator made a lower high. "
                        "The new high was achieved with less force than the previous one."
                    ),
                )
            )
        elif divergence.kind == BULLISH and structure_bearish:
            conflicts.append(
                Conflict(
                    conflict_type="PRICE_VS_OSCILLATOR",
                    severity=SEVERITY_LOW,
                    bullish_evidence=[
                        f"bullish {divergence.indicator} divergence, strength {divergence.strength:.0f}"
                    ],
                    bearish_evidence=["price structure is still making lower highs and lower lows"],
                    explanation=(
                        "Selling pressure is easing while the downtrend is technically intact. "
                        "A first sign, not a turn."
                    ),
                )
            )

    # 5. The stock and the market pointing different ways.
    if (structure_bullish or above_ma20) and bundle.regime_blocks_buying:
        conflicts.append(
            Conflict(
                conflict_type="STOCK_VS_MARKET",
                severity=SEVERITY_HIGH,
                bullish_evidence=_trend_evidence(bundle, structure_bullish, above_ma20),
                bearish_evidence=[f"market regime is {bundle.regime}"],
                explanation=(
                    "The stock looks constructive in a market regime the gates forbid buying "
                    "into. Individual strength in a hostile tape resolves against the stock "
                    "more often than not."
                ),
            )
        )

    # 6. Behaviour disagreeing with trend.
    if structure_bullish and bundle.behaviour and bundle.behaviour.verdict == DISTRIBUTION_LIKE:
        conflicts.append(
            Conflict(
                conflict_type="TREND_VS_BEHAVIOUR",
                severity=SEVERITY_HIGH,
                bullish_evidence=["price structure is bullish"],
                bearish_evidence=[
                    f"distribution-like score {bundle.behaviour.distribution_like_score:.0f} "
                    f"vs accumulation-like {bundle.behaviour.accumulation_like_score:.0f}"
                ],
                explanation=(
                    "Structure is rising but the session-by-session behaviour -- where closes "
                    "land in their range, which side carries the volume -- resembles supply "
                    "being distributed into strength."
                ),
            )
        )

    # 7. Buying that is not moving the price. §9 calls this out explicitly.
    if bundle.foreign_net_5d is not None and bundle.foreign_net_5d > 0 and (roc_20 is not None and roc_20 <= 0):
        conflicts.append(
            Conflict(
                conflict_type="FLOW_VS_PRICE",
                severity=SEVERITY_MEDIUM,
                bullish_evidence=[f"foreign investors net bought {bundle.foreign_net_5d / 1e9:,.0f}bn over 5 sessions"],
                bearish_evidence=[f"price is {roc_20:+.1f}% over 20 sessions"],
                explanation=(
                    "Sustained buying that does not lift the price means supply is meeting it. "
                    "Net foreign buying is not automatically bullish; what price did with it is "
                    "the information."
                ),
            )
        )

    conflicts.sort(key=lambda c: -_SEVERITY_ORDER[c.severity])
    return conflicts


def _trend_evidence(bundle: EvidenceBundle, structure_bullish: bool, above_ma20: bool) -> list[str]:
    evidence: list[str] = []
    if structure_bullish:
        evidence.append("price structure is making higher highs and higher lows")
    if above_ma20:
        evidence.append(f"price {bundle.feature('price_vs_ma20_pct'):+.1f}% above its 20-session average")
    rsi = bundle.feature("rsi14")
    if rsi is not None and rsi > 55:
        evidence.append(f"RSI {rsi:.0f}")
    return evidence


def highest_severity(conflicts: Sequence[Conflict]) -> str | None:
    if not conflicts:
        return None
    return max(conflicts, key=lambda c: _SEVERITY_ORDER[c.severity]).severity
