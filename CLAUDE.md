# CLAUDE.md

Context for Claude Code working in this repository. Read before making changes.

---

## What this is

An investment **research** assistant for the Vietnamese equity market. It
produces evidence, scenarios and explicit uncertainty. The investment decision
belongs to the user.

**It is not a trading bot and must never become one.** There is no order API,
no broker write credential, no position-opening code anywhere in the design.
`src/collectors/providers/dnse_provider.py` wraps the DNSE SDK in a
`_GuardedClient` that raises `PermissionError` on `post_order`, `put_order`,
`cancel_order`, `close_position` and every other name in `FORBIDDEN_METHODS`.
Eight parametrised tests enforce this. Do not remove the guard, do not add an
execution path, and do not "temporarily" bypass it for testing.

---

## Where things stand

| Phase | Scope | State |
|---|---|---|
| 1 | Structure, config, DB schema, provider interfaces | Done |
| 2 | Market data (DNSE REST + websocket, universe, backfill) | Done — see notes |
| 3 | News collection, dedup, ranking | Done for the focus list (CafeF ticker pages + CafeF/Vietstock RSS) |
| 4 | Technical / pattern engine + SEPA screener | Done (patterns backtested, see docs/PATTERNS.md; screener untested on history) |
| 5 | Fundamental engine | Tier A: CafeF quarterly income + growth grade (`src/quant/fundamentals.py`); banks/brokers not graded yet |
| 6 | Macro engine | Not started |
| 7 | Flow engine | Partial (DNSE foreign flow, breadth) |
| 8 | Analyst layer | **Claude Code in-session, no API** — see below |
| 9 | Decision / scoring engine | Not started |
| 10 | 08:00 pre-market report | Not started |
| 11 | Intraday alerts | Not started |
| 12 | End-of-day evaluation | Not started |
| 13 | Backtesting, self-evaluation | Engine + SEPA rule backtest (`src/backtest/sepa.py`, report in `data/backtest/`) |

Each phase must be functional and tested before anything relies on it.

### Working model (user decision, 2026-09-27)

The user screens the whole market into a **focus list of at most 80 monitored
names** (`python -m src.main screen`; 60 until 2026-09-29). Liquidity floor is
3 bn VND/session on every exchange, with a label-only tier (Cao >= 10 bn,
Khá 8-10, Thấp 3-8) shown next to traded value. The **main list is groups A + B only**
(A = 8/8 Trend Template, B = near-pass forming a base). Since 2026-09-30,
group C holds only uptrending names (price above MA50/150/200, RS >= 60, no
broken chart), hot user themes from `config/themes.yaml` first, and is NOT
padded; A/B names with a fresh SHARP_PULLBACK/BROKEN base are demoted. Group C
fills up to 80
and lives in a separate "Theo dõi mở rộng" tab / CLI table: monitored and
given news, but not SEPA-qualified and never mixed into the main list. The
user monitors only these plus `watchlist.yaml` until they explicitly ask to
re-screen. `python -m src.main monitor` (daily, after
the close) refreshes those tickers' bars, recomputes each one's state today
and the session before, and writes the differences to `alerts` and a brief
in `data/briefs/`. Screen runs are append-only
(`screen_runs`, `screen_members`) so they can be graded later. The SEPA rules
and their confidence tags live in the skill `.claude/skills/sepa-vn-screening/`.

**No Claude API.** No module under `src/` may import `anthropic`. Code
collects, computes and writes compact briefs; Claude Code reads news, original
financial-statement PDFs and writes analysis in the session when asked.

### Dashboard

`python -m src.main start` serves a dashboard on 127.0.0.1:8765
(`src/web/`, standard library only; charts from lightweight-charts via
jsDelivr). Everything is GET except ONE write, `POST /api/mylist`, which adds
or removes a ticker in `user_watchlist` ("Danh mục của tôi", 2026-09-30). It
accepts only listed 3-character shares, only a JSON body (forcing a CORS
preflight the server never answers), and only when Host/Origin are this
dashboard -- so another site open in the browser cannot write. Do not add
other write routes, and never anything order-like. All CLI output
is forced to UTF-8 in `main()` because Windows consoles default to cp1252.

`start` (2026-10-05, user decision) runs `src/pipeline.py` first: backfill ->
index-session -> index-check -> industry -> fundamentals -> screen -> refprices -> monitor
--no-fetch -> flow -> news -> logos -> market, each as its own CLI process, skipping what is already done
for the latest closed session (one DNSE VN-Index request decides it; today
counts after 15:05). It **re-screens automatically** whenever that session has
not been screened, and opens the web only when every step has finished.
`start --plan` shows the plan, `--no-update` opens the web at once. A lock
file (`data/pipeline.lock`) stops two updates running at once.

UI layout (rebuilt 2026-10-03 at the user's request): every stock group in the
sidebar (Danh sách chính = A+B, Theo dõi mở rộng = C, Của tôi, Đầu ngành, Tất
cả theo dõi) is the same table fed by `/api/stocks`, with three views
(Tổng quan / Kỹ thuật / Dòng tiền) and four or five filters. The header keeps
only a compact VN-Index card. The chart draws only what "Lớp hiển thị" enables
(default MA50/150/200, pivot/stop, volume, RSI). "Học" (`static/learn.js`)
draws candlestick and chart patterns as SVG. The user's own chart marks were
removed on request. Ngày / Tuần / Tháng (2026-10-05): `src/quant/timeframes.py`
resamples daily bars for `GET /api/bars?ticker|index=&tf=W|M` with MAs from
`chart_timeframes` (W 4/10/30/40, M 5/10/20/40, keyed by the daily MA slots),
RSI and MACD on weekly / monthly closes; the running period is `partial`.
Display only -- screening, states and alerts stay on daily bars. Since
2026-10-06 trendlines / channel are also found on the weekly / monthly
candles (`trend_overlays`, `chart_levels.trendline_by_tf`, counts in bars of
that timeframe, projection by weeks / months); zones stay the daily ones.
`scripts/audit_chart.py --round 3 --tf W|M` re-groups the candles by hand and
checks every line against its rule. Price history is ~11 years
(`backfill_history_days: 4000`, all DNSE has, from 2015-09-15, one request per
ticker); `backfill --deepen` filled older years once for series stored with
the former 5-year window (marker `data/history_depth.json`).

Foreign flow: `python -m src.main flow [--days N]` stores DNSE's per-session
foreign buy / sell **share volumes** (`flow_daily.foreign_buy_volume`,
`foreign_sell_volume`, migrated in `_migrate`). Domestic = matched volume
minus the foreign side (user's definition, 2026-10-03); DNSE does not split
domestic investors further, so never present it as individuals vs institutions.
`scripts/daily.ps1` runs monitor, then flow, news and logos, then the whole
market: `backfill` (every ticker, ~30-45 min) and `market`.

Whole market ("Toàn thị trường", 2026-10-04): `src/quant/market_view.py`
builds one row per ticker with prices (~1,470) from the screen's own functions
-- liquidity, Trend Template, MAs, 52-week high, base -- in ~15 s and stores it
in `market_snapshot` (last `market_view.keep_snapshots` dates). RS is the
latest screen run's rating; below the floor there is no RS and TT8 is "not
assessable", never failed. A series flagged in `data_freshness` gets its price
only. `GET /api/market` (read-only) adds group / leader / Của tôi at read time.
Display only: it never changes a screen run, a score or a group.

SQLite runs in WAL mode with a 60 s busy timeout (`src/database.connect`):
a long read during the 45-minute backfill once made its commit fail with
"database is locked".

Chart audit (2026-10-04, `scripts/audit_chart.py --round 1|2|3`, report in
`data/audit/`): 1) MA/RSI/MACD recomputed with plain loops from the published
definitions match what the chart receives on 14 tickers + VN-Index (rounding
only); 2) VNDirect bars agree with DNSE closes (ABB differs by its cash
dividend, which DNSE does not adjust); 3) every overlay obeys its rule
(465/465). Two real bugs were fixed: the RSI pane drifted 14 sessions from the
candles after any scroll / zoom (panes sync by bar position; every pane now
gets one point per session, blank where an indicator has no value), and the
zone text called "sessions crossing the zone" touches. The VN-Index volume
open item was resolved on 2026-10-08 (see "Index volume" below).

Index volume and breadth (2026-10-08, user decision: matched volume).
`python -m src.main index-session` first brings the index bars up to date (one
OHLC request: the figures attach to an existing row, and in the 15:30 job it runs
before anything else has fetched today's bar), then reads DNSE
`GET /price/{index}/market-index` (one request per session, `limit=1`, newest snapshot = the final one) into
`index_daily.matched_volume / put_through_volume / *_value_vnd / advancers /
decliners / unchanged / ceilings / floors` (`src/collectors/index_session.py`).
Matched volume equals VNDirect's VN-Index volume exactly; the OHLC feed's
index volume was sometimes total, sometimes matched, sometimes neither.
Distribution days and the FTD compare matched volume
(`sepa_screen.market.index_volume: matched`); a pair lacking it falls back to
the OHLC volume for both sessions, never one of each. DNSE keeps these
snapshots from 2026-03-25 (`data/index_session_depth.json` stops the walk
back). On 2026-10-08 the count went from 8 to 9. The same run stores DNSE's
trading calendar (`/market/working-dates`, today onwards, announced holidays
left out) in `data/calendar/working_dates.json`; `market_calendar.is_trading_day`
uses it for reference-price sessions and chart projections, weekdays outside it.

VN-Index vs HOSE (2026-10-09, user decision: report only, change nothing yet).
HOSE's own index values come from `api.hsx.vn/mk/api/v1/market/basic-chart`
(the API behind hsx.vn's chart; undocumented, read-only, whole history since
2015 in one request; `hose_index_chart`, kind `crosscheck`, level 1, stored in
`hose_index_daily`). DNSE's VN-Index OHLC bars miss the session extremes: on
137 sessions their high matched HOSE 94 times and their low 57, DNSE's own
market-index snapshot (now stored as `index_daily.session_high / session_low`)
135 / 136, VNDirect 102 / 100; over 11 years 575 lows, 359 highs, 68 closes
(only up to 2023) differ and 33 HOSE sessions are missing. `python -m src.main
index-check` (pipeline + 15:30 job) writes `data/audit/vnindex_check_<date>.json`:
agreement per source, the differing sessions, and the market status recomputed
with HOSE's high / low (on 09/10 only the rally-low date moved). The rules keep
reading index_daily until the user decides; if they switch, the open stays the
TradingView-style first value (HOSE's open is the previous close).

DNSE pacing (2026-10-08): `DnseHttpClient` paces each endpoint group from
`rate_limits` in sources.yaml (OHLC 50k/h, trades & foreign 10k/h, secdef &
market-index 1k/h, working-dates 100/h published), reads the 24-hour quota
from `X-Ratelimit-*` headers and stops with `DnseQuotaExceeded` before DNSE's
403, retries a 429 after `Retry-After`. A 403 is a quota error when the body or
the headers say so, otherwise a signature error.
Clicking the VN-Index card opens `/api/index` in the same drawer (chart only,
PP / FTD markers).

SSI FastConnect Data (2026-10-04, user decision: combine both providers):
DNSE stays the primary series; SSI is a `crosscheck` source kind that the
backfill and runner never read (`REQUIRED_KINDS` in src/config.py), stored in
`ssi_stock_daily` / `ssi_index_daily`. Commands `ssi` and `crosscheck`
(report in `data/audit/`), both run by the 15:30 job and harmless without
keys (`SSI_CONSUMER_ID`, `SSI_CONSUMER_SECRET`). The client is **data only**:
`ALLOWED_ENDPOINTS` permits the token call and eight `Market/*` reads; any
other endpoint or host (FastConnect Trading) raises PermissionError. Never
read or store SSI's PrivateKey. Switching any daily update to SSI (phase C in
docs/SSI.md) waits for the user's review of the cross-check reports.

On Windows `ThreadingHTTPServer`'s SO_REUSEADDR let a second `start` bind
8765 silently while the old server kept answering with old code;
`DashboardServer` turns that off, so a second `start` now says the port is
taken.

Logos: `python -m src.main logos` saves each monitored company's image from
`https://finance.vietstock.vn/image/<TICKER>` into `data/logos/` once (format
read from the bytes -- the server always says image/jpeg; an unknown code
redirects to a 43-byte blank GIF and is retried after
`logos.retry_missing_days`). The dashboard serves them read-only at
`GET /logo/<TICKER>` (3-character codes only) and shows a tinted tile with the
code when none is stored. Display only.

### SEPA upgrade (2026-09-27)

- RS tiers (`sepa_screen.rs_tiers`) order A/B before the composite score.
- `src/quant/setup_status.py` answers trend / base / entry and gives one of
  four neutral statuses (Ở vùng pivot, Đang theo dõi, Vừa breakout, Mất hiệu
  lực); every status carries the market note. Never rename these to BUY/READY.
- `market_status` detects the follow-through day (`market.ftd`) and counts
  distribution days since it.
- Monitor splits breakouts into BREAKOUT (volume, close in upper half of the
  range, not extended) and BREAKOUT_WEAK, naming what was missing.
- Fundamentals: `python -m src.main fundamentals` reads CafeF quarterly
  income (values are full VND despite the "tỷ đồng" label). A quarter is
  accepted only if NI = parent + minority within 1%, which rejects the
  misaligned bank / broker layouts rather than scoring them. Grade FAIL moves
  an A/B stock off the main list; NO_DATA never passes or fails.
- Industry: `python -m src.main industry` reads Vietstock's sector breadcrumb
  (2/4/6-digit codes) into `ticker_industry`; `src/quant/industry.py` ranks
  industries by mean raw RS (>= 3 liquid members). Display only, weight 0.
- TradingView-style technicals (`src/quant/tv_ratings.py`, `candles.py`,
  stored as `tech` in each monitor state, "Kỹ thuật" table view):
  display only, neutral labels ("Tích cực mạnh", never "Mua mạnh" as a label).
- Position check (`src/quant/position_check.py`, `/api/position`, "Kiểm tra
  vị thế" tab): the user's cost placed among the price levels (8% max loss,
  stop, pivot, MA50/200, the chart's nearest zones), a headline and rule text
  that change with the situation (loss beyond / within the max, small / big
  gain), the last five sessions and foreign flow. It must never output a buy /
  add / sell verdict -- personalised advice is out of scope.
- Chart overlays: support/resistance zones reuse `src/quant/levels.py`
  (also used by analysis/behaviour/conflicts -- do not overwrite it);
  `src/quant/channel.py` adds auto trendlines (two swing lows / highs, no
  close through the line between anchors) and a shaded parallel channel on
  the most recent one, drawn with a lightweight-charts series primitive
  (`FillPrimitive` in app.js). Only the nearest zone per side is drawn.
- Industry leaders: top `industry.leaders_per_industry` liquid names per
  industry by traded value (market cap is not stored), saved append-only in
  `screen_industry_leaders`; monitored and given news with the focus list,
  never presented as SEPA candidates unless also in A/B.
- Short-term setups (`backtest-swing`, `src/backtest/swing.py`, 2026-10-09, user
  asked for stocks to trade within two weeks): 4 families x variants x 4
  liquidity tiers (no liquidity floor, user's choice; "below 3 bn" is its own
  tier) under VN rules (next-open entry, skip a ceiling open, sell from the
  afternoon of T+2, floor-locked sessions unsellable, slippage by tier). Only
  setups clearing the `swing.gate` (in- and out-of-sample vs the same tier's
  baseline) may ever reach the dashboard, and only after the user reads the
  report -- nothing is displayed yet. First run: 3 of 36 cleared, but the two
  oversold ones (below 3 bn) have negative medians and tiny signal-day value,
  and the liquid pullback earns +0.13% at T+5. Never label any of it a
  prediction or a buy.
- Tester report (2026-10-11, `Bao_cao_kiem_thu_SEPA_Viet_Nam.docx`, fixed the same day):
  `screener.tracked_base` keeps the pivot / stop of a base for `base.hold_sessions`
  (5 = the status window) after a close through its pivot or its stop, so
  "Vừa breakout" never sits beside a pivot above the price; `stop_breached` when
  close <= stop. Pivot / stop rounded to the tick (`round_to_tick`). RS is ranked
  over every clean ticker (~785), and the market table is rebuilt after each
  new screen run (`rs_from` says which). DNSE rescales prices but not volumes on
  ex-rights dates: volume averages start at the ex-date (`monitor.
  min_sessions_after_action`), no valid breakout on the ex-date + 2 sessions.
  `recent_gaps` uses UPCoM bounds up to `tickers.listed_date` (exchange moves);
  `data_freshness.note` says why a series is flagged. News links found only in
  an RSS summary are `role = 'mention'`. `start --update-only` ends the 15:30 job.
- Tester report 2 (2026-10-11, `Bao_cao_kiem_thu_lan_2_SEPA_Viet_Nam.docx`), base rules 2
  (`screener.BASE_RULES`, stored as `base_rules` / `rules`): contractions are the major ones
  (`major_contractions`: lower highs, higher lows; KSV 37 -> 1); pivot = the last contraction's
  high, or the final tight area's when it sits in the upper half of the base (user decision);
  the base starts at its left-side high within `left_high_tolerance_pct` unless deeper than a cup;
  an opening spike (`spike_bar`) is measured at its close (ABB: flat base, pivot 18,300); stop risk
  <= 8% (floor rounded up); no stop without a forming base (user decision). States stored by
  the earlier rules keep their held pivot / stop until the hold ends (`monitor.legacy_hold`);
  `monitor --keep-events` re-reads states without rewriting a session's alerts. Status: a breached
  stop is never AT_PIVOT, TREND_TEMPLATE_RESTORED cancels its loss, BREAKOUT needs close >= pivot.
  `python -m src.main check` (src/web/checks.py, pipeline + 15:30 job, `GET /api/checks`) checks
  the invariants on the real data: the monitored states, plus the base rules on every ticker of
  the market (~1,224 read afresh, ~15 s). Nearest zones among every zone, on the tick grid. HCM/GAS/OIL/BIG
  are ambiguous news codes (`news.ticker_aliases`); `industry --names` reads Vietnamese names
  from Vietstock's profile title once.
- Tester report 3 (2026-10-12, `Bao_cao_kiem_thu_lan_3_SEPA_Viet_Nam.docx`, R3-01..14): status
  APPROACHING "Sắp tới pivot" (-5%..0, `entry.approaching_pct`); AT_PIVOT is pivot..+5% only, entry
  NO_BASE when the base is not forming. A stop counts as breached only when the close crossed it
  after the low it rests on (`stop_breached_on`), otherwise `stop_pending` ("stop dự kiến").
  Foreign "5 phiên" = the last five price sessions (`net5_sessions` / `net5_of`, empty sessions
  shown). Legacy holds show `legacy_until` / `legacy_new`. `watchlist.yaml` names carry diacritics
  and `sync_reference_data` never replaces a diacritic name with an ASCII one. A base shallower than
  a cup but under `base.min_generic_sessions` (25) is NEAR_HIGH / SHARP_PULLBACK, not BASE (HII).
  Criterion labels carry the TT6 threshold. Ambiguous-code mentions without stored evidence are
  dropped; `news.alias_exclusions` keeps "Sở giao dịch Chứng khoán TP.HCM" (HOSE) off HCM. Daily
  trendlines skip opening spikes. UI: checks popup fixed in the window, price column "Giá · %",
  timeframe remembered per stock / index, tab clicks wired before the first load, TradingView tab
  only for HNX / UPCoM, SEPA lessons on the Learn page.
- Backtest (`backtest-sepa`, 1,209 tickers, 2016-2026): most rules as
  implemented show no edge over a random liquid stock; valid breakouts of 8/8
  stocks help only in an UPTREND market and hurt under pressure. Thresholds
  were deliberately **not** changed -- the user decides.
  Rerun 2026-10-11 (1,463 tickers, `data/backtest/sepa_2026-10-11.md`, the old base
  rules on the same data in `..._oldrules.md`), now with a stop exit beside the fixed
  horizons: edge, where any, is at T+60 and in UPTREND / MIXED markets; breakouts under
  pressure lose under both rule sets (-3.25% T+20 with base rules 2); flat-base breakouts
  improved (+2.39% T+20, +6.52% T+60, both |t| >= 2); VCPs are 8x more frequent with a
  lower mean; "weak" breakouts do as well as "valid" ones; RS >= 90 first entries lag;
  the newest 30% of signals is mostly negative. The 8% stop ends ~55-60% of trades within
  60 sessions, trims losses in bad regimes and cuts winners in good ones.

### Phase 3 notes

- `python -m src.main news` collects CafeF's per-ticker list
  (`/du-lieu/Ajax/Events_RelatedNews_New.aspx`) for the focus list and the
  CafeF/Vietstock RSS feeds, stores every article, fetches bodies only for
  articles about monitored tickers (JSON under `data/news/`), deduplicates
  into `news_events` and scores deterministically. Requests are spaced 2.5 s.
- CafeF `/du-lieu/<TICKER>-<id>/` pages are reposted company disclosures; they
  carry the issuer's original PDF link and stay INFERENCE (level 3) until
  checked against it.
- `news-brief <TICKER>` writes <= 7 ranked events for in-session reading;
  `news-annotate` stores Claude Code's interpretation only after checking it
  (valid direction/horizon, evidence belongs to the event, assertion never
  raised above what the sources allow).
- Ambiguous codes (VND, USD, SJC, CNG...) are tagged only with a cue or the
  company name -- list in `thresholds.yaml` `news.ambiguous_tickers`.

### Phase 2 notes

- Universe: 1,663 ordinary 3-character shares on HOSE/HNX/UPCoM
  (`config/universe.yaml`, `python -m src.main universe`).
- `/price/ohlc` needs `type=STOCK|INDEX`; the column payload is confirmed live.
  Eleven years return in one request. DNSE history is **adjusted** for
  share-based corporate actions (docs/DNSE.md), which means it can be
  rewritten after an ex-date: `backfill` re-reads an overlap and refetches the
  full series when stored closes moved.
- Gap check: HOSE/HNX close-to-close cannot exceed the band; UPCoM's reference
  is the prior VWAP, so its close-to-close bound is -37.2%..+55.6%. Band
  breaks inside the last 300 sessions mark the series stale for screening.
- Reference prices (2026-10-06, user decision): `python -m src.main refprices`
  stores DNSE's `secdef` reference / ceiling / floor per session
  (`reference_prices`, `src/collectors/refprices.py`), labelled by DNSE's
  publication `time` (next session's definition appears ~20:45 the evening
  before; no history). The dashboard's % change is against the reference, as
  the price boards show it -- UPCoM's reference is the prior session's average
  price, so close-to-close differs there (PHP 05/10: +6.00% vs +4.65%).
  A missed UPCoM session is filled by the exchange rule (reference = the
  ticker's previous trading session's average matched price, DNSE trades
  `avgPrice`, rounded half-up to 100 dong; band +-15%), `method = avg_price`,
  re-checked every run against the published UPCoM references (13/13 on
  2026-10-06; below `average_rule_min_share` nothing is filled); published
  values always win. `refprices --market` fills every UPCoM ticker's latest
  session (~230 requests) for the whole-market view; `start` and the 15:30
  job run it. HOSE/HNX are never filled (ex-dates cannot be inferred).
  Corporate actions (2026-10-06): DNSE rescales a ticker's whole history the
  evening before its ex-date, today's bar included, while the tape stays
  unadjusted. `tape_adjustment_factor` keeps a bar that differs from the tape
  by one common factor on O/H/L/C (it used to be dropped as "unsettled": GMD,
  2:1 bonus, ex 07/10). The backfill records each rescale in
  `price_adjustments` (factor, last old-scale session); stored references stay
  as published and `lookup` reads old-scale ones through the factor;
  `board_pct` puts an old-scale session's close back on that day's tick to
  reproduce the board exactly. `plausible` rejects a reference whose % exceeds
  the exchange band (different scale) and falls back to close-to-close.
  Without a stored reference the % is close-to-close and says so. Monitor's
  "closed at the ceiling" uses the stored ceiling. Rules (up/down days,
  distribution days, screen) stay close-to-close.
- Still open: feed `secdef` reference prices to `price_adjuster.py` each
  morning (cash dividends are not covered by the adjustment check).

### Reconstruction note (2026-09-27)

The 2026-08-28 edits to files that already existed (thresholds.yaml,
sources.yaml, dnse_provider.py, main.py) were lost when the project was
copied; new files survived. They were rebuilt from code, tests, docs and the
database. Values marked `[chọn lại]` in `thresholds.yaml` are reasoned
defaults, not the originals. The `analyse`, `backtest`, `probe` and `breadth`
CLI commands referenced in docs have not been rebuilt yet.

---

## Non-negotiable design rules

**Deterministic code computes. The model interprets.** RSI, MACD, ATR, moving
averages, valuation multiples, breadth ratios, risk/reward arithmetic — all
Python in `src/quant/`. Never ask the LLM to calculate something pandas can
calculate. The LLM layer in `src/analysts/` receives a compact feature vector
and does judgement: conflicting evidence, cross-source comparison, second-order
implications.

**The context boundary.** Never send bulk data to the model.

| Never | Instead |
|---|---|
| 10y OHLCV | ~35 named features for the current session |
| 500 articles | 7 deduplicated events with impact scores |
| Unchanged financials | The 8 metrics that moved |

**Provenance or it doesn't exist.** Every record carries `source_id`,
`source_level`, `observed_at`. `Provenance.__post_init__` raises without a
source_id. Do not add a code path that constructs records without it.

**FACT / INFERENCE / ESTIMATE / RUMOUR** is a `CHECK` constraint in
`database/schema.sql`, not a convention. Level-5 sources (forums, social) are
forced `rumour_only` by the interface regardless of how confident the text is.

**Failure never fabricates.** Source down → mark stale → reduce confidence →
continue with what remains. If `data_completeness < 0.60`, the recommendation
becomes `INSUFFICIENT_DATA`. Never interpolate, never estimate a missing price,
never let a plausible-looking guess through.

**Score ≠ confidence.** Two independent numbers. High score on stale data with
conflicting analysts must read low confidence, and the gates in
`config/thresholds.yaml` block the BUY.

**Signals are append-only.** `signals` rows are never mutated. Outcomes go in
`signal_outcomes`. Past predictions must never be quietly improved.

**Nothing hard-codes a threshold.** Every tunable number lives in
`config/thresholds.yaml`. Config is validated at load — weights must sum to
100, recommendation bands must cover 0–100 with no gaps.

---

## Domain traps that have already bitten

**Units.** DNSE quotes stock prices in **thousands of VND** (`matchPrice: 24.35`
= 24,350 VND). Foreign-trading amounts arrive in **full VND**
(`buyTradedAmount: 46040960000`). Index values are **points** (`1669.38`).
Mixing these produces a valuation wrong by 1000x. Always route prices through
`normalize_price(raw, scale)` with an explicit configured scale. Never infer
scale from magnitude — a penny stock and a thousands-scaled blue chip occupy
the same numeric range, so a magnitude heuristic silently corrupts exactly the
cheap tickers where the error is hardest to spot.

**Ex-rights adjustment is mandatory, and the obvious API does not solve it.**
`DNSEClient.get_corporate_action_history` hits
`/accounts/{account_no}/corporate-action-history` — it is scoped to one trading
account and returns only entitlements *that account* received. For a watchlist
ticker the user does not hold, it returns nothing. It is a portfolio feature,
not reference data. Detection therefore works off the exchange's reference
price (`security_definition.basicPrice`) versus previous close.

**A small stock dividend is invisible to gap detection.** VIX's 20:1 dividend
(ex-date 21/08/2026) moves the reference price only −4.8%, *inside* the 7% HOSE
band. No heuristic can separate it from ordinary selling. SSI's 5:1 bonus
(−16.7%) is visible only because it is large. Reference prices are the only
correct path for small dividends, which are the kind VN brokers pay most often.
`test_small_stock_dividend_is_INVISIBLE_to_gap_detection` documents this — do
not "fix" that test by widening the band.

**Sector profiles matter.** A generic P/E screen applied to a broker is
confidently wrong. The `securities` profile carries `brokerage_revenue`,
`margin_balance`, `prop_trading_pnl`, `market_share_hose`,
`capital_adequacy_ratio`. Proprietary trading is the swing factor for VN
brokers — it is why VIX's Q2 2026 net profit fell 94% while revenue held up.

**Breadth ≠ index level.** An index can rise on four large caps while most of
the market falls. `market_index` WebSocket channel carries
`fluctuationUpIssueCount` / `fluctuationDownIssueCount` directly — use them.

**WebSocket lifetime is 8 hours.** DNSE closes the connection at the cap and
sends PING every 3 minutes expecting PONG within 1. A session opened at 07:00
for the pre-market job expires at 15:00, mid-afternoon. `RECONNECT_MARGIN`
rotates early so expiry never lands inside a trading session. Symbols must be
uppercase or the subscription silently returns nothing.

---

## Working conventions

- Run `python -m pytest -q` after every change. 873 tests currently pass.
- `congthuc.md` (2026-10-06, for the user's review) states every formula and threshold the
  system uses, with its source tag. Update it in the same change whenever a formula or a
  threshold changes.
- Never install `vnstock`/`vnai`: PyPI quarantined them on 2026-09-24 because
  releases wrote instructions into `AGENTS.md` and `~/.claude/CLAUDE.md`.
- `python -m src.main doctor` validates config and reports which sources are
  live vs fixture. Exit code 2 means fixtures only — reports are not
  publishable in that state, which is correct behaviour, not a bug.
- Fixture providers exist so the whole pipeline and every test run offline with
  no credentials. Never let fixture data reach a user-facing report.
- Credentials come from `.env` only. Never commit them, never hard-code them,
  never paste them into a chat or a comment.
- When a test fails, first ask whether the test is right. The gap-detection
  test above failed for a correct reason and the *code contract* changed, not
  the threshold.

## Safety

Never state a prediction as certainty. Use "current evidence favours",
"base case", "risk scenario", "invalidation level". If output ever claims a
stock will definitely rise, that is a bug worth filing.
