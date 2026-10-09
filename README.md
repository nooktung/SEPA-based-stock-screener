# vn-stock-agent

An investment **research** assistant for the Vietnamese equity market.

It is not a trading bot. It never places orders. It produces evidence,
scenarios and explicit uncertainty; the investment decision belongs to the
user.

---

## Status

| Phase | Scope | State |
|---|---|---|
| 1 | Project structure, configuration, database schema, provider interfaces | **Done** |
| 2 | Market data provider implementations (DNSE) | **Partial** |
| 3 | News collection, deduplication, ranking | Not started |
| 4 | Technical engine | Not started |
| 5 | Fundamental engine | Not started |
| 6 | Macro engine | Not started |
| 7 | Flow engine | Not started |
| 8 | Analyst modules (Claude reasoning layer) | Not started |
| 9 | Decision / scoring engine | Not started |
| 10 | 08:00 pre-market report | Not started |
| 11 | Intraday alerts | Not started |
| 12 | End-of-day evaluation | Not started |
| 13 | Backtesting and self-evaluation | Not started |

---

## Quick start

```bash
pip install -r requirements.txt
cp .env.example .env          # optional in Phase 1
python -m src.main doctor     # validate config, show source status
python -m src.main initdb     # create schema, sync watchlist
python -m pytest -q           # 78 tests
```

`doctor` exits **2** while every enabled source is a fixture. That is correct
behaviour, not a bug: it means no real data is wired yet and no report may be
published.

---

## What Phase 1 actually gives you

**Provider abstraction** (`src/collectors/base.py`). Five interfaces —
`MarketDataProvider`, `NewsProvider`, `FinancialDataProvider`,
`MacroDataProvider`, `FlowDataProvider`. The engine depends only on these.
Changing data vendor means editing `config/sources.yaml` and adding one
subclass; `quant/`, `analysts/` and `decision/` never change.

**Provenance enforced by type.** Every record carries `source_id`,
`source_level` and `observed_at`. A record without provenance raises at
construction, not three layers downstream.

**Assertion types in the schema.** `FACT | INFERENCE | ESTIMATE | RUMOUR` is a
`CHECK` constraint in SQLite, not a convention. A level-5 forum source is
forced to `rumour_only` by the interface regardless of what the article says.

**Config validated at load.** Scoring weights that do not sum to 100, or
recommendation bands with a gap, fail immediately rather than producing a
plausible wrong number later.

**Fixture providers.** Deterministic offline data so the pipeline and all
tests run with no network and no credentials — and the runner knows they are
fixtures and refuses to publish from them.

**Append-only decision history.** `signals` is never mutated; outcomes go in
`signal_outcomes`. Past predictions cannot be quietly rewritten to look better.

---

## Design decisions worth knowing

**Data source: DNSE OpenAPI.** REST (`/price/ohlc`, `/price/{sym}/foreign-trading`,
`/market/instruments`) for bars and flow; WebSocket (`wss://ws-openapi.dnse.com.vn`)
for intraday. Auth is HMAC-SHA256 over api_key/api_secret. The SDK's execution
methods are blocked at the proxy layer -- see `FORBIDDEN_METHODS`.

**Adjusted prices are mandatory.** Vietnamese securities firms issue bonus
shares and stock dividends often. SSI went ex-rights on a 5:1 bonus in August
2026; VIX has done two capital increases in one year. An unadjusted series
shows a phantom gap-down on the ex-date that a naive breakdown detector reads
as a real breakdown. `adj_close` and `adj_factor` exist for this reason.

**Sector-specific fundamentals.** A generic P/E screen applied to a broker is
confidently wrong. The `securities` sector profile carries
`brokerage_revenue`, `margin_balance`, `prop_trading_pnl`,
`market_share_hose`, `capital_adequacy_ratio`. Proprietary trading is the
swing factor for VN brokers — it is why two firms with similar revenue can
report wildly different profit.

**Breadth is tracked separately from index level.** An index can rise on four
large caps while most of the market falls. The regime classifier must see that
difference.

**Score is not confidence.** Kept as two independent numbers. A high score
built on stale data with conflicting signals should read `BUY / 81 / conf 44`,
and the gates in `thresholds.yaml` will block the BUY at that confidence.

**Failure never fabricates.** Source down → mark stale → reduce confidence →
continue with what remains. If critical data is missing the recommendation
becomes `INSUFFICIENT_DATA` rather than a forced BUY/HOLD/SELL.

---

## Layout

```
config/       watchlist, thresholds, sources -- all behaviour tuning lives here
database/     schema.sql (SQLite dev / PostgreSQL prod, common subset only)
src/
  config.py         validated config loading
  database.py       bootstrap and connection
  collectors/
    base.py         the five provider interfaces
    registry.py     instantiation, priority fallback, staleness marking
    providers/      concrete vendors (fixtures only so far)
  quant/            deterministic calculation -- Python, never the LLM
  analysts/         interpretation and synthesis -- LLM, never arithmetic
  decision/         scoring, gating, risk/reward
  reports/          premarket / intraday / end-of-day
tests/        30 tests, no network required
```

---

## The division of labour

Deterministic code computes. The model interprets.

RSI, MACD, ATR, moving averages, valuation multiples, breadth ratios,
risk/reward arithmetic — all Python. Asking a language model to compute a
14-period RSI is slower, more expensive and less reliable than four lines of
pandas.

The model receives a compact feature vector and does what it is actually good
at: reading conflicting evidence, comparing sources, spotting that a positive
headline has negative second-order implications, and explaining why.

Not `500 articles + 10 years of OHLCV`. Instead: `7 new events, 3 material,
35 technical features, 8 changed financial metrics, 5 macro deltas`.

---

## Safety

This is research tooling, not advice, and not a guarantee of returns.

The system is built to say "current evidence favours", "base case",
"invalidation level" — and to say "data unavailable" instead of guessing. It
must never say a stock will definitely rise. If you find output that states
a prediction as certainty, that is a bug worth filing.
