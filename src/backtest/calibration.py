"""Score calibration.

A bull-trap score of 80 means four of five conditions were met. It does not
mean 80% -- and treating it as a probability is the most natural mistake to
make with a number on a 0-100 scale.

Calibration is the measurement that would justify such a reading: bucket the
historical occurrences by score, and look at what actually followed each
bucket. If higher scores were followed by better outcomes, the score carries
information about outcome. If they were not, the score is a description of the
setup and nothing more, which is still useful but must not be dressed up as a
probability.

Three ways this can come out, and all three are reported honestly:

    calibrated       higher scores, better outcomes, on adequate samples
    uninformative    outcomes do not order with the score
    unmeasurable     samples too small to tell the two apart

Most scores in a young system land in the third category. Saying so is the
point.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass, field
from typing import Any, Mapping, Sequence

from src.backtest.engine import BacktestConfig, Outcome

log = logging.getLogger(__name__)

CALIBRATED = "CALIBRATED"
UNINFORMATIVE = "UNINFORMATIVE"
UNMEASURABLE = "UNMEASURABLE"

DEFAULT_EDGES: tuple[float, ...] = (0.0, 40.0, 60.0, 80.0, 100.01)


@dataclass
class Bucket:
    low: float
    high: float
    sample_size: int = 0
    win_rate: float | None = None
    median_return: float | None = None
    mean_return: float | None = None

    @property
    def label(self) -> str:
        return f"{self.low:.0f}-{self.high:.0f}"

    def as_dict(self) -> dict[str, Any]:
        return {
            "range": self.label,
            "sample_size": self.sample_size,
            "win_rate": None if self.win_rate is None else round(self.win_rate, 1),
            "median_return": None if self.median_return is None else round(self.median_return, 2),
            "mean_return": None if self.mean_return is None else round(self.mean_return, 2),
        }


@dataclass
class Calibration:
    pattern: str
    metric: str
    horizon: int
    buckets: list[Bucket] = field(default_factory=list)
    verdict: str = UNMEASURABLE
    inverted: bool = False
    note: str = ""

    @property
    def measurable_buckets(self) -> list[Bucket]:
        return [b for b in self.buckets if b.sample_size > 0]

    def as_dict(self) -> dict[str, Any]:
        return {
            "pattern": self.pattern,
            "metric": self.metric,
            "horizon": self.horizon,
            "verdict": self.verdict,
            "inverted": self.inverted,
            "note": self.note,
            "buckets": [b.as_dict() for b in self.buckets],
        }


def calibrate(
    outcomes: Sequence[Outcome],
    pattern_key: str,
    metric: str = "score",
    horizon: int | None = None,
    edges: Sequence[float] = DEFAULT_EDGES,
    config: BacktestConfig | None = None,
    min_bucket_size: int = 20,
) -> Calibration:
    """Map score ranges onto the outcomes that followed them.

    `min_bucket_size` is deliberately separate from the sample threshold used
    elsewhere: a bucket is a slice of an already-thin sample, and a win rate
    from six occurrences should not be allowed to decide whether a score is
    informative.
    """
    settings = config or BacktestConfig.from_thresholds()
    target = horizon or settings.horizons[len(settings.horizons) // 2]

    calibration = Calibration(pattern=pattern_key, metric=metric, horizon=target)
    calibration.buckets = [Bucket(low=low, high=high) for low, high in zip(edges, edges[1:])]

    relevant = [
        outcome
        for outcome in outcomes
        if outcome.tradeable
        and outcome.signal.key == pattern_key
        and target in outcome.returns
        and isinstance(outcome.signal.metadata.get(metric), (int, float))
    ]

    if not relevant:
        calibration.note = f"no occurrences of {pattern_key} carry a '{metric}' value"
        return calibration

    for bucket in calibration.buckets:
        values = [
            outcome.returns[target]
            for outcome in relevant
            if bucket.low <= float(outcome.signal.metadata[metric]) < bucket.high
        ]
        bucket.sample_size = len(values)
        if not values:
            continue
        bucket.win_rate = sum(1 for v in values if v > 0) / len(values) * 100.0
        bucket.median_return = statistics.median(values)
        bucket.mean_return = statistics.fmean(values)

    usable = [b for b in calibration.measurable_buckets if b.sample_size >= min_bucket_size]
    if len(usable) < 2:
        calibration.verdict = UNMEASURABLE
        calibration.note = (
            f"only {len(usable)} bucket(s) reach {min_bucket_size} occurrences; "
            "the score cannot be told apart from noise at this sample size"
        )
        return calibration

    medians = [b.median_return for b in usable]
    if _is_monotonic(medians):
        calibration.verdict = CALIBRATED
        # Direction matters more than the fact of ordering. A score whose high
        # bucket does *worse* is informative and inverted -- which is a finding
        # about the pattern, not a detail of presentation, and calling it
        # "calibrated" without saying so would invite reading it backwards.
        rising = medians[-1] >= medians[0]
        calibration.inverted = not rising
        calibration.note = (
            "outcomes rise with the score: a higher score meant a better result"
            if rising
            else "outcomes FALL as the score rises: a higher score meant a WORSE "
                 "result, so the score is informative in the opposite direction "
                 "to the one its name implies"
        )
    else:
        calibration.verdict = UNINFORMATIVE
        calibration.note = (
            "outcomes do not order with the score: a higher score did not mean a "
            "better result, so the number describes the setup, not the odds"
        )
    return calibration


def _is_monotonic(values: Sequence[float | None]) -> bool:
    clean = [v for v in values if v is not None]
    if len(clean) < 2:
        return False
    rising = all(later >= earlier for earlier, later in zip(clean, clean[1:]))
    falling = all(later <= earlier for earlier, later in zip(clean, clean[1:]))
    return rising or falling


def format_calibration(calibration: Calibration) -> str:
    verdict = calibration.verdict
    if calibration.inverted:
        verdict += " (INVERTED)"
    lines = [
        "=" * 58,
        f"CALIBRATION:  {calibration.pattern}   on '{calibration.metric}'   at T+{calibration.horizon}",
        f"VERDICT:      {verdict}",
    ]
    if calibration.note:
        lines.append(f"              {calibration.note}")
    lines.append("")
    lines.append(f"{'score':>10} {'N':>6} {'win%':>8} {'median':>9} {'mean':>9}")
    for bucket in calibration.buckets:
        if not bucket.sample_size:
            lines.append(f"{bucket.label:>10} {0:>6} {'-':>8} {'-':>9} {'-':>9}")
            continue
        lines.append(
            f"{bucket.label:>10} {bucket.sample_size:>6} "
            f"{bucket.win_rate:>8.1f} {bucket.median_return:>9.2f} {bucket.mean_return:>9.2f}"
        )
    lines.append("")
    lines.append("  A score is not a probability until this table says it is.")
    return "\n".join(lines)
