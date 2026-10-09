---
name: sepa-vn-screening
description: Verified knowledge base for screening Vietnamese stocks (HOSE/HNX/UPCoM) with Mark Minervini's SEPA method — Trend Template (8 criteria), RS Rating replication, Stage Analysis, base patterns (VCP, cup with/without handle, flat base, double bottom, high tight flag/power play, base-on-base, 3-weeks-tight, pocket pivot), market-direction gate (follow-through/distribution days), SEPA risk and exit rules, Japanese candlestick continuation and reversal patterns with OHLC geometry and Bulkowski statistics, and indicator definitions matching TradingView (MA20/50/150/200, MACD, RSI, volume), plus Vietnam market adaptations (price bands, T+2 lock, ATC close, lot size). Use whenever screening/filtering stocks, writing or reviewing screener, pattern, candlestick or indicator code in src/quant/, choosing thresholds for config/thresholds.yaml, reading a ticker's stage/setup, or cross-checking a screening result. Triggers: "lọc cổ phiếu", "Trend Template", "Minervini", "SEPA", "VCP", "cốc tay cầm", "cup with handle", "mô hình nến", "nến đảo chiều", "RS Rating", "giai đoạn tăng trưởng", "tích lũy", "stage 2", "MA150", "MACD", "RSI".
---

# SEPA screening knowledge for the VN market

The rules here were researched from primary and reputable sources, then cross-checked by an
independent reviewer against other sources over 3 rounds until both agreed (2026-09-26).
Every rule carries a confidence tag — respect it:

| Tag | Meaning | How to use in code |
|---|---|---|
| **[SÁCH]** | In the original book, confirmed via independent summary | Default rule |
| **[TRANG GỐC]** | From the author's/publisher's own page (IBD, thepatternsite.com, TradingView, TA-Lib) | Default rule |
| **[THỨ CẤP]** | Secondary sources only | Default only if nothing better; say so in output |
| **[TRIỂN KHAI]** | Researcher's proposal to make a qualitative rule measurable — **not the original method** | Configurable; label as implementation choice |
| **UNVERIFIED** / disputed (D#/U#) | Could not be verified, or sources disagree | Never hard-code; put **both** values in config; surface the uncertainty |

## Which reference to read

Read only the file(s) the task needs:

| Task | File |
|---|---|
| Trend Template 8 criteria, RS Rating formulas, RS line, Stage 1–4 | [references/01-sepa-trend-template.md](references/01-sepa-trend-template.md) |
| VCP & base patterns, pivots/buy points, 3-weeks-tight, pocket pivot, fundamentals (Code 33), stops/expectancy/breakeven/8-week rule/climax top, market-direction gate | [references/02-bases-risk-market.md](references/02-bases-risk-market.md) |
| Candlestick geometry primitives (`is_tall` = 1.46× avg range, doji, shadows), single/double/triple/5-candle patterns, Bulkowski reversal %/rank/sample size | [references/03-candlesticks.md](references/03-candlesticks.md) |
| MA20/50/150/200, MACD 12/26/9, RSI Wilder 14 (Cardwell/Brown ranges), volume rules — TradingView-matching formulas | [references/04-indicators.md](references/04-indicators.md) |
| VN price bands, T+2 locked worst-case loss, ATC close, UPCoM VWAP reference, lot 100, tick sizes, what to adjust | [references/05-vietnam.md](references/05-vietnam.md) |
| Source URLs, full disputed/unverified list and resolution status | [references/06-sources-open-items.md](references/06-sources-open-items.md) |

## Screening workflow

1. **Universe**: ordinary 3-character shares on HOSE/HNX/UPCoM only (`config/universe.yaml`,
   `src/collectors/universe.py`). No derivatives, warrants, ETFs, bonds.
2. **Data gate**: bars must be ex-rights adjusted and not marked stale; need enough history
   (≥ ~252 sessions for 52-week metrics, ≥ 3×period bars before trusting EMA/MACD values).
   Missing/stale → `INSUFFICIENT_DATA`, never "pass".
3. **Market-direction gate** (02 §3.5): report market status (follow-through day, distribution
   day count) alongside results; SEPA avoids new entries in a correction.
4. **Trend Template** (01 §2.1): all 8 criteria must hold. Report each criterion's actual value,
   not just pass/fail. TT6 threshold (25% vs 30%) and RS formula (Variant A vs B) come from config.
5. **RS Rating**: percentile rank across the whole universe with sufficient data (01 §2.2).
6. **Setup detection** (02 §3.1–3.2b): VCP / cup / flat base / etc. — output depth of each
   contraction, duration, volume behaviour, pivot = defined high + 1 tick.
7. **Candles** (03): only meaningful with context — prior trend, near support/resistance, volume,
   confirmation next session. Flag ceiling/floor-locked sessions; exclude them from pattern geometry.
8. **Risk** (02 §3.4, 05 §6.2): report both `risk_to_stop` and the VN `locked_worst_case`
   (T+2 lock: up to ~19.6% HOSE / 27.1% HNX / 38.6% UPCoM from reference price, more from ceiling —
   not a hard bound).

## Non-negotiable rules

- **Deterministic code computes; the model interprets.** Every indicator, pattern and threshold
  check is Python in `src/quant/`. Never estimate an MA, RSI or pattern depth by eye/LLM.
- **Every threshold lives in `config/thresholds.yaml`** — including the disputed ones, with the
  alternative value documented in a comment.
- **Probability, not prediction.** Output says "ứng viên nghiên cứu", "bằng chứng hiện tại nghiêng
  về", "điểm vô hiệu hóa" — never "sẽ tăng". The Trend Template is a necessary condition, not a
  buy signal. Bulkowski statistics are US-market, small samples for rare patterns, untested in VN.
- **No trading.** This skill produces screens and evidence; it never places or suggests placing
  orders, and never gives personalised investment advice.
- **Cite the rule's tag** when explaining a result to the user, especially for [TRIỂN KHAI] and
  UNVERIFIED items.
- Numbers from the books were confirmed through summaries, not full text — if the user supplies a
  book passage that contradicts a rule, the book wins; update the reference file and note it.
