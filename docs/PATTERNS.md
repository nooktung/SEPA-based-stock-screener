# The deterministic pattern layer

Structure, breakouts, traps, shakeouts, divergence, support/resistance zones,
behaviour scoring, conflict detection — and the backtest that decides whether
any of it is worth reading.

Everything here is computed. Nothing is interpreted. A trap score of 80 is a
statement that four of five observable conditions were met; whether that means
sell is a judgement made elsewhere, with the rest of the evidence in view.

---

## The rule that shapes every module

**Nothing may use information that did not exist yet.**

This is not a coding preference. A pattern detector that can see one session
ahead produces excellent backtest numbers and no tradeable strategy, and the
failure is invisible in the output — the equity curve looks wonderful right up
until it is traded. So the constraint is enforced structurally:

| Where | How |
|---|---|
| Swing pivots | need `swing_strength` bars on each side, so a pivot carries both the date it printed and `confirmed_on`, the date it could first be known |
| Divergence | dated from the *second* pivot's `confirmed_on`, never from when it printed |
| Traps | `CANDIDATE` on the day; `CONFIRMED` only from the later session that followed through |
| Breakout lifecycle | `setup_date`, `confirmation_date` and `failure_date` are three separate fields; `status_as_of(t)` never answers with anything later than `t` |
| Zones | a pivot becomes a level only after its `confirmed_on`; touches counted only up to `as_of` |
| Backtest | signals carry the status knowable on their own date, and a candidate and its confirmation enter the sample as two different signals on two different dates |

Several tests exist for no other purpose than to prove this. The most direct:
`test_a_pivot_is_invisible_on_the_day_it_prints`, and
`test_trailing_data_does_not_change_earlier_signals` — handing the engine extra
future bars must not change what it said about the past.

---

## What each engine answers

| Module | Question |
|---|---|
| `patterns.py` | Where are the swings? Is this trend making higher highs? Did the break hold? Is this a trap? |
| `divergence.py` | Is the oscillator confirming the price, or disagreeing with it? |
| `levels.py` | Where are the levels that have actually turned price before? |
| `behaviour.py` | Does the session-by-session behaviour resemble accumulation or distribution? |
| `events.py` | What is the full lifecycle of this break, and when did each stage become known? |
| `conflicts.py` | Which lines of evidence disagree, and how badly? |
| `changes.py` | What moved since the last analysis? |
| `backtest/` | Does any of this actually work? |

### Vocabulary

`ACCUMULATION_LIKE`, never "institutions are accumulating". Price and volume
cannot identify a participant: a stock absorbing supply on heavy volume looks
identical whether the buyer is a fund, an index tracker rebalancing, or a
thousand retail accounts. The engine reports behaviour that resembles
accumulation, and the two scores are independent — a quiet stock scores low on
both, which is the correct answer.

### Conflicts are not averaged

A stock can have strong trend, strong momentum, expanding volume — and sit
under a level it failed at twice with a trap score of 80. Rolled into one
number those cancel and the number looks calm. The disagreement was the most
useful thing in the data. Each conflict is therefore reported with the bullish
evidence, the bearish evidence, and a severity assigned by rule.

---

## Backtest results

Run: 39 tickers (the VN30 constituents plus the names already followed),
93,620 sessions, 2015-09-15 to 2026-08-28.

**Assumptions**, all in `thresholds.yaml`:

```
signal timestamp   the session the pattern became knowable
information set    bars up to and including the signal date
entry              next session's open
exit               close at signal date + horizon
costs              0.6% round trip (0.15% brokerage x2, 0.10% sell tax,
                   0.10% slippage x2)
horizons           T+1, T+3, T+5, T+10, T+20
```

Bearish patterns are scored on the fall they predicted, not on what a long
position would have lost — measuring everything as a long marks the bearish
patterns as failures exactly when they were right. **Vietnam's cash market has
no retail short selling**, so a SHORT row measures whether the signal predicted
a decline. That is useful for calibration and for deciding what to avoid; it is
not a strategy most participants can trade.

### The baseline is the whole story

| horizon | N | win% | median | expectancy | t |
|---|---|---|---|---|---|
| T+1 | 93,542 | 38.5% | −0.60% | **−0.49%** | −45.1 |
| T+3 | 93,464 | 43.2% | −0.60% | −0.33% | −20.8 |
| T+5 | 93,386 | 45.2% | −0.60% | −0.16% | −8.2 |
| T+10 | 93,191 | 48.1% | −0.27% | +0.25% | +9.2 |
| T+20 | 92,801 | 50.2% | +0.05% | **+1.12%** | +27.8 |

This table is the single most useful result in the project so far, and it is
about costs rather than patterns. A random session bought and held is a **loser
at every horizon up to a week** and only turns positive past ten sessions. The
0.6% round trip is simply larger than what five sessions of drift produces on
these stocks. Any pattern claiming a short-horizon edge has to clear that
hurdle before it clears anything else.

### Results at T+5, net of costs

`sig` is whether the mean sits more than two standard errors from zero. The
per-trade spread is around 6 percentage points, so a sample of a few hundred
cannot resolve an edge smaller than roughly half a point.

| Pattern | Dir | N | win% | expectancy | vs baseline | t | sig |
|---|---|---|---|---|---|---|---|
| `bullish_divergence` CANDIDATE | LONG | 515 | 46.4% | +0.21% | +0.37 | 0.7 | no |
| `breakout_60d` ATTEMPT | LONG | 309 | 48.0% | +0.14% | +0.30 | 0.4 | no |
| `breakout_60d` CONFIRMED | LONG | 224 | 46.4% | +0.08% | +0.24 | 0.2 | no |
| `shakeout` CONFIRMED | LONG | 1,058 | 47.7% | −0.11% | +0.05 | −0.5 | no |
| `bear_trap` CONFIRMED | LONG | 584 | 43.8% | −0.12% | +0.04 | −0.4 | no |
| **baseline** | LONG | 93,542 | 45.2% | −0.16% | — | −8.2 | yes |
| `accumulation_like` SETUP | LONG | 4,576 | 44.4% | −0.22% | −0.06 | −2.6 | yes |
| `breakout_20d` CONFIRMED | LONG | 294 | 40.1% | −0.39% | −0.23 | −1.1 | no |
| `failed_breakdown_20d` FAILED | LONG | 666 | 41.0% | −0.47% | −0.31 | −2.1 | yes |
| `breakdown_20d` ATTEMPT | SHORT | 1,000 | 44.4% | −0.73% | −0.57 | −3.9 | yes |
| `bear_trap` CANDIDATE | LONG | 1,685 | 44.4% | −0.79% | −0.63 | −4.8 | yes |
| `shakeout` CANDIDATE | LONG | 2,534 | 41.9% | −0.91% | −0.75 | −7.1 | yes |
| `distribution_like` SETUP | SHORT | 4,374 | 42.4% | −0.93% | −0.77 | −10.5 | yes |
| `bull_trap` CONFIRMED | SHORT | 1,089 | 44.3% | −1.14% | −0.98 | −5.8 | yes |
| `bearish_divergence` CANDIDATE | SHORT | 729 | 42.7% | −1.55% | −1.39 | −5.8 | yes |
| `bull_trap` CANDIDATE | SHORT | 3,005 | 40.9% | **−1.60%** | −1.44 | **−13.4** | yes |
| `failed_breakout_20d` FAILED | SHORT | 257 | 40.5% | −2.02% | −1.86 | −5.1 | yes |
| `bearish_divergence` CONFIRMED | SHORT | 263 | 38.0% | −2.04% | −1.88 | −4.9 | yes |

### What that says

**Not one pattern with a positive edge is distinguishable from noise.** The five
that beat the baseline all have |t| below 1. They are consistent with luck, and
reporting them as findings would be the mistake this whole layer was built to
avoid.

**Every pattern that *is* significant is significantly bad.** With samples in
the thousands and t-statistics past −5, these are not marginal calls.

**The bearish patterns are inverted.** `bull_trap` CANDIDATE, at N=3,005 and
t=−13.4, is the clearest result in the study: after a session that broke a
60-day high intraday and closed back below it, price went **up** more than
average, and the effect grows with horizon (−4.38% short expectancy by T+20).
The setup identifies a stock under strong demand pressing against resistance —
which is momentum, not exhaustion. The rejection reading is wrong for this
market.

`bearish_divergence` behaves the same way. Both should be treated as
information about strength, not as sell signals, until something changes.

### The regime lesson, demonstrated

The first run of this study used 12 tickers over 14 months and concluded the
exact opposite — that bearish patterns worked and bullish ones did not. That
report carried the caveat "the result describes one regime". It did:

| Pattern | 14 months, 12 tickers | 11 years, 39 tickers |
|---|---|---|
| `bull_trap` CANDIDATE | +3.63% (N=26) | **−1.60%** (N=3,005) |
| `failed_breakout_20d` FAILED | +1.93% (N=37) | **−2.02%** (N=257) |
| `bullish_divergence` CANDIDATE | −4.68% (N=21) | **+0.21%** (N=515) |

Every sign flipped. The earlier conclusion was a property of a declining
fourteen-month window, not of the patterns. This is the argument for sample
size in one table.

### Calibration

`bull_trap` on its own score, at T+5, N=3,002:

| score | N | win% | median |
|---|---|---|---|
| 40–60 | 510 | 41.8% | −0.88% |
| 60–80 | 1,172 | 41.3% | −1.05% |
| 80–100 | 1,320 | 40.2% | −1.11% |

**CALIBRATED (INVERTED).** The score orders the outcomes — monotonically — but
in the direction opposite to its name. A higher trap score meant price rose
*more* afterwards. The number carries information; the label on it is wrong.

### Robustness

Re-run at 0.5x, 1x and 2x the breakout confirmation threshold:

| Pattern | x0.5 | x1.0 | x2.0 | Verdict |
|---|---|---|---|---|
| `failed_breakout_20d` FAILED | +1.16% | +1.47% | +2.01% | **HIGH** |
| `breakdown_60d` CONFIRMED | +0.59% | +0.59% | +0.23% | **HIGH** |
| `breakout_20d` CONFIRMED | −4.12% | −4.67% | −3.51% | **HIGH** |

The sign survives in all three, so none of these results is an artefact of the
threshold it was measured at. Note that HIGH robustness on a bad result means
the result is reliably bad.

---

## Limitations, stated plainly

**One market, thirty-nine stocks.** The VN30 plus a handful more. Large caps
that move together: thirteen banks and six brokers among them, so the effective
independent sample is well below the row counts.

**Costs dominate the horizons most of the patterns speak to.** At 0.6% round
trip, nothing at T+1 to T+5 clears the bar on this evidence. A pattern layer
aimed at short-horizon entries is fighting the fee schedule before it fights
the market.

**Significance is not proof.** The t-statistics above assume occurrences are
independent, and they are not: signals cluster in time and across correlated
tickers, so the true error bars are wider than the printed ones. Treat |t| = 2
as a floor for consideration, not a certificate.

**Regimes are approximate before 2016.** The classifier needs a 200-day index
average, so the first year of the sample carries no regime label. The
historical regime series also has **no breadth at all** — breadth arrives only
on the live websocket — and DISTRIBUTION is defined largely by breadth. Any
DISTRIBUTION count in a historical breakdown is a trend-and-momentum
approximation.

**Nothing here is a strategy.** Forward returns after a signal are not a
trading result: no position sizing, no stop, no portfolio constraint, one
instrument at a time, and a short side that Vietnamese retail cannot access.

**What is not yet tested.** Pattern combinations (§30 asks for them) — with the
significant results all negative, combining them would be searching a large
space for something that looks good by chance, which is the overfitting the
same section warns against. Combinations are worth testing once a single
pattern earns a positive, significant result.

## Running it

```bash
python -m src.main analyse TCB          # every pattern for one ticker
python -m src.main analyse TCB --date 2026-06-30   # as it stood that day
python -m src.main backtest --limit 20  # the whole measurement
python -m src.main backtest --pattern breakout_60d:CONFIRMED   # with holdout split
```
