# SSI FastConnect Data — cross-check source

Read from https://guide.ssi.com.vn/ssi-products/tieng-viet/fastconnect-data on 2026-10-04.
Code: `src/collectors/providers/ssi_fcdata_provider.py`, `src/collectors/ssi_collect.py`,
`src/collectors/crosscheck.py`. Source entry: `config/sources.yaml` → `crosscheck: ssi_fcdata`.

## Role

DNSE stays the primary series (history, stock-dividend-adjusted closes, indicators).
SSI is a **cross-check** source kind: the backfill and the runner never read it
(`src/config.py` `REQUIRED_KINDS`). Its rows live in `ssi_stock_daily` and `ssi_index_daily`.

| Need | SSI endpoint | Why SSI |
|---|---|---|
| Whole-exchange end-of-day snapshot | `DailyStockPrice?market=HOSE&fromDate=d&toDate=d` | ≤ 1,000 rows per page: one call per exchange instead of one per ticker |
| Matched vs put-through volume | same, `TotalMatchVol` / `TotalDealVol` | DNSE gives one volume |
| Reference / ceiling / floor price | same, `RefPrice`, `CeilingPrice`, `FloorPrice` | ex-dates, including cash dividends |
| Foreign buy / sell, room | same, `ForeignBuyVolTotal`, `ForeignSellVolTotal`, `ForeignCurrentRoom` | all tickers in the same call |
| Index matched vs put-through, breadth | `DailyIndex?indexId=VNIndex` | DNSE's VN-Index volume mixes the two |
| History for the chart audit | `DailyOhlc?symbol=…` | official alternative to VNDirect |

Index codes differ from DNSE's: `VNIndex`, `HNXIndex`, `UpcomIndex`, `VN30` (mapped in `index_codes`).

## Access

- Free for SSI account holders according to third-party guides; SSI's pages state no fee.
  Registration at an SSI branch or through a broker, then iBoard → **Dịch vụ API** to create
  a key. The service runs for a fixed term and must be renewed (SSI's docs: 1 year).
- Only **ConsumerID** and **ConsumerSecret** are used (`.env`: `SSI_CONSUMER_ID`,
  `SSI_CONSUMER_SECRET`). The **PrivateKey** signs orders and is never read.
- SSI mentions registering a fixed IP on iBoard; ask the broker whether the data service needs it.
- Rate limit is per key and unpublished ("API calls quota exceeded! maximum admitted x per y s");
  the client waits `quota_waits_seconds` and then gives up — no value is ever filled in.

## Protocol

- `POST {base}/{path}AccessToken` with `{"consumerID", "consumerSecret"}` → `data.accessToken`;
  then `GET {base}/{path}{Endpoint}` with `Authorization: Bearer …`. A 401 renews the token once.
- The docs give both `api/v2/Market/` and `v2.0/Market/`; the first path that issues a token is kept.
- Paging: `pageIndex` 1..10, `pageSize` 10/20/50/100/1000. Dates `dd/mm/yyyy`.
- Field names come in mixed case (`TotalMatchVol`, `Totalmatchvol`); read case-insensitively.
  Numbers are strings; an empty field stays missing, never 0.
- Units: the documented samples quote full VND (`"28600"`), so `price_scale: 1.0`
  **[chưa xác minh trực tiếp]** — the first live `crosscheck` compares every close with DNSE.

## Safety

Data only. `ALLOWED_ENDPOINTS` lists the token call and the eight `Market/*` reads; any other
endpoint, method or host (FastConnect Trading's included) raises `PermissionError` before a
request is sent (`tests/test_ssi_fcdata.py`). The project never places orders.

## Phases (user decision, 2026-10-04)

A. Client, storage, `ssi` / `crosscheck` commands, tests — done; nothing changes the DNSE tables.
B. With keys: the 15:30 job runs `ssi --days 3` and `crosscheck --days 30`; review the reports for ~2 weeks.
C. Only after the user agrees: daily whole-market update from the SSI snapshot (DNSE re-fetch only
   for tickers with an ex-date), foreign flow from SSI, distribution days on SSI's matched index volume.
