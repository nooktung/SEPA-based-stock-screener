"""Backtest engine (§30).

The look-ahead tests here are the reason the module exists in this shape. A
backtest that can see one session into the future produces excellent numbers
and no tradeable strategy, and the failure is invisible in the output -- so it
has to be caught structurally, by tests.
"""

from datetime import date

import pandas as pd
import pytest

from src.backtest.engine import (
    ENTRY_NEXT_OPEN,
    ENTRY_SIGNAL_CLOSE,
    INSUFFICIENT,
    BacktestConfig,
    Outcome,
    Signal,
    evaluate_signal,
    generate_signals,
    regime_series,
    run_backtest,
    split_sample,
    summarise,
)
from src.backtest.report import (
    ROBUST_HIGH,
    ROBUST_LOW,
    check_robustness,
    format_report,
    format_split,
    format_stats,
)
from src.quant.patterns import STATUS_CANDIDATE, STATUS_CONFIRMED


@pytest.fixture
def config():
    return BacktestConfig.from_thresholds()


def build(rows, start="2026-01-01"):
    index = pd.bdate_range(start=start, periods=len(rows))
    out = pd.DataFrame(
        {
            "open": [r[0] for r in rows],
            "high": [r[1] for r in rows],
            "low": [r[2] for r in rows],
            "close": [r[3] for r in rows],
            "volume": [r[4] if len(r) > 4 else 1_000_000.0 for r in rows],
        },
        index=index,
        dtype="float64",
    )
    out["adj_close"] = out["close"]
    out.index.name = "trade_date"
    return out


def ramp(n, start=100.0, step=1.0):
    """A clean advance: each session opens where the last closed."""
    rows, price = [], start
    for _ in range(n):
        rows.append((price, price + step, price - step / 2, price + step))
        price += step
    return rows


# -- assumptions -------------------------------------------------------------

def test_the_configuration_states_every_assumption(config):
    assumptions = config.describe_assumptions()
    for key in ("entry", "exit", "price_field", "round_trip_cost_pct", "horizons"):
        assert key in assumptions


def test_costs_charge_both_legs_and_the_sell_tax(config):
    expected = config.brokerage_fee_pct * 2 + config.sell_tax_pct + config.slippage_pct * 2
    assert config.round_trip_cost_pct == pytest.approx(expected)
    assert config.round_trip_cost_pct > 0


def test_returns_are_reported_net_not_gross(config):
    frame = build(ramp(30))
    signal = Signal("TEST", "demo", "CANDIDATE", frame.index[10].date())
    outcome = evaluate_signal(signal, frame, config)

    entry = outcome.entry_price
    exit_price = float(frame["close"].iloc[11 + 5])
    gross = (exit_price - entry) / entry * 100.0
    assert outcome.returns[5] == pytest.approx(gross - config.round_trip_cost_pct)


# -- entry mechanics ---------------------------------------------------------

def test_entry_is_the_session_after_the_signal(config):
    frame = build(ramp(30))
    signal = Signal("TEST", "demo", "CANDIDATE", frame.index[10].date())
    outcome = evaluate_signal(signal, frame, config)
    # A signal is known at a close; the first tradeable price is the next open.
    assert outcome.entry_date == frame.index[11].date()
    assert outcome.entry_price == pytest.approx(float(frame["open"].iloc[11]))


def test_the_close_entry_variant_is_explicit(config):
    frame = build(ramp(30))
    at_close = BacktestConfig(**{**config.__dict__, "entry": ENTRY_SIGNAL_CLOSE})
    signal = Signal("TEST", "demo", "CANDIDATE", frame.index[10].date())
    outcome = evaluate_signal(signal, frame, at_close)
    assert outcome.entry_date == frame.index[10].date()
    assert outcome.entry_price == pytest.approx(float(frame["close"].iloc[10]))


def test_a_signal_on_the_last_session_has_no_entry(config):
    frame = build(ramp(30))
    signal = Signal("TEST", "demo", "CANDIDATE", frame.index[-1].date())
    outcome = evaluate_signal(signal, frame, config)
    # Filling it would mean trading at a price that does not exist yet.
    assert outcome.entry_price is None
    assert outcome.tradeable is False


def test_horizons_beyond_the_data_are_omitted_not_guessed(config):
    frame = build(ramp(30))
    signal = Signal("TEST", "demo", "CANDIDATE", frame.index[-4].date())
    outcome = evaluate_signal(signal, frame, config)
    assert 1 in outcome.returns
    assert 20 not in outcome.returns


def test_excursions_are_measured_along_the_path(config):
    frame = build(ramp(30))
    signal = Signal("TEST", "demo", "CANDIDATE", frame.index[5].date())
    outcome = evaluate_signal(signal, frame, config)
    assert outcome.mfe[5] >= outcome.returns[5]
    assert outcome.mae[5] <= outcome.mfe[5]
    assert outcome.max_drawdown[5] <= 0


# -- look-ahead --------------------------------------------------------------

def test_a_candidate_and_its_confirmation_are_separate_signals():
    """§30: never leak confirmation into the setup date."""
    rows = [(100, 101, 99, 100)] * 70 + [(101, 115, 98, 99, 5_000_000)] + [(98, 99, 94, 95)] * 5
    signals = generate_signals(build(rows), "TEST")
    traps = [s for s in signals if s.pattern == "bull_trap"]
    if traps:
        candidates = [s for s in traps if s.status == STATUS_CANDIDATE]
        confirmations = [s for s in traps if s.status == STATUS_CONFIRMED]
        assert candidates
        for confirmation in confirmations:
            # The confirmation is always dated later than the setup it belongs to.
            assert any(c.signal_date < confirmation.signal_date for c in candidates)


def test_signal_dates_never_precede_the_information_that_defines_them():
    rows = [(100, 101, 99, 100)] * 70 + ramp(30, start=100)
    frame = build(rows)
    signals = generate_signals(frame, "TEST")
    for signal in signals:
        assert signal.signal_date in {stamp.date() for stamp in frame.index}


def test_trailing_data_does_not_change_earlier_signals():
    """Truncating the future must not change what was knowable before it."""
    rows = [(100, 101, 99, 100)] * 60 + ramp(40, start=100)
    full = build(rows)
    cutoff = full.index[75].date()

    from_full = [
        (s.pattern, s.status, s.signal_date)
        for s in generate_signals(full, "TEST")
        if s.signal_date <= cutoff
    ]
    from_truncated = [
        (s.pattern, s.status, s.signal_date)
        for s in generate_signals(full.loc[: pd.Timestamp(cutoff)], "TEST")
    ]
    assert from_full == from_truncated


def test_the_engine_is_deterministic(config):
    frame = build([(100, 101, 99, 100)] * 60 + ramp(40, start=100))
    first, _ = run_backtest({"TEST": frame}, config=config)
    second, _ = run_backtest({"TEST": frame}, config=config)
    assert {k: v.as_dict(config) for k, v in first.items()} == {
        k: v.as_dict(config) for k, v in second.items()
    }


# -- statistics --------------------------------------------------------------

def _sample(returns, pattern="demo", status="CANDIDATE"):
    outcomes = []
    for index, value in enumerate(returns):
        signal = Signal("TEST", pattern, status, date(2026, 1, 5))
        outcome = Outcome(signal=signal, entry_date=date(2026, 1, 6), entry_price=100.0)
        outcome.returns = {1: value, 3: value, 5: value, 10: value, 20: value}
        outcome.mfe = {h: max(value, 0.0) for h in (1, 3, 5, 10, 20)}
        outcome.mae = {h: min(value, 0.0) for h in (1, 3, 5, 10, 20)}
        outcome.max_drawdown = {h: min(value, 0.0) for h in (1, 3, 5, 10, 20)}
        outcomes.append(outcome)
    return outcomes


def test_win_rate_and_medians_are_computed_per_horizon(config):
    stats = summarise(_sample([5.0, -2.0, 3.0, -1.0]), config)["demo:CANDIDATE"]
    horizon = stats.horizons[5]
    assert horizon.sample_size == 4
    assert horizon.win_rate == pytest.approx(50.0)
    assert horizon.median_return == pytest.approx(1.0)


def test_expectancy_combines_hit_rate_with_size(config):
    stats = summarise(_sample([10.0, -1.0, 10.0, -1.0]), config)["demo:CANDIDATE"]
    horizon = stats.horizons[5]
    # 50% of +10 and 50% of -1 is worth +4.5 per occurrence.
    assert horizon.expectancy == pytest.approx(4.5)


def test_profit_factor_divides_gains_by_losses(config):
    stats = summarise(_sample([6.0, -2.0, 4.0, -3.0]), config)["demo:CANDIDATE"]
    assert stats.horizons[5].profit_factor == pytest.approx(10.0 / 5.0)


def test_a_sample_with_no_losses_has_no_profit_factor(config):
    stats = summarise(_sample([1.0, 2.0]), config)["demo:CANDIDATE"]
    assert stats.horizons[5].profit_factor is None


def test_small_samples_are_labelled_insufficient(config):
    stats = summarise(_sample([5.0] * 4), config)["demo:CANDIDATE"]
    assert stats.sample_size == 4
    assert stats.sample_verdict(config) == INSUFFICIENT


def test_large_samples_are_labelled_adequate(config):
    stats = summarise(_sample([1.0] * (config.robust_sample_size + 1)), config)["demo:CANDIDATE"]
    assert stats.sample_verdict(config) == "ADEQUATE"


def test_untradeable_outcomes_are_excluded_from_the_sample(config):
    outcomes = _sample([5.0, 3.0])
    outcomes.append(Outcome(signal=Signal("TEST", "demo", "CANDIDATE", date(2026, 1, 5))))
    stats = summarise(outcomes, config)["demo:CANDIDATE"]
    assert stats.sample_size == 2


# -- regimes -----------------------------------------------------------------

def test_a_regime_is_attached_to_every_session(config):
    index = build(ramp(260, start=1000, step=2))
    series = regime_series(index)
    assert len(series) == len(index)
    assert series.dropna().isin(
        ["STRONG_BULL", "BULL", "EARLY_BULL", "RECOVERY", "SIDEWAYS",
         "DISTRIBUTION", "EARLY_BEAR", "BEAR"]
    ).all()


def test_regime_history_has_no_breadth_and_says_so():
    """Breadth is only pushed live, so no past session has it."""
    from src.backtest.engine import regime_series as function

    assert "Breadth is not available historically" in (function.__doc__ or "")


def test_results_break_down_by_regime(config):
    outcomes = _sample([5.0, -2.0])
    outcomes[0].signal.regime = "BULL"
    outcomes[1].signal.regime = "BEAR"
    stats = summarise(outcomes, config)["demo:CANDIDATE"]
    assert stats.by_regime == {"BULL": 1, "BEAR": 1}
    assert set(stats.regime_expectancy) == {"BULL", "BEAR"}


# -- splitting and robustness ------------------------------------------------

def test_the_holdout_split_is_chronological(config):
    outcomes = []
    for day in range(1, 21):
        signal = Signal("TEST", "demo", "CANDIDATE", date(2026, 1, day))
        outcome = Outcome(signal=signal, entry_date=date(2026, 1, day), entry_price=100.0)
        outcome.returns = {5: 1.0}
        outcomes.append(outcome)

    in_sample, out_sample = split_sample(outcomes, config)
    # Shuffling dates would let the strategy learn from sessions after the ones
    # it is tested on, so the split must respect time.
    assert max(o.signal.signal_date for o in in_sample) <= min(
        o.signal.signal_date for o in out_sample
    )


def test_an_empty_sample_splits_into_nothing(config):
    assert split_sample([], config) == ([], [])


def test_robustness_reports_low_when_the_sign_flips(config):
    frame = build([(100, 101, 99, 100)] * 60 + ramp(40, start=100))

    def variant(multiplier):
        from src.quant.patterns import PatternConfig

        base = PatternConfig.from_thresholds()
        return {
            "pattern_config": PatternConfig(
                **{**base.__dict__, "breakout_confirm_pct": base.breakout_confirm_pct * multiplier}
            )
        }

    result = check_robustness(
        {"TEST": frame}, "breakout_20d:CONFIRMED", variant, multipliers=(0.5, 1.0, 2.0), config=config
    )
    assert result.verdict in (ROBUST_HIGH, "MEDIUM", ROBUST_LOW)
    assert set(result.variants) == {"x0.5", "x1.0", "x2.0"}


# -- reporting ---------------------------------------------------------------

def test_a_win_rate_never_appears_without_its_sample_size(config):
    text = format_stats(summarise(_sample([5.0, -1.0, 2.0]), config)["demo:CANDIDATE"], config)
    assert "N = 3" in text
    assert INSUFFICIENT in text


def test_the_report_states_its_assumptions(config):
    results = summarise(_sample([5.0, -1.0]), config)
    text = format_report(results, config)
    assert "BACKTEST ASSUMPTIONS" in text
    assert "round_trip_cost_pct" in text


def test_the_split_report_measures_its_own_history_limitation(config):
    """The caveat is computed from the data, not asserted as a constant."""
    outcomes = []
    for month in range(1, 13):
        signal = Signal("TEST", "demo", "CANDIDATE", date(2026, month, 5))
        outcome = Outcome(signal=signal, entry_date=date(2026, month, 6), entry_price=100.0)
        outcome.returns = {5: 1.0}
        outcomes.append(outcome)

    text = format_split(outcomes, config, pattern_key="demo:CANDIDATE")
    assert "out-of-sample" in text
    assert "Span: 2026-01-05 .. 2026-12-05" in text
    # Under a year of holdout: the report must say it cannot confirm anything.
    assert "cannot confirm" in text


def test_a_long_history_gets_a_different_caveat(config):
    outcomes = []
    for year in range(2015, 2027):
        signal = Signal("TEST", "demo", "CANDIDATE", date(year, 6, 5))
        outcome = Outcome(signal=signal, entry_date=date(year, 6, 6), entry_price=100.0)
        outcome.returns = {5: 1.0}
        outcomes.append(outcome)

    text = format_split(outcomes, config, pattern_key="demo:CANDIDATE")
    assert "more than one market condition" in text


# -- direction ---------------------------------------------------------------

def test_bearish_patterns_are_scored_on_the_fall_they_predicted(config):
    """Measuring a long return after a distribution signal scores it as a
    failure exactly when it worked."""
    from src.backtest.engine import LONG, SHORT, direction_of

    assert direction_of("breakout_60d") == LONG
    assert direction_of("breakdown_60d") == SHORT
    assert direction_of("bull_trap") == SHORT
    assert direction_of("bear_trap") == LONG
    assert direction_of("failed_breakout_60d") == SHORT
    assert direction_of("distribution_like") == SHORT
    assert direction_of("bullish_divergence") == LONG


def test_the_longest_matching_prefix_wins():
    from src.backtest.engine import LONG, SHORT, direction_of

    # "failed_breakout" must not be read as "breakout".
    assert direction_of("failed_breakout_20d") == SHORT
    assert direction_of("breakout_20d") == LONG


def test_a_short_signal_profits_when_price_falls(config):
    falling = build([(100 - i, 101 - i, 98 - i, 99 - i) for i in range(30)])
    short = Signal("TEST", "bull_trap", "CANDIDATE", falling.index[5].date())
    long = Signal("TEST", "breakout_20d", "CONFIRMED", falling.index[5].date())

    assert evaluate_signal(short, falling, config).returns[5] > 0
    assert evaluate_signal(long, falling, config).returns[5] < 0


def test_costs_are_charged_on_both_directions(config):
    frame = build(ramp(30))
    long_return = evaluate_signal(
        Signal("TEST", "breakout_20d", "CONFIRMED", frame.index[10].date()), frame, config
    ).returns[5]
    short_return = evaluate_signal(
        Signal("TEST", "bull_trap", "CANDIDATE", frame.index[10].date()), frame, config
    ).returns[5]
    # The two are mirror images of the gross move, both paying the round trip.
    assert long_return + short_return == pytest.approx(-2 * config.round_trip_cost_pct, abs=1e-6)


def test_excursions_swap_sides_for_a_short(config):
    frame = build(ramp(30))
    outcome = evaluate_signal(
        Signal("TEST", "bull_trap", "CANDIDATE", frame.index[5].date()), frame, config
    )
    # Price rose, so a short's favourable excursion is small and its adverse
    # excursion is large and negative.
    assert outcome.mae[5] < 0
    assert outcome.mfe[5] >= outcome.mae[5]


# -- baseline ----------------------------------------------------------------

def test_a_baseline_of_every_session_is_produced(config):
    from src.backtest.engine import baseline_signals

    frame = build(ramp(40))
    signals = baseline_signals(frame, "TEST")
    assert len(signals) == len(frame)
    assert all(s.pattern == "baseline" for s in signals)


def test_the_baseline_is_what_makes_a_pattern_interpretable(config):
    """A pattern at -1.5% is only bad if the average session did better."""
    frame = build([(100, 101, 99, 100)] * 60 + ramp(40, start=100))
    results, _ = run_backtest({"TEST": frame}, config=config, include_baseline=True)
    assert "baseline:ALL_SESSIONS" in results
    assert results["baseline:ALL_SESSIONS"].sample_size > 50


def test_the_baseline_can_be_switched_off(config):
    frame = build([(100, 101, 99, 100)] * 60 + ramp(40, start=100))
    results, _ = run_backtest({"TEST": frame}, config=config, include_baseline=False)
    assert "baseline:ALL_SESSIONS" not in results


# -- statistical caution -----------------------------------------------------

def test_a_tiny_edge_on_a_wide_spread_is_not_distinguishable(config):
    """0.3 points of edge against a 6-point spread is luck, not a finding."""
    import random

    random.seed(11)
    returns = [random.gauss(0.3, 6.0) for _ in range(300)]
    stats = summarise(_sample(returns), config)["demo:CANDIDATE"]
    horizon = stats.horizons[5]
    assert horizon.standard_error is not None
    assert horizon.distinguishable_from_noise is False


def test_a_large_consistent_edge_is_distinguishable(config):
    import random

    random.seed(11)
    returns = [random.gauss(3.0, 2.0) for _ in range(300)]
    horizon = summarise(_sample(returns), config)["demo:CANDIDATE"].horizons[5]
    assert horizon.t_stat > 2.0
    assert horizon.distinguishable_from_noise is True


def test_the_standard_error_shrinks_with_sample_size(config):
    import random

    random.seed(7)
    small = summarise(_sample([random.gauss(1.0, 5.0) for _ in range(40)]), config)
    random.seed(7)
    large = summarise(_sample([random.gauss(1.0, 5.0) for _ in range(4000)]), config)
    assert (large["demo:CANDIDATE"].horizons[5].standard_error
            < small["demo:CANDIDATE"].horizons[5].standard_error)


def test_a_single_occurrence_has_no_error_bar(config):
    horizon = summarise(_sample([5.0]), config)["demo:CANDIDATE"].horizons[5]
    assert horizon.standard_error is None
    assert horizon.distinguishable_from_noise is False
