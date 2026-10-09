# Architecture

## Data flow

```
                 config/sources.yaml
                         |
                    [ registry ]  priority order + credential check
                         |
   +---------+---------+---------+---------+---------+
   | market  |  news   |financial|  macro  |  flow   |   collectors/
   +---------+---------+---------+---------+---------+
                         |
                   raw records with provenance
                         |
   +------------------------------------------------+
   | dedup -> entity extract -> normalize            |   processors/
   | 20 articles about one filing => 1 EVENT         |
   +------------------------------------------------+
                         |
   +------------------------------------------------+
   | deterministic computation (pandas/numpy)        |   quant/
   | RSI MACD ATR MA valuation breadth RS regime     |
   +------------------------------------------------+
                         |
                 compact feature vector
                (~50 numbers + N events)
                         |
   +------------------------------------------------+
   | LLM reasoning: interpret, compare, synthesise   |   analysts/
   | news | fundamental | technical | macro          |
   | flow | regime | risk                            |
   +------------------------------------------------+
                         |
   +------------------------------------------------+
   | scoring -> gates -> confidence -> risk/reward   |   decision/
   +------------------------------------------------+
                         |
                    report + signal row
                         |
                  [ append-only history ]
                         |
              outcome evaluation -> self-eval
```

## The context boundary

The single most important constraint: the LLM sees *changes and conclusions*,
never bulk data.

| Never send | Send instead |
|---|---|
| 10y OHLCV | 35 named technical features for the current session |
| 500 articles | 7 deduplicated events with impact scores |
| Unchanged financials | The 8 metrics that moved since last read |
| Unchanged macro | The 5 series that printed a new value |

Rationale: cost, latency, and accuracy. Long context degrades reasoning
quality on exactly the comparative judgements this system needs.

## Two-number output

`score` answers "how attractive does the evidence look".
`confidence` answers "how much should you trust that assessment".

They move independently. A stock can score 85 on strong technicals and a fresh
catalyst while confidence sits at 45 because half the fundamental data is
stale and the news analyst disagrees with the flow analyst. The gates in
`thresholds.yaml` block a BUY at that confidence — deliberately.

## Failure semantics

```
source fails
  -> retry per failure_policy (2 attempts, 3s then 10s backoff)
  -> still failing: mark dataset stale in data_freshness
  -> apply stale_confidence_penalty (-12)
  -> continue with remaining sources
  -> if data_completeness < 0.60: recommendation = INSUFFICIENT_DATA
```

No step in that chain interpolates a value.

## Sector profiles

`generic` and `securities` today. A profile selects which fundamental metric
set is required and which valuation method applies. Adding `banking` or
`real_estate` later means adding a profile, not branching the engine.

For `securities`, the metrics that actually explain results:

- brokerage revenue and HOSE/HNX market share
- margin balance and margin/equity ratio
- proprietary trading P&L — usually the swing factor
- FVTPL portfolio composition (deposits vs bonds vs equities)
- capital adequacy, equity growth, ROE
- market-wide trading value, since broker revenue is levered to it

## What is deliberately absent

No auto-execution path exists anywhere in the design. There is no order API,
no broker write credential, no position-opening code. Adding one would require
a new module, not a config flag. That asymmetry is intentional.
