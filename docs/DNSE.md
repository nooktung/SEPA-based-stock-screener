# DNSE OpenAPI integration

The live market data source. Everything here is read-only: history over signed
REST, realtime over websocket. No order, position or trading-token code exists
in this repository, and the trading channels DNSE offers are deliberately not
wired up.

---

## Credentials

Created in the DNSE app under **Quản lý mã kết nối**. You get an API key and an
API secret; the secret is displayed once at registration and cannot be read
back, so store it immediately. If it is lost, reissue the pair.

```bash
cp .env.example .env
# then fill in
DNSE_API_KEY=...
DNSE_API_SECRET=...
```

`.env` is gitignored and read by `src/env.py` at CLI start. A real environment
variable always wins over the file. The secret is never logged, never included
in an exception message, and `DnseCredentials.__repr__` redacts it.

Verify end to end:

```bash
python -m src.main probe SSI
```

---

## REST

Base URL `https://openapi.dnse.com.vn`. Every request carries four headers:

| Header | Value |
|---|---|
| `Date` | RFC 1123, UTC — `Wed, 26 Aug 2026 03:11:07 +0000` |
| `X-Signature` | `Signature keyId="…",algorithm="hmac-sha256",headers="(request-target) date",signature="…",nonce="…"` |
| `x-api-key` | the API key |
| `version` | API version pin, currently `2026-07-23` |

The signed string is, with real newlines:

```
(request-target): get /price/ohlc
date: Wed, 26 Aug 2026 03:11:07 +0000
nonce: 9f2c…
```

signed with HMAC-SHA256 over the API secret, base64 encoded, then URL escaped.

Two consequences worth remembering:

**The query string is not signed.** Only the path. Parameters can be reordered
without breaking anything.

**Requests are time-sensitive.** The `Date` header is part of the signature, so
a machine clock more than a few minutes out of true produces a 401 that looks
exactly like a wrong secret. `DnseAuthError` names both causes for that reason.

### Endpoints this project uses

| Call | Purpose |
|---|---|
| `GET /price/ohlc` | historical candles — the market provider |
| `GET /price/{symbol}/foreign-trading` | foreign investor buy/sell — the flow provider |
| `GET /price/{symbol}/secdef` | reference / ceiling / floor of the current session (`refprices`) |
| `GET /price/{symbol}/trades` | the tape's last trade: candle settlement check, UPCoM session average |
| `GET /price/{index}/market-index` | index snapshot every 5 s: matched / put-through volume, breadth (`index-session`) |
| `GET /market/working-dates` | trading calendar (`index-session`), and the `health_check()` probe |
| `GET /market/instruments` | the universe (`universe`) |

### Endpoints available and not yet used

`/price/{symbol}/quotes`, `/price/{symbol}/close`, `/market/trading-session`,
volume profile, expected price.

### Limits (read 2026-10-08, "Giới hạn truy cập")

| Group | Per hour | Per day |
|---|---|---|
| `/price/ohlc` | 50,000 | 100,000 |
| trades, foreign-trading, quotes, expected price | 10,000 | 100,000 |
| secdef, market-index, instruments, close, trading-session | 1,000 | 10,000 |
| working-dates | 100 | 1,000 |

Over the rate: HTTP 429. Over the quota: HTTP 403 — the same status as a bad
signature, so the client tells them apart by the body / the headers. Every
response carries the 24-hour quota of its group:

```
X-Ratelimit-Limit: 10000        (market-index; working-dates showed 1000)
X-Ratelimit-Remaining: 9986
X-Ratelimit-Reset: 1791540892   (epoch seconds, ~24 hours after the window opened)
```

`DnseHttpClient` paces each group separately (`rate_limits` in sources.yaml:
a per-minute pace and a rolling-hour budget well under the table), stops with
`DnseQuotaExceeded` when `X-Ratelimit-Remaining` falls to `min_quota_remaining`,
and retries a 429 after `Retry-After` (`retry_429`), halving that group's pace.
Before 2026-10-08 one pace of 60/minute served every endpoint.

### Market index (verified 2026-10-08)

`GET /price/{index}/market-index?from=&to=&limit=&nextPageToken=` — `from` and
`to` are epoch seconds and **must fall on the same date** (HTTP 400 "from and to
must be in the same date" otherwise). Snapshots every 5 seconds, **newest
first**, so `limit=1` over the whole day returns the final state (stamped
15:05:05, repeated after the close). History reached 2026-03-25 on 2026-10-08
(2026-01-06 and 2025-10-08 came back empty).

```
valueIndexes / highestValueIndexes / lowestValueIndexes / priorValueIndexes   points
totalVolumeTraded   = contauctAccTrdVol (matched) + blkTrdAccTrdVol (put-through)
grossTradeAmount, contauctAccTrdVal, blkTrdAccTrdVal                         billions of VND
fluctuationUpIssueCount / SteadinessIssueCount / DownIssueCount              advancers / unchanged / decliners
fluctuationUpperLimitIssueCount / LowerLimitIssueCount                       of which at the ceiling / floor
```

Advancers include the ceiling stocks and decliners the floor stocks: the three
groups' volumes (`fluctuation*IssueVolume`) add up to `totalVolumeTraded`.

**`contauctAccTrdVol` equals VNDirect's VN-Index volume exactly** on 01/04,
01/07, 08/09, 02/10 and 08/10/2026. The OHLC feed's index volume does not
follow one definition: 08/09 equals the total (with put-through), 14/09 the
matched volume, 02/10 neither (785.1M vs 615.8M matched, 829.4M total). That was
the open item "DNSE's VN-Index volume is 11-40% above VNDirect's on most days
but equal on some"; distribution days now use the matched figure
(`sepa_screen.market.index_volume`).

### Index OHLC high / low (checked against HOSE, 2026-10-09)

`/price/ohlc` with `type=INDEX` gets the open and close right but often misses
the session's extremes: on 137 sessions (25/03-09/10/2026) its VN-Index high
matched HOSE's published value on 94, its low on 57 (08/10: low 1738.97 = the
close; HOSE 1722.27). The market-index snapshot's `highestValueIndexes` /
`lowestValueIndexes` matched on 135 / 136 and are stored as `session_high` /
`session_low`. HOSE's convention: the index opens at the previous close and
that value counts toward the day's range. Report: `python -m src.main
index-check`; the rules still read the OHLC bar (user decision).

### Working dates (verified 2026-10-08)

`GET /market/working-dates` → `{"workingDates": ["2026-10-08", "2026-10-09", …]}`:
the sessions from today to a year ahead. Announced holidays are left out
(24/11/2026, 01/01, 30/04 and 02/09/2027 on 2026-10-08); Tet 2027 was not yet.
Past dates are not served, so `src/collectors/trading_calendar.py` keeps them
from earlier reads.

Account and order endpoints exist in the API. They are intentionally absent
from `DnseHttpClient`, which implements `get()` and nothing else.

### OHLC response shape

TradingView UDF style — six parallel arrays, not a list of bars:

```json
{"t": [1756177200], "o": [24.25], "h": [24.35],
 "l": [24.15], "c": [24.30], "v": [1184240]}
```

Query parameters: `type` (`STOCK` | `INDEX` | `DERIVATIVE`), `symbol`,
`resolution` (`1`, `3`, `5`, `15`, `30`, `1H`, `1D`, `1W`), `from` and `to` as
epoch seconds.

Three normalisations happen in `DnseMarketProvider` so nothing downstream has
to know about them:

**Stock prices arrive in thousands of VND.** `24.35` means 24,350 VND. Index
values and derivative prices are index points and are not scaled. What the
provider returns is VND for stocks, points for indices.

**Timestamps are epoch seconds on a Vietnamese trading day.** Converted with a
fixed +07:00 offset — Vietnam has no DST, so this is exact and needs no tzdata.
Using the machine's local timezone would misdate bars for anyone outside UTC+7.

**Turnover is not derived.** The feed carries no traded value, so `value_vnd`
stays empty rather than being reconstructed as `close × volume`.

Long ranges are fetched in `chunk_days` windows (default 365) and merged by
date. The endpoint caps how many bars one call returns, and a silently
truncated five-year request is worse than several requests.

---

## Price adjustment — verified 2026-08-28

`config/sources.yaml` now carries `price_series: adjusted`, so `adj_close` is
populated. This was measured, not assumed, and the measurement is worth
repeating if anything ever looks wrong.

DNSE does not document whether `/price/ohlc` returns a series adjusted for
bonus issues and stock dividends. Vietnamese securities firms issue them
constantly, and an unadjusted series shows a phantom gap down on the ex-date
that a breakdown detector reads as a real breakdown.

**What was checked.** 275 sessions (2025-07-24 to 2026-08-28) for all five
watchlist brokers, spanning SSI's 5:1 bonus in August 2026 and VIX's two
capital increases. Not one overnight move above 10% in any of them. An
unadjusted series would step down roughly 17% on those ex-dates.

**What that does not cover.** Cash dividends. A 5% cash dividend produces a 5%
gap, which is indistinguishable from an ordinary down day inside the ±7% HOSE
band. Share-based actions are the ones that wreck a technical read, so this is
an acceptable residual — but it is a residual, not a proof.

To re-check: pick a ticker with a known ex-rights date, pull a window spanning
it, and look at the close either side.

```bash
python -m src.main probe SSI --days 400 --rows 40
```

`probe` flags every overnight move of 10% or more. A HOSE stock cannot move
more than 7% in a session, so each flag is either a corporate action or an
unadjusted series. If the closes step down cleanly across a known ex-date, the
series is unadjusted: set `price_series: unknown` and treat the adjustment gap
as an open item. If the series is continuous across the ex-date, `adjusted` is
correct and `adj_close` is authoritative.

Never set it because it seems likely. An unverified `adj_close` is worse than
an absent one: absent data lowers confidence, wrong data raises it.

### Foreign trading response shape

Nothing like the OHLC endpoint, and the differences are worth stating plainly.

```json
{"foreigners": [{"symbol": "SSI", "time": "2026-08-27 15:32:22.530",
                 "buyVolume": 200900, "buyTradedAmount": 4294265000,
                 "sellVolume": 326500, "sellTradedAmount": 6979330000,
                 "totalBuyVolume": 200976, "totalBuyTradedAmount": 4295891400,
                 "totalSellVolume": 326506, "totalSellTradedAmount": 6979458700,
                 "foreignerOrderLimitQuantity": 1022025471,
                 "foreignerBuyPossibleQuantity": 1028640000}],
 "nextPageToken": "…"}
```

**Rows are intraday snapshots, not daily records.** Each is the running state at
a moment in the session — 12:46, 12:47, 15:32 — with cumulative day totals in
the `total*` fields. A session's final numbers are the *newest* row of that day,
which is why the provider asks for `limit=1&order=DESC` per day instead of
paging through thousands of ticks.

**The window is capped at one day.** `from`/`to` spanning two days returns
`400 range exceeds maximum time range`. History is therefore one request per
session, which is why the collector skips weekends and sessions it already
holds. `from` is mandatory: omitting it returns `400 missing timestamp param`.

**A non-trading day returns an empty list**, not an error and not a zero.

**Room fields are on every row.** `foreignerOrderLimitQuantity` is the total
foreign ownership limit and `foreignerBuyPossibleQuantity` what remains of it —
material for Vietnamese equities, where a filled room changes who can buy at
all. `flow_daily` has no column for either, so they are not persisted yet.

**Not on this feed at all:** proprietary-desk flow, ETF creations and
redemptions, matched trading value. Those columns stay NULL.

## Market breadth

Settled counts come from REST since 2026-10-08: `python -m src.main
index-session` reads the market-index snapshot above and stores advancers,
unchanged, decliners, ceilings and floors on the session's `index_daily` row,
with the matched and put-through volume.

The same fields are pushed on the `market_index` websocket channel
(`src/collectors/breadth.py` takes one frame) — useful only for intraday
readings: the channel is silent outside a session, and the counts keep moving
until the close.

## Index symbols

DNSE names some benchmarks differently from the exchanges. The provider
translates on the way out and labels the returned bars with the project's name,
so `config/`, the database and the quant layer only ever see one spelling:

| Project | DNSE |
|---|---|
| `HNXINDEX` | `HNX` |
| `VNINDEX`, `VN30` | unchanged |

Other indices DNSE publishes: `HNX30`, `VN100`, `VNXALLSHARE`, `UPCOM`,
`VNDIVIDEND`, `VN50GROWTH`, `VNMITECH`. Add any further mismatch to
`index_symbol_aliases` in the source entry rather than renaming a benchmark in
`watchlist.yaml` — a vendor's vocabulary should not leak into the system's.

---

## Websocket

`wss://ws-openapi.dnse.com.vn/v1/stream?encoding=json` (or `msgpack`).
Implemented in `src/collectors/providers/dnse_stream.py`, used from Phase 11.

Handshake:

1. Server sends a welcome frame carrying a session id.
2. Client sends `{"action":"auth", "api_key", "signature", "timestamp",
   "nonce"}` where the signature is HMAC-SHA256 of `{api_key}:{timestamp}:
   {nonce}`, hex encoded. **This is a different construction from the REST
   signature** — the two are not interchangeable.
3. Server answers `{"action":"auth_success"}`.
4. Client sends `{"action":"subscribe","channels":[{"name":…,"symbols":[…]}]}`.

Every data frame carries a short type tag in `T`: `t` trade, `te` trade extra,
`q` quote, `b` forming candle, `bc` closed candle, `mi` market index, `emi`
estimated VN30, `f` foreign flow, `sd` security definition, `e` expected price,
`s` session. Order (`do`, `eo`) and position (`dp`, `ep`) tags are documented by
DNSE and intentionally unmapped here.

### Channels

| Helper | Channel |
|---|---|
| `trade_channel("G1")` | `tick.G1.json` |
| `quote_channel("G1")` | `top_price.G1.json` |
| `ohlc_channel("1")` | `ohlc.1.json` |
| `ohlc_closed_channel("15")` | `ohlc_closed.15.json` |
| `market_index_channel("VNINDEX")` | `market_index.VNINDEX.json` |
| `foreign_channel("G1")` | `foreign.G1.json` |
| `security_definition_channel("G1")` | `security_definition.G1.json` |
| `session_channel("STO", "G1")` | `session.STO.G1.json` |

Symbols must be upper case. The helpers enforce it.

### Board ids

| Board | Meaning |
|---|---|
| `G1` | round lot |
| `G3` | after-hours (PLO) |
| `G4` | odd lot |
| `T1` / `T3` | negotiated round lot, 09:00–14:45 / 14:45–15:00 |
| `T4` / `T6` | negotiated odd lot, 09:00–14:45 / 14:45–15:00 |

Product groups for the session channel: `STO` HOSE equity, `STX` HNX equity,
`UPX` UPCoM, `FIO` index futures, `FBX` bond futures, `HCX` HNX corporate bonds.

### Staying connected

Two server rules, both handled in `DnseMarketStream`:

* The server pings roughly every three minutes and drops any client that has
  not answered `{"action":"pong"}` within one minute. The client answers on
  receipt and also sends its own ping every 45 seconds — the protocol allows an
  unprompted keepalive, and mobile networks and NATs quietly drop idle frames.
* A connection is capped at eight hours. The client cycles at seven and a half,
  re-authenticating and replaying its subscriptions, so the reconnect happens
  when we choose rather than mid-session.

`websockets` and (for msgpack) `msgpack` are imported lazily. The REST path,
the CLI and the test suite run without them installed.

---

## Troubleshooting

| Symptom | Cause |
|---|---|
| 401/403 on every call | wrong secret, or machine clock skew — the Date header is signed |
| 429 | client-side throttle too high; lower `rate_limit_per_min` in sources.yaml |
| `no credential in $DNSE_API_KEY` in `doctor` | `.env` missing or not filled; the registry skips the source and falls back to fixtures |
| Empty result, no error | the symbol has no bars in that window; check it is upper case and the range covers trading days |
| Bars land on the wrong day | not possible through this provider — dates are computed at UTC+7 regardless of machine timezone |

---

## References

* SDK and examples: <https://github.com/dnse-tech/openapi-sdk> (`python/dnse/api/common.py` is the reference implementation of the REST signature; `python/dnse/websocket/auth.py` of the websocket one)
* Developer docs: <https://developers.dnse.com.vn>

### Security definition timing (observed 2026-10-05/06)

`/price/{symbol}/secdef` holds one definition per symbol: `basicPrice` (reference),
`ceilingPrice`, `floorPrice` (thousands of VND) and `time`, the publication time in
Vietnam time without a zone (`"2026-10-05 20:46:29.147"`). Most symbols get the next
session's definition at about 20:45 the evening before; PVS (HNX) had been published
at 08:00 the same morning. There is no history: a session's values can only be read
between their publication and the next one, which is why `refprices` runs at 15:30
and in `start`. UPCoM's `basicPrice` is the previous session's average matched price
(PHP for 06/10/2026: 49.1 against a 49.5 close).

### Session average price (observed 2026-10-06)

Every record of `/price/{symbol}/trades` carries the running session totals:
`avgPrice` (average matched price, thousands of VND), `totalVolumeTraded`,
`grossTradeAmount` (billions of VND), `openPrice`, `highestPrice`,
`lowestPrice`. The newest trade of a past session therefore gives that
session's average. On UPCoM the next session's reference is that average
rounded half-up to the 100-dong tick: 13/13 monitored UPCoM tickers' 05/10
averages reproduced DNSE's published `basicPrice` for 06/10, and PHP's 02/10
average 47.275 gives the 47.30 reference the board showed for 05/10. Trades
of 02/10 were still served on 06/10; how far back the endpoint goes is not
documented.
