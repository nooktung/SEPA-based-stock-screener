"""Market regime classifier.

Answers one question for the whole market rather than for a stock: what kind
of tape is this? The recommendation gates read the answer -- `thresholds.yaml`
blocks a BUY outright in BEAR and DISTRIBUTION, and scales position risk by
regime -- so this has to be legible and reproducible, not a black box.

It is a decision table over three axes, in deliberately that order:

    trend          where the index sits against its own 50 and 200 averages
    participation  how many stocks are actually moving with it
    momentum       what the index has done over 20 sessions

Participation is the axis that earns its place. An index can rise on four large
caps while most of the market falls, and the two cases look identical if you
only read the index level. That is what separates BULL from DISTRIBUTION here,
and it is why breadth is stored beside the index rather than derived from it.

Volatility and volume do not change the state. They move confidence: a call
made on thin volume at the top of the volatility range is the same call, held
less firmly.

Every input, and the reason for the verdict, is written to
`market_regime_history.inputs_json`. A classification that cannot be
reconstructed six months later is not evidence, it is an opinion with a
timestamp.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any, Mapping

from src.config import load_config
from src.database import connect, utcnow
from src.quant.store import load_flows, load_index
from src.quant.technical import TechnicalConfig, compute_features, drop_partial_session

log = logging.getLogger(__name__)

# The eight states, in order from most constructive to most hostile.
STRONG_BULL = "STRONG_BULL"
BULL = "BULL"
EARLY_BULL = "EARLY_BULL"
RECOVERY = "RECOVERY"
SIDEWAYS = "SIDEWAYS"
DISTRIBUTION = "DISTRIBUTION"
EARLY_BEAR = "EARLY_BEAR"
BEAR = "BEAR"

TREND_UP, TREND_DOWN, TREND_MIXED = "UP", "DOWN", "MIXED"
PARTICIPATION_BROAD, PARTICIPATION_NARROW, PARTICIPATION_WEAK = "BROAD", "NARROW", "WEAK"
MOMENTUM_POSITIVE, MOMENTUM_FLAT, MOMENTUM_NEGATIVE = "POSITIVE", "FLAT", "NEGATIVE"


@dataclass(frozen=True)
class RegimeConfig:
    breadth_bullish_ratio: float
    breadth_bearish_ratio: float
    volume_healthy_vs_ma20: float
    foreign_flow_lookback_sessions: int
    volatility_high_atr_percentile: float
    momentum_flat_band_pct: float
    base_confidence: float
    confidence_per_agreeing_input: float
    confidence_penalty_per_missing_input: float
    risk_multiplier_by_regime: dict[str, float]
    states: tuple[str, ...]

    @classmethod
    def from_thresholds(cls, thresholds: Mapping[str, Any] | None = None) -> "RegimeConfig":
        raw = dict(thresholds or load_config().thresholds["market_regime"])
        return cls(
            breadth_bullish_ratio=float(raw["breadth_bullish_ratio"]),
            breadth_bearish_ratio=float(raw["breadth_bearish_ratio"]),
            volume_healthy_vs_ma20=float(raw["volume_healthy_vs_ma20"]),
            foreign_flow_lookback_sessions=int(raw["foreign_flow_lookback_sessions"]),
            volatility_high_atr_percentile=float(raw["volatility_high_atr_percentile"]),
            momentum_flat_band_pct=float(raw["momentum_flat_band_pct"]),
            base_confidence=float(raw["base_confidence"]),
            confidence_per_agreeing_input=float(raw["confidence_per_agreeing_input"]),
            confidence_penalty_per_missing_input=float(raw["confidence_penalty_per_missing_input"]),
            risk_multiplier_by_regime=dict(raw.get("risk_multiplier_by_regime", {})),
            states=tuple(raw.get("states", ())),
        )


@dataclass
class RegimeInputs:
    """What the classifier reads. Any of it may be missing."""

    close: float | None = None
    ma50: float | None = None
    ma200: float | None = None
    roc_20d: float | None = None
    breadth_ratio: float | None = None
    volume_vs_ma20: float | None = None
    atr_percentile: float | None = None
    realized_vol_20d_pct: float | None = None
    foreign_net_20d: float | None = None

    # Inputs whose absence should reduce confidence. Volatility and volume are
    # not here: they only ever moved confidence, so their absence costs the
    # adjustment, not a penalty on top of it.
    REQUIRED = ("close", "ma50", "ma200", "roc_20d", "breadth_ratio")

    @property
    def missing(self) -> list[str]:
        return [name for name in self.REQUIRED if getattr(self, name) is None]

    def as_dict(self) -> dict[str, float | None]:
        return {
            "close": self.close,
            "ma50": self.ma50,
            "ma200": self.ma200,
            "roc_20d": self.roc_20d,
            "breadth_ratio": self.breadth_ratio,
            "volume_vs_ma20": self.volume_vs_ma20,
            "atr_percentile": self.atr_percentile,
            "realized_vol_20d_pct": self.realized_vol_20d_pct,
            "foreign_net_20d": self.foreign_net_20d,
        }


@dataclass
class RegimeAssessment:
    trade_date: date
    regime: str
    confidence: float
    index_name: str = "VNINDEX"
    trend: str | None = None
    participation: str | None = None
    momentum: str | None = None
    inputs: RegimeInputs = field(default_factory=RegimeInputs)
    reasons: list[str] = field(default_factory=list)
    risk_multiplier: float | None = None
    partial_session_excluded: bool = False

    @property
    def blocks_buying(self) -> bool:
        """Whether the recommendation gates forbid a BUY in this regime."""
        gates = load_config().thresholds.get("recommendation_gates", {}) or {}
        return self.regime in (gates.get("block_buy_in_regimes") or [])

    def to_json(self) -> str:
        return json.dumps(
            {
                "index": self.index_name,
                "trend": self.trend,
                "participation": self.participation,
                "momentum": self.momentum,
                "inputs": self.inputs.as_dict(),
                "missing": self.inputs.missing,
                "reasons": self.reasons,
            },
            sort_keys=True,
        )


# ---------------------------------------------------------------------------
# The axes
# ---------------------------------------------------------------------------

def classify_trend(close: float | None, ma50: float | None, ma200: float | None) -> str | None:
    """Where the index sits against its own averages.

    UP and DOWN both require the long average to agree with price. Price above
    a 200-day average that is still falling is not an uptrend, it is a rally
    inside a downtrend -- which is MIXED, and the states reachable from MIXED
    are the transitional ones.
    """
    if close is None or ma50 is None or ma200 is None:
        return None
    if close > ma200 and ma50 > ma200:
        return TREND_UP
    if close < ma200 and ma50 < ma200:
        return TREND_DOWN
    return TREND_MIXED


def classify_participation(breadth_ratio: float | None, config: RegimeConfig) -> str | None:
    if breadth_ratio is None:
        return None
    if breadth_ratio >= config.breadth_bullish_ratio:
        return PARTICIPATION_BROAD
    if breadth_ratio <= config.breadth_bearish_ratio:
        return PARTICIPATION_WEAK
    return PARTICIPATION_NARROW


def classify_momentum(roc_20d: float | None, config: RegimeConfig) -> str | None:
    if roc_20d is None:
        return None
    if roc_20d > config.momentum_flat_band_pct:
        return MOMENTUM_POSITIVE
    if roc_20d < -config.momentum_flat_band_pct:
        return MOMENTUM_NEGATIVE
    return MOMENTUM_FLAT


# ---------------------------------------------------------------------------
# The table
# ---------------------------------------------------------------------------

def classify(inputs: RegimeInputs, config: RegimeConfig, trade_date: date,
             index_name: str = "VNINDEX") -> RegimeAssessment:
    """Deterministic classification. Pure: no database, no clock."""
    trend = classify_trend(inputs.close, inputs.ma50, inputs.ma200)
    participation = classify_participation(inputs.breadth_ratio, config)
    momentum = classify_momentum(inputs.roc_20d, config)

    regime, reasons = _decide(trend, participation, momentum)
    confidence = _confidence(inputs, config, trend, participation, momentum, reasons)

    return RegimeAssessment(
        trade_date=trade_date,
        regime=regime,
        confidence=confidence,
        index_name=index_name,
        trend=trend,
        participation=participation,
        momentum=momentum,
        inputs=inputs,
        reasons=reasons,
        risk_multiplier=config.risk_multiplier_by_regime.get(regime),
    )


def _decide(trend: str | None, participation: str | None, momentum: str | None) -> tuple[str, list[str]]:
    """The decision table, with the reason recorded alongside the verdict."""
    if trend is None:
        # Without the index's own position there is no market read at all.
        # SIDEWAYS is the honest default: it asserts no direction.
        return SIDEWAYS, ["index trend unavailable; defaulting to no directional call"]

    reasons: list[str] = [f"trend {trend.lower()} against the 50 and 200 averages"]

    if trend == TREND_UP:
        if participation == PARTICIPATION_WEAK:
            # The signature case: index holding up while most stocks fall.
            reasons.append("index above its averages but most stocks are falling")
            return DISTRIBUTION, reasons
        if momentum == MOMENTUM_NEGATIVE:
            reasons.append("uptrend intact but the last 20 sessions have gone backwards")
            return DISTRIBUTION, reasons
        if participation == PARTICIPATION_BROAD and momentum == MOMENTUM_POSITIVE:
            reasons.append("broad participation and positive 20-session momentum")
            return STRONG_BULL, reasons
        reasons.append(f"participation {(participation or 'unknown').lower()}, "
                       f"momentum {(momentum or 'unknown').lower()}")
        return BULL, reasons

    if trend == TREND_DOWN:
        if momentum == MOMENTUM_POSITIVE and participation == PARTICIPATION_BROAD:
            reasons.append("downtrend, but the market is rising broadly off the lows")
            return RECOVERY, reasons
        if momentum == MOMENTUM_NEGATIVE:
            reasons.append("price and momentum both below trend")
            return BEAR, reasons
        reasons.append(f"downtrend with momentum {(momentum or 'unknown').lower()}")
        return EARLY_BEAR, reasons

    # MIXED: the averages and price disagree, which is where turns happen.
    if momentum == MOMENTUM_POSITIVE and participation in (PARTICIPATION_BROAD, PARTICIPATION_NARROW):
        reasons.append("averages still crossed, but momentum and participation are turning up")
        return EARLY_BULL, reasons
    if momentum == MOMENTUM_NEGATIVE and participation == PARTICIPATION_WEAK:
        reasons.append("averages crossed with momentum and breadth both deteriorating")
        return EARLY_BEAR, reasons
    reasons.append("no agreement between trend, momentum and participation")
    return SIDEWAYS, reasons


def _confidence(
    inputs: RegimeInputs,
    config: RegimeConfig,
    trend: str | None,
    participation: str | None,
    momentum: str | None,
    reasons: list[str],
) -> float:
    """How firmly the call should be held. Independent of the call itself."""
    confidence = config.base_confidence

    # Agreement between the two axes that can conflict.
    constructive = {TREND_UP: 1, TREND_DOWN: -1, TREND_MIXED: 0}.get(trend or "", 0)
    moving = {MOMENTUM_POSITIVE: 1, MOMENTUM_NEGATIVE: -1, MOMENTUM_FLAT: 0}.get(momentum or "", 0)
    broad = {PARTICIPATION_BROAD: 1, PARTICIPATION_WEAK: -1, PARTICIPATION_NARROW: 0}.get(participation or "", 0)

    for signal in (moving, broad):
        if constructive != 0 and signal == constructive:
            confidence += config.confidence_per_agreeing_input
        elif constructive != 0 and signal == -constructive:
            confidence -= config.confidence_per_agreeing_input

    confidence -= config.confidence_penalty_per_missing_input * len(inputs.missing)

    if inputs.atr_percentile is not None and inputs.atr_percentile >= config.volatility_high_atr_percentile:
        # A regime call made in the top decile of volatility has a shorter
        # half-life than the same call made in a quiet tape.
        confidence -= config.confidence_per_agreeing_input
        reasons.append(f"volatility in the top {100 - config.volatility_high_atr_percentile:.0f}% "
                       "of the past year; the call is less durable")

    if inputs.volume_vs_ma20 is not None and inputs.volume_vs_ma20 < config.volume_healthy_vs_ma20:
        confidence -= config.confidence_per_agreeing_input / 2
        reasons.append("index volume below its 20-session average")

    return round(max(0.0, min(100.0, confidence)), 1)


# ---------------------------------------------------------------------------
# Loading and persistence
# ---------------------------------------------------------------------------

def gather_inputs(
    index_name: str = "VNINDEX",
    as_of: date | None = None,
    include_partial_session: bool = False,
    now: datetime | None = None,
    path: Path | None = None,
) -> tuple[RegimeInputs, date | None, bool]:
    """Read everything the classifier needs out of the database.

    Returns (inputs, trade_date, partial_session_excluded).
    """
    frame = load_index(index_name, end=as_of, path=path)
    if frame.empty:
        return RegimeInputs(), None, False

    partial_dropped = False
    if not include_partial_session:
        # Same rule as the technical engine. A regime call built on a session
        # still in progress reads its part-day volume as a volume collapse and
        # its running index level as a close.
        frame, partial_dropped = drop_partial_session(frame, now=now)
        if frame.empty:
            return RegimeInputs(), None, partial_dropped

    trade_date = frame.index[-1].date()
    features = compute_features(frame, TechnicalConfig.from_thresholds(), index_name).features

    inputs = RegimeInputs(
        close=features.get("close"),
        ma50=features.get("ma50"),
        ma200=features.get("ma200"),
        roc_20d=features.get("roc_20d"),
        volume_vs_ma20=features.get("vol_ratio_20"),
        atr_percentile=features.get("atr_percentile_1y"),
        realized_vol_20d_pct=features.get("realized_vol_20d_pct"),
        breadth_ratio=_breadth_ratio(index_name, trade_date, path=path),
        foreign_net_20d=_market_foreign_net(load_config().thresholds["market_regime"], path=path),
    )
    return inputs, trade_date, partial_dropped


def _breadth_ratio(index_name: str, trade_date: date, path: Path | None = None) -> float | None:
    with connect(path) as conn:
        row = conn.execute(
            "SELECT advancers, decliners FROM index_daily WHERE symbol = ? AND trade_date = ?",
            (index_name.upper(), trade_date.isoformat()),
        ).fetchone()
    if not row or row["advancers"] is None or not row["decliners"]:
        return None
    return float(row["advancers"]) / float(row["decliners"])


def _market_foreign_net(regime_config: Mapping[str, Any], path: Path | None = None) -> float | None:
    """Foreign flow for the market as a whole, if such a record exists.

    Only a MARKET-scope row counts. Summing the watchlist would describe the
    watchlist -- five brokers -- and calling that "the market" is the kind of
    substitution that makes a regime call quietly wrong.
    """
    frame = load_flows("HOSE", scope="MARKET", path=path)
    if frame.empty:
        return None
    window = int(regime_config.get("foreign_flow_lookback_sessions", 20))
    net = frame["foreign_net_value"].astype("float64")
    if net.notna().sum() < window:
        return None
    return float(net.iloc[-window:].sum())


def assess_regime(
    index_name: str = "VNINDEX",
    as_of: date | None = None,
    include_partial_session: bool = False,
    now: datetime | None = None,
    path: Path | None = None,
) -> RegimeAssessment:
    inputs, trade_date, partial_dropped = gather_inputs(
        index_name=index_name,
        as_of=as_of,
        include_partial_session=include_partial_session,
        now=now,
        path=path,
    )
    if trade_date is None:
        raise ValueError(
            f"no settled {index_name} session in index_daily"
            f"{' up to ' + as_of.isoformat() if as_of else ''}. Run `collect` first."
        )
    assessment = classify(inputs, RegimeConfig.from_thresholds(), trade_date, index_name=index_name)
    assessment.partial_session_excluded = partial_dropped
    if partial_dropped:
        assessment.reasons.append("today's unfinished session excluded from the read")
    return assessment


def store_regime(assessment: RegimeAssessment, path: Path | None = None) -> int:
    """Write the assessment, keeping the full input vector for later audit."""
    with connect(path) as conn:
        conn.execute(
            """
            INSERT INTO market_regime_history
                (trade_date, regime, confidence, breadth_ratio, volume_vs_ma20,
                 foreign_net_20d, volatility_pct, inputs_json, computed_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(trade_date) DO UPDATE SET
                regime=excluded.regime,
                confidence=excluded.confidence,
                breadth_ratio=excluded.breadth_ratio,
                volume_vs_ma20=excluded.volume_vs_ma20,
                foreign_net_20d=excluded.foreign_net_20d,
                volatility_pct=excluded.volatility_pct,
                inputs_json=excluded.inputs_json,
                computed_at=excluded.computed_at
            """,
            (
                assessment.trade_date.isoformat(),
                assessment.regime,
                assessment.confidence,
                assessment.inputs.breadth_ratio,
                assessment.inputs.volume_vs_ma20,
                assessment.inputs.foreign_net_20d,
                assessment.inputs.realized_vol_20d_pct,
                assessment.to_json(),
                utcnow(),
            ),
        )
    return 1
