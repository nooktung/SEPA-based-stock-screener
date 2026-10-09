"""Rendering backtest results (§30).

Two rules about presentation, both of which change what a reader concludes:

**A win rate never appears without its sample size.** 68% from four occurrences
and 68% from three hundred are different claims, and printing them the same way
invites the reader to treat them the same way.

**Robustness is reported, not implied.** A pattern that only works at one exact
threshold is an artefact of that threshold. The check re-runs it at neighbouring
settings and reports whether the conclusion survives.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Sequence

import pandas as pd

from src.backtest.engine import (
    INSUFFICIENT,
    BacktestConfig,
    Outcome,
    PatternStats,
    run_backtest,
    split_sample,
    summarise,
)

ROBUST_HIGH, ROBUST_MEDIUM, ROBUST_LOW = "HIGH", "MEDIUM", "LOW"


def format_stats(stats: PatternStats, config: BacktestConfig) -> str:
    """The per-pattern block from the specification."""
    verdict = stats.sample_verdict(config)
    lines = [
        "=" * 58,
        f"PATTERN:  {stats.pattern}   [{stats.status}]",
        f"SAMPLE:   N = {stats.sample_size}   ({verdict})",
        f"SPAN:     {stats.first_signal} .. {stats.last_signal}   "
        f"across {stats.tickers} ticker(s)",
    ]
    if verdict == INSUFFICIENT:
        lines.append(
            f"          fewer than {config.min_sample_size} occurrences -- the numbers below"
        )
        lines.append("          describe this sample, not the pattern")
    lines.append("")
    lines.append(f"{'horizon':>8} {'N':>5} {'win%':>7} {'median':>8} {'mean':>8} "
                 f"{'avg win':>8} {'avg loss':>9} {'expect':>8} {'PF':>6} {'t':>6} {'sig':>4}")

    for horizon, horizon_stats in sorted(stats.horizons.items()):
        if not horizon_stats.sample_size:
            continue
        lines.append(
            f"{'T+' + str(horizon):>8} {horizon_stats.sample_size:>5} "
            f"{_fmt(horizon_stats.win_rate):>7} {_fmt(horizon_stats.median_return):>8} "
            f"{_fmt(horizon_stats.mean_return):>8} {_fmt(horizon_stats.average_gain):>8} "
            f"{_fmt(horizon_stats.average_loss):>9} {_fmt(horizon_stats.expectancy):>8} "
            f"{_fmt(horizon_stats.profit_factor):>6} {_fmt(horizon_stats.t_stat, 1):>6} "
            f"{('yes' if horizon_stats.distinguishable_from_noise else 'no'):>4}"
        )

    middle = config.horizons[len(config.horizons) // 2]
    reference = stats.horizons.get(middle)
    if reference and reference.sample_size:
        lines += [
            "",
            f"AT T+{middle}:",
            f"  expectancy            {_fmt(reference.expectancy)}%  per occurrence, net of costs",
            f"  max favourable exc.   {_fmt(reference.max_favourable_excursion)}%  (average)",
            f"  max adverse exc.      {_fmt(reference.max_adverse_excursion)}%  (average)",
            f"  worst drawdown        {_fmt(reference.max_drawdown)}%",
            f"  per-trade spread      {_fmt(reference.stdev)}%  "
            f"(standard error {_fmt(reference.standard_error, 3)})",
        ]
        if not reference.distinguishable_from_noise:
            lines.append(
                "  NOT DISTINGUISHABLE FROM NOISE: the mean sits within two standard "
                "errors of zero, so this sample cannot say the pattern has an edge."
            )

    if stats.by_regime:
        lines += ["", "REGIME BREAKDOWN:"]
        for regime, count in sorted(stats.by_regime.items(), key=lambda kv: -kv[1]):
            expectancy = stats.regime_expectancy.get(regime)
            lines.append(f"  {regime:<14} N={count:<5} expectancy {_fmt(expectancy)}%")

    return "\n".join(lines)


def _fmt(value: float | None, digits: int = 2) -> str:
    return "-" if value is None else f"{value:.{digits}f}"


def format_report(
    results: Mapping[str, PatternStats],
    config: BacktestConfig,
    limit: int | None = None,
) -> str:
    """The whole run, most-sampled patterns first."""
    ordered = sorted(results.values(), key=lambda s: -s.sample_size)
    if limit:
        ordered = ordered[:limit]

    header = [
        "BACKTEST ASSUMPTIONS",
        "-" * 58,
    ]
    for key, value in config.describe_assumptions().items():
        header.append(f"  {key:<22} {value}")
    header.append("")

    return "\n".join(header + [format_stats(stats, config) for stats in ordered])


# ---------------------------------------------------------------------------
# Robustness
# ---------------------------------------------------------------------------

@dataclass
class RobustnessResult:
    pattern: str
    variants: dict[str, float | None]
    verdict: str

    def as_dict(self) -> dict[str, Any]:
        return {"pattern": self.pattern, "variants": self.variants, "verdict": self.verdict}


def check_robustness(
    frames: Mapping[str, pd.DataFrame],
    pattern_key: str,
    variant_builder: Callable[[float], dict[str, Any]],
    multipliers: Sequence[float] = (0.5, 1.0, 2.0),
    config: BacktestConfig | None = None,
    horizon: int | None = None,
    **kwargs: Any,
) -> RobustnessResult:
    """Re-run one pattern at neighbouring settings and see if it survives.

    A conclusion that holds only at the parameter it was discovered with is a
    property of that parameter. This does not search for a better setting --
    that would be the overfitting it is meant to detect -- it only asks whether
    the sign of the result is stable.
    """
    settings = config or BacktestConfig.from_thresholds()
    target_horizon = horizon or settings.horizons[len(settings.horizons) // 2]

    variants: dict[str, float | None] = {}
    for multiplier in multipliers:
        overrides = variant_builder(multiplier)
        results, _ = run_backtest(frames, config=settings, **{**kwargs, **overrides})
        stats = results.get(pattern_key)
        horizon_stats = stats.horizons.get(target_horizon) if stats else None
        variants[f"x{multiplier}"] = horizon_stats.median_return if horizon_stats else None

    values = [v for v in variants.values() if v is not None]
    if len(values) < 2:
        verdict = ROBUST_LOW
    elif all(v > 0 for v in values) or all(v < 0 for v in values):
        spread = max(values) - min(values)
        centre = abs(statistics.fmean(values))
        verdict = ROBUST_HIGH if centre > 0 and spread <= abs(centre) * 2 else ROBUST_MEDIUM
    else:
        verdict = ROBUST_LOW

    return RobustnessResult(pattern=pattern_key, variants=variants, verdict=verdict)


def format_split(
    outcomes: Sequence[Outcome],
    config: BacktestConfig,
    pattern_key: str | None = None,
) -> str:
    """In-sample against out-of-sample, with the caveat this history deserves."""
    in_sample, out_sample = split_sample(outcomes, config)
    lines = [
        "IN-SAMPLE / OUT-OF-SAMPLE",
        "-" * 58,
        f"  split            chronological, {(1 - config.out_of_sample_fraction):.0%} / "
        f"{config.out_of_sample_fraction:.0%}",
        f"  in-sample        N = {len(in_sample)}",
        f"  out-of-sample    N = {len(out_sample)}",
    ]

    for label, sample in (("in-sample", in_sample), ("out-of-sample", out_sample)):
        results = summarise(sample, config)
        stats = results.get(pattern_key) if pattern_key else None
        if pattern_key and not stats:
            lines.append(f"  {label:<16} no occurrences of {pattern_key}")
            continue
        middle = config.horizons[len(config.horizons) // 2]
        if stats:
            horizon_stats = stats.horizons.get(middle)
            lines.append(
                f"  {label:<16} N={stats.sample_size:<5} "
                f"median T+{middle} = {_fmt(horizon_stats.median_return if horizon_stats else None)}%"
            )

    # The caveat has to be measured, not asserted: written as a constant it
    # would keep claiming fourteen months long after the history grew.
    dated = [o for o in outcomes if o.tradeable]
    if dated:
        first = min(o.signal.signal_date for o in dated)
        last = max(o.signal.signal_date for o in dated)
        span_days = (last - first).days
        holdout_days = int(span_days * config.out_of_sample_fraction)
        lines += [
            "",
            f"  Span: {first} .. {last}  ({span_days / 365.25:.1f} years), "
            f"holdout roughly {holdout_days / 365.25:.1f} years.",
        ]
        if holdout_days < 365:
            lines.append(
                "  A holdout shorter than a year covers one market condition. It can\n"
                "  contradict an in-sample result but cannot confirm one."
            )
        else:
            lines.append(
                "  Long enough to span more than one market condition, which is what\n"
                "  makes a surviving result worth anything."
            )
    return "\n".join(lines)
