# Phase 2 handoff

## First thing to do on a networked machine

The development container could not reach `openapi.dnse.com.vn`, so the live
response shape of `GET /price/ohlc` has never been observed. `_parse_bars`
guesses at two plausible shapes. Confirm before building anything on top:

```python
# scripts/probe_ohlc.py
import os, json
from dnse import DNSEClient

c = DNSEClient(
    api_key=os.environ["DNSE_API_KEY"],
    api_secret=os.environ["DNSE_API_SECRET"],
    base_url="https://openapi.dnse.com.vn",
    api_version="2026-05-07",
)

status, body = c.get_ohlc("1D", query={
    "symbol": "SSI", "from": 1754006400, "to": 1756598400,
})
print("status:", status)
print(json.dumps(body, indent=2, ensure_ascii=False)[:2000])
```

Three things to read off the output:

1. **Shape.** Column arrays (`{t:[],o:[],h:[],l:[],c:[],v:[]}`) or a list of
   row objects? If neither, fix `_parse_bars` and add a test with the real
   payload pasted in.
2. **Units.** Is `c` around `21.4` or `21400` for SSI? That sets `price_scale`
   in `config/sources.yaml`. Getting this wrong is a silent 1000x error.
3. **Adjusted or raw?** Pull SSI across 17/08/2026 (5:1 bonus ex-date). If the
   series shows a ~-16.7% step, the feed is raw and `price_adjuster` must run.
   If the step is absent, the feed is already adjusted — record that in
   `sources.yaml` and skip adjustment for this source, but keep
   `verify_continuity()` running as a guard.

Do the same for VIX across 21/08/2026 (20:1 dividend, ~-4.8%). That one is the
harder case: small enough to hide inside the daily band.

## Then, in order

1. `src/collectors/backfill.py` — incremental bar backfill into `prices_daily`,
   resumable, writing `data_freshness` rows. Batch by ticker, respect the
   rate-limit headers (`X-RATELIMIT-*`).
2. Wire `price_adjuster` into the backfill: fetch `security_definition` for each
   session boundary, run `detect_from_reference`, `apply_adjustments`, then
   `verify_continuity`. A non-empty complaint list marks the series stale.
3. `automation/morning_job.py` skeleton that calls the backfill and prints a
   freshness report. No analysis yet — that is Phase 4 onward.

## Definition of done for Phase 2

- `python -m src.main doctor` shows `market_data LIVE` and `health_check` true
- 2 years of daily bars for all five watchlist tickers in `prices_daily`
- `verify_continuity` returns empty for every ticker across both August 2026
  ex-dates
- Tests still green, plus new tests using the real payload
