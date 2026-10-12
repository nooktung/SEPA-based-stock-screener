-- vn-stock-agent schema
-- Target: SQLite 3.35+ (dev) and PostgreSQL 14+ (prod).
-- Kept to the common subset of both. No vendor-specific types.
--
-- Design rules enforced here:
--   * Every fact row carries source_id, source_url, source_level, observed_at.
--   * Nothing is stored without provenance. A row with no source is a bug.
--   * assertion_type separates FACT / INFERENCE / ESTIMATE / RUMOUR at the
--     storage layer, so downstream code cannot silently promote a rumour.
--   * Historical signals are append-only. Predictions are never rewritten.

PRAGMA foreign_keys = ON;

-- ==========================================================================
-- REFERENCE
-- ==========================================================================

CREATE TABLE IF NOT EXISTS tickers (
    ticker            TEXT PRIMARY KEY,
    exchange          TEXT NOT NULL,
    name              TEXT,
    sector_profile    TEXT NOT NULL DEFAULT 'generic',
    listed_date       TEXT,
    active            INTEGER NOT NULL DEFAULT 1,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS sources (
    source_id         TEXT PRIMARY KEY,
    kind              TEXT NOT NULL,      -- market_data|news|financial|macro|flow
    source_level      INTEGER NOT NULL,   -- 1..5, see sources.yaml
    event_authority   INTEGER NOT NULL DEFAULT 0,
    enabled           INTEGER NOT NULL DEFAULT 0,
    last_success_at   TEXT,
    last_failure_at   TEXT,
    consecutive_fails INTEGER NOT NULL DEFAULT 0,
    CHECK (source_level BETWEEN 1 AND 5)
);

-- ==========================================================================
-- MARKET DATA
-- ==========================================================================

CREATE TABLE IF NOT EXISTS prices_daily (
    ticker            TEXT NOT NULL,
    trade_date        TEXT NOT NULL,
    open              REAL,
    high              REAL,
    low               REAL,
    close             REAL,
    volume            INTEGER,
    value_vnd         REAL,
    adj_close         REAL,              -- adjusted for splits/bonus/dividends
    adj_factor        REAL DEFAULT 1.0,
    source_id         TEXT NOT NULL,
    observed_at       TEXT NOT NULL,
    PRIMARY KEY (ticker, trade_date),
    FOREIGN KEY (ticker) REFERENCES tickers(ticker) ON DELETE CASCADE,
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
);
CREATE INDEX IF NOT EXISTS idx_prices_date ON prices_daily(trade_date);

CREATE TABLE IF NOT EXISTS index_daily (
    symbol            TEXT NOT NULL,     -- VNINDEX|VN30|HNXINDEX
    trade_date        TEXT NOT NULL,
    open              REAL,
    high              REAL,
    low               REAL,
    close             REAL,
    volume            INTEGER,
    value_vnd         REAL,
    advancers         INTEGER,           -- incl. stocks at the ceiling (DNSE market-index)
    decliners         INTEGER,           -- incl. stocks at the floor
    unchanged         INTEGER,
    source_id         TEXT NOT NULL,
    observed_at       TEXT NOT NULL,
    -- 2026-10-08: end-of-session figures from DNSE /price/{index}/market-index
    -- (src/collectors/index_session.py). `volume` above is the OHLC feed's and
    -- includes put-through deals; matched_volume is what the price boards show.
    matched_volume        INTEGER,
    matched_value_vnd     REAL,
    put_through_volume    INTEGER,
    put_through_value_vnd REAL,
    ceilings          INTEGER,
    floors            INTEGER,
    -- 2026-10-09: the snapshot's highest / lowest index value (match HOSE on 135-136 of 137
    -- sessions; the OHLC bar's high / low above often do not). Reported, not yet used by rules.
    session_high      REAL,
    session_low       REAL,
    session_source_id   TEXT,
    session_observed_at TEXT,
    PRIMARY KEY (symbol, trade_date)
);

-- HOSE's own end-of-day index values (api.hsx.vn basic-chart), a cross-check of index_daily
-- (`python -m src.main index-check`). `open` is HOSE's convention: the previous close.
CREATE TABLE IF NOT EXISTS hose_index_daily (
    symbol        TEXT NOT NULL,
    trade_date    TEXT NOT NULL,
    open          REAL,
    high          REAL,
    low           REAL,
    close         REAL,
    source_id     TEXT NOT NULL,
    observed_at   TEXT NOT NULL,
    PRIMARY KEY (symbol, trade_date)
);

-- Derived technical features. Recomputed deterministically in Python;
-- cached so Claude receives a compact feature vector, not raw OHLCV.
CREATE TABLE IF NOT EXISTS technical_features (
    ticker            TEXT NOT NULL,
    trade_date        TEXT NOT NULL,
    feature_name      TEXT NOT NULL,     -- ma20|rsi14|macd_hist|atr14|rs_vnindex_20...
    value             REAL,
    computed_at       TEXT NOT NULL,
    PRIMARY KEY (ticker, trade_date, feature_name),
    FOREIGN KEY (ticker) REFERENCES tickers(ticker) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_techfeat_lookup ON technical_features(ticker, feature_name, trade_date);

-- ==========================================================================
-- NEWS INTELLIGENCE
-- ==========================================================================

-- Raw article. Many articles may describe one event.
CREATE TABLE IF NOT EXISTS news_articles (
    article_id        TEXT PRIMARY KEY,
    title             TEXT NOT NULL,
    url               TEXT NOT NULL,
    source_id         TEXT NOT NULL,
    source_level      INTEGER NOT NULL,
    published_at      TEXT,
    collected_at      TEXT NOT NULL,
    body_hash         TEXT,              -- for exact-duplicate detection
    simhash           TEXT,              -- for near-duplicate clustering
    language          TEXT DEFAULT 'vi',
    raw_path          TEXT,              -- data/news/... , body not stored in DB
    event_id          TEXT,              -- assigned by the deduplicator
    FOREIGN KEY (source_id) REFERENCES sources(source_id)
);
CREATE INDEX IF NOT EXISTS idx_articles_event ON news_articles(event_id);
CREATE INDEX IF NOT EXISTS idx_articles_published ON news_articles(published_at);

-- The deduplicated unit of meaning. 20 articles -> 1 event.
CREATE TABLE IF NOT EXISTS news_events (
    event_id          TEXT PRIMARY KEY,
    event_type        TEXT NOT NULL,     -- EARNINGS|DIVIDEND|CAPITAL_RAISING|INSIDER|...
    headline          TEXT NOT NULL,
    occurred_at       TEXT,
    first_seen_at     TEXT NOT NULL,
    article_count     INTEGER NOT NULL DEFAULT 1,
    primary_source_id TEXT,
    best_source_level INTEGER NOT NULL,
    assertion_type    TEXT NOT NULL,     -- FACT|INFERENCE|ESTIMATE|RUMOUR
    relevance_score       REAL,
    credibility_score     REAL,
    novelty_score         REAL,
    market_impact_score   REAL,
    company_impact_score  REAL,
    sector_impact_score   REAL,
    time_sensitivity      REAL,
    direction         TEXT,              -- POSITIVE|NEUTRAL|NEGATIVE
    what_happened     TEXT,
    why_it_matters    TEXT,
    expected_impact   TEXT,
    time_horizon      TEXT,              -- INTRADAY|DAYS|WEEKS|QUARTERS
    probability       REAL,              -- NULL when not reliably estimable
    CHECK (assertion_type IN ('FACT','INFERENCE','ESTIMATE','RUMOUR')),
    CHECK (direction IS NULL OR direction IN ('POSITIVE','NEUTRAL','NEGATIVE'))
);
CREATE INDEX IF NOT EXISTS idx_events_type ON news_events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_seen ON news_events(first_seen_at);

CREATE TABLE IF NOT EXISTS event_tickers (
    event_id          TEXT NOT NULL,
    ticker            TEXT NOT NULL,
    role              TEXT NOT NULL DEFAULT 'subject',  -- subject|peer|sector|market
    PRIMARY KEY (event_id, ticker),
    FOREIGN KEY (event_id) REFERENCES news_events(event_id) ON DELETE CASCADE
);

-- ==========================================================================
-- FUNDAMENTALS
-- ==========================================================================

CREATE TABLE IF NOT EXISTS financial_periods (
    ticker            TEXT NOT NULL,
    period_end        TEXT NOT NULL,     -- 2026-06-30
    period_type       TEXT NOT NULL,     -- Q|H|FY
    is_consolidated   INTEGER NOT NULL DEFAULT 1,
    is_audited        INTEGER NOT NULL DEFAULT 0,
    filed_at          TEXT,
    source_id         TEXT NOT NULL,
    source_url        TEXT,
    observed_at       TEXT NOT NULL,
    PRIMARY KEY (ticker, period_end, period_type, is_consolidated)
);

-- Long format: one row per metric. Lets sector-specific metric sets coexist
-- (a securities company's margin_balance has no analogue at a manufacturer)
-- without schema churn.
CREATE TABLE IF NOT EXISTS financial_metrics (
    ticker            TEXT NOT NULL,
    period_end        TEXT NOT NULL,
    period_type       TEXT NOT NULL,
    metric_name       TEXT NOT NULL,     -- revenue|net_income|roe|margin_balance|prop_trading_pnl...
    value             REAL,
    unit              TEXT DEFAULT 'VND',
    assertion_type    TEXT NOT NULL DEFAULT 'FACT',
    source_id         TEXT NOT NULL,
    source_url        TEXT,
    observed_at       TEXT NOT NULL,
    PRIMARY KEY (ticker, period_end, period_type, metric_name),
    CHECK (assertion_type IN ('FACT','INFERENCE','ESTIMATE','RUMOUR'))
);
CREATE INDEX IF NOT EXISTS idx_finmetric_lookup ON financial_metrics(ticker, metric_name, period_end);

CREATE TABLE IF NOT EXISTS valuation_snapshots (
    ticker            TEXT NOT NULL,
    trade_date        TEXT NOT NULL,
    pe_ttm            REAL,
    pe_forward        REAL,
    pb                REAL,
    peg               REAL,
    ev_ebitda         REAL,
    pe_percentile_5y  REAL,              -- vs own history
    pb_percentile_5y  REAL,
    peer_median_pe    REAL,
    peer_median_pb    REAL,
    computed_at       TEXT NOT NULL,
    PRIMARY KEY (ticker, trade_date)
);

-- ==========================================================================
-- MACRO AND FLOW
-- ==========================================================================

CREATE TABLE IF NOT EXISTS macro_series (
    series_id         TEXT NOT NULL,     -- FED_FUNDS_RATE|VN_CPI_YOY|USDVND...
    observation_date  TEXT NOT NULL,
    value             REAL,
    unit              TEXT,
    region            TEXT,              -- US|VN|GLOBAL
    source_id         TEXT NOT NULL,
    source_url        TEXT,
    observed_at       TEXT NOT NULL,
    PRIMARY KEY (series_id, observation_date)
);

CREATE TABLE IF NOT EXISTS flow_daily (
    scope             TEXT NOT NULL,     -- TICKER|SECTOR|MARKET
    scope_key         TEXT NOT NULL,     -- SSI | securities | HOSE
    trade_date        TEXT NOT NULL,
    foreign_buy_value REAL,
    foreign_sell_value REAL,
    foreign_net_value REAL,
    prop_net_value    REAL,
    etf_net_value     REAL,
    total_match_value REAL,
    source_id         TEXT NOT NULL,
    observed_at       TEXT NOT NULL,
    foreign_buy_volume  REAL,            -- shares bought by foreign investors (matched board)
    foreign_sell_volume REAL,
    PRIMARY KEY (scope, scope_key, trade_date)
);

CREATE TABLE IF NOT EXISTS market_regime_history (
    trade_date        TEXT PRIMARY KEY,
    regime            TEXT NOT NULL,
    confidence        REAL,
    breadth_ratio     REAL,
    volume_vs_ma20    REAL,
    foreign_net_20d   REAL,
    volatility_pct    REAL,
    inputs_json       TEXT,              -- full input vector for auditability
    computed_at       TEXT NOT NULL
);

-- ==========================================================================
-- DECISIONS -- APPEND ONLY
-- ==========================================================================

CREATE TABLE IF NOT EXISTS signals (
    signal_id         TEXT PRIMARY KEY,
    ticker            TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    report_type       TEXT NOT NULL,     -- PREMARKET|INTRADAY|EOD
    recommendation    TEXT NOT NULL,     -- STRONG_BUY|BUY|BUY_ON_PULLBACK|HOLD|REDUCE|SELL_AVOID|INSUFFICIENT_DATA
    total_score       REAL,
    confidence        REAL,
    score_fundamental REAL,
    score_technical   REAL,
    score_news        REAL,
    score_regime      REAL,
    score_flow        REAL,
    score_valuation   REAL,
    score_macro       REAL,
    score_risk        REAL,
    entry_low         REAL,
    entry_high        REAL,
    stop_loss         REAL,
    target_1          REAL,
    target_2          REAL,
    risk_reward       REAL,
    market_regime     TEXT,
    main_catalyst     TEXT,
    main_risk         TEXT,
    reasoning         TEXT,
    data_completeness REAL,
    is_speculative    INTEGER NOT NULL DEFAULT 0,
    FOREIGN KEY (ticker) REFERENCES tickers(ticker)
);
CREATE INDEX IF NOT EXISTS idx_signals_ticker_date ON signals(ticker, created_at);

-- Outcome is a separate table so the original signal row is never mutated.
CREATE TABLE IF NOT EXISTS signal_outcomes (
    signal_id         TEXT NOT NULL,
    evaluated_at      TEXT NOT NULL,
    horizon_days      INTEGER NOT NULL,
    price_at_eval     REAL,
    return_pct        REAL,
    max_favourable_pct REAL,
    max_adverse_pct   REAL,
    hit_target_1      INTEGER DEFAULT 0,
    hit_target_2      INTEGER DEFAULT 0,
    hit_stop          INTEGER DEFAULT 0,
    verdict           TEXT,              -- CORRECT|PARTIAL|WRONG|INVALIDATED|OPEN
    notes             TEXT,
    PRIMARY KEY (signal_id, horizon_days),
    FOREIGN KEY (signal_id) REFERENCES signals(signal_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS alerts (
    alert_id          TEXT PRIMARY KEY,
    ticker            TEXT,
    created_at        TEXT NOT NULL,
    alert_type        TEXT NOT NULL,     -- BREAKOUT|BREAKDOWN|VOLUME_ANOMALY|DISCLOSURE|THESIS_INVALIDATION...
    what_changed      TEXT NOT NULL,
    why_it_matters    TEXT,
    previous_thesis   TEXT,
    thesis_still_valid INTEGER,
    suggested_action  TEXT,
    related_signal_id TEXT,
    related_event_id  TEXT
);

-- ==========================================================================
-- RESEARCH MEMORY
-- ==========================================================================

CREATE TABLE IF NOT EXISTS theses (
    thesis_id         TEXT PRIMARY KEY,
    ticker            TEXT NOT NULL,
    created_at        TEXT NOT NULL,
    last_verified_at  TEXT NOT NULL,
    status            TEXT NOT NULL,     -- ACTIVE|WEAKENED|INVALIDATED|CLOSED
    summary           TEXT NOT NULL,
    invalidation_conditions TEXT NOT NULL,
    source_url        TEXT,
    superseded_by     TEXT,
    FOREIGN KEY (ticker) REFERENCES tickers(ticker) ON DELETE CASCADE,
    CHECK (status IN ('ACTIVE','WEAKENED','INVALIDATED','CLOSED'))
);
CREATE INDEX IF NOT EXISTS idx_theses_ticker_status ON theses(ticker, status);

-- Staleness watchdog: anything not re-verified within its TTL is downgraded
-- rather than silently trusted.
CREATE TABLE IF NOT EXISTS data_freshness (
    dataset           TEXT NOT NULL,
    scope_key         TEXT NOT NULL,
    last_updated_at   TEXT,
    ttl_hours         INTEGER NOT NULL,
    is_stale          INTEGER NOT NULL DEFAULT 0,
    note              TEXT,              -- why the series is flagged (2026-10-11)
    PRIMARY KEY (dataset, scope_key)
);

CREATE TABLE IF NOT EXISTS collection_runs (
    run_id            TEXT PRIMARY KEY,
    started_at        TEXT NOT NULL,
    finished_at       TEXT,
    job_type          TEXT NOT NULL,     -- MORNING|INTRADAY|CLOSING
    status            TEXT NOT NULL,     -- OK|PARTIAL|FAILED
    sources_ok        INTEGER DEFAULT 0,
    sources_failed    INTEGER DEFAULT 0,
    failure_detail    TEXT
);

-- ==========================================================================
-- PORTFOLIO (schema only, not wired in MVP)
-- ==========================================================================

CREATE TABLE IF NOT EXISTS portfolio_positions (
    position_id       TEXT PRIMARY KEY,
    ticker            TEXT NOT NULL,
    quantity          REAL NOT NULL,
    average_cost      REAL NOT NULL,
    opened_at         TEXT NOT NULL,
    closed_at         TEXT,
    risk_level        TEXT,
    notes             TEXT,
    FOREIGN KEY (ticker) REFERENCES tickers(ticker)
);

-- ==========================================================================
-- SEPA SCREEN RUNS (focus list). Append-only, like `signals`: a past screen is
-- evidence to be graded later (Phase 13), so it is never rewritten.
-- ==========================================================================

CREATE TABLE IF NOT EXISTS screen_runs (
    run_id            TEXT PRIMARY KEY,
    created_at        TEXT NOT NULL,
    as_of_date        TEXT NOT NULL,     -- last session the screen could see
    params_json       TEXT NOT NULL,     -- the sepa_screen config used
    funnel_json       TEXT NOT NULL,     -- counts surviving each stage
    market_json       TEXT,              -- market-direction gate at screen time
    note              TEXT
);

CREATE TABLE IF NOT EXISTS screen_members (
    run_id            TEXT NOT NULL,
    ticker            TEXT NOT NULL,
    grp               TEXT NOT NULL,     -- A = 8/8 Trend Template, B = near-pass, C = fill to the list size (not SEPA-qualified)
    rank              INTEGER NOT NULL,
    score             REAL NOT NULL,
    exchange          TEXT,
    close             REAL,
    rs_rating         REAL,
    criteria_json     TEXT NOT NULL,     -- each Trend Template criterion: value, threshold, pass
    base_json         TEXT,              -- base type, depths, length, pivot, dry-up
    pivot             REAL,
    stop              REAL,
    risk_to_stop_pct  REAL,
    locked_worst_case_pct REAL,          -- T+2 lock from the reference price
    liquidity_vnd     REAL,              -- estimated 20-session average traded value
    PRIMARY KEY (run_id, ticker),
    FOREIGN KEY (run_id) REFERENCES screen_runs(run_id),
    CHECK (grp IN ('A','B','C'))
);
CREATE INDEX IF NOT EXISTS idx_screen_members_ticker ON screen_members(ticker);

CREATE TRIGGER IF NOT EXISTS screen_runs_append_only
BEFORE UPDATE ON screen_runs
BEGIN SELECT RAISE(ABORT, 'screen_runs is append-only'); END;

CREATE TRIGGER IF NOT EXISTS screen_members_append_only
BEFORE UPDATE ON screen_members
BEGIN SELECT RAISE(ABORT, 'screen_members is append-only'); END;

-- Intraday alerts (Phase 11, src/collectors/intraday_watch.py): a matched trade of a monitored
-- ticker reaching its pivot / stop / MA50 during the session. One per ticker, type and day.
-- Append-only; display only (never an order, never a buy / sell instruction).
CREATE TABLE IF NOT EXISTS intraday_alerts (
    alert_id      TEXT PRIMARY KEY,          -- session_date:ticker:alert_type
    session_date  TEXT NOT NULL,
    ticker        TEXT NOT NULL,
    alert_type    TEXT NOT NULL CHECK (alert_type IN ('PIVOT_CROSS','STOP_TOUCH','MA50_LOST','BREAKOUT_UNDO')),
    price         REAL NOT NULL,             -- VND
    level         REAL NOT NULL,             -- the pivot / stop / MA50 it reached
    traded_at     TEXT NOT NULL,             -- the trade's time (Vietnam)
    volume        REAL,                      -- the session's matched volume so far
    volume_pace   REAL,                      -- volume / (50-session average x share of the day elapsed)
    what_changed  TEXT NOT NULL,
    source_id     TEXT NOT NULL,
    source_level  INTEGER NOT NULL,
    observed_at   TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_intraday_alerts_day ON intraday_alerts(session_date);

CREATE TRIGGER IF NOT EXISTS intraday_alerts_append_only
BEFORE UPDATE ON intraday_alerts
BEGIN SELECT RAISE(ABORT, 'intraday_alerts is append-only'); END;

-- Daily computed state of each monitored ticker. Recomputable from bars at any
-- time; stored so a report can show how a name has evolved.
CREATE TABLE IF NOT EXISTS monitor_state (
    ticker            TEXT NOT NULL,
    trade_date        TEXT NOT NULL,
    state_json        TEXT NOT NULL,
    computed_at       TEXT NOT NULL,
    PRIMARY KEY (ticker, trade_date)
);

-- One row per ticker: the last attempt to read its quarterly statements, so a
-- rejected layout (bank / securities on CafeF) is reported with its reason
-- instead of looking like missing data.
CREATE TABLE IF NOT EXISTS financial_fetch_log (
    ticker            TEXT PRIMARY KEY,
    source_id         TEXT NOT NULL,
    checked_at        TEXT NOT NULL,
    accepted_quarters INTEGER NOT NULL,
    latest_quarter    TEXT,
    rejected_reason   TEXT
);

-- Industry classification per ticker (Vietstock sector tree: 2 / 4 / 6-digit codes).
-- Reference data, refreshed monthly; overwritten in place (it is not a signal).
CREATE TABLE IF NOT EXISTS ticker_industry (
    ticker          TEXT PRIMARY KEY,
    sector_code     TEXT NOT NULL,
    sector          TEXT NOT NULL,
    group_code      TEXT,
    industry_group  TEXT,
    industry_code   TEXT,
    industry        TEXT,
    source_id       TEXT NOT NULL,
    source_url      TEXT,
    observed_at     TEXT NOT NULL
);

-- Top liquid stocks of each industry in a screen run ("Đầu ngành" tab). Monitored
-- with the focus list; not SEPA candidates unless they are also in groups A/B.
CREATE TABLE IF NOT EXISTS screen_industry_leaders (
    run_id          TEXT NOT NULL REFERENCES screen_runs(run_id),
    industry_code   TEXT NOT NULL,
    industry        TEXT NOT NULL,
    rank            INTEGER NOT NULL,          -- 1 = most traded in the industry
    ticker          TEXT NOT NULL,
    grp             TEXT,                      -- A/B/C when also in the focus list
    liquidity_vnd   REAL,
    detail_json     TEXT,
    PRIMARY KEY (run_id, industry_code, ticker)
);

CREATE TRIGGER IF NOT EXISTS screen_industry_leaders_append_only
BEFORE UPDATE ON screen_industry_leaders
BEGIN SELECT RAISE(ABORT, 'screen_industry_leaders is append-only'); END;

-- The user's own picks ("Danh mục của tôi"), added from the dashboard. Monitored
-- and given news with the focus list until removed. Not SEPA candidates as such.
-- SSI FastConnect Data end-of-day snapshots, kept apart from the DNSE tables:
-- a cross-check source, never the primary series (src/collectors/ssi_collect.py).
-- Reference / ceiling / floor of one session, read from the definition DNSE publishes the
-- evening before (src/collectors/refprices.py). VND. The dashboard's % change is taken
-- against `reference`, as the price boards do (UPCoM: previous session's average price).
CREATE TABLE IF NOT EXISTS reference_prices (
    ticker        TEXT NOT NULL,
    session_date  TEXT NOT NULL,      -- the session these values apply to
    reference     REAL NOT NULL,
    ceiling       REAL NOT NULL,
    floor         REAL NOT NULL,
    published_at  TEXT NOT NULL,      -- secdef: DNSE's own timestamp (Vietnam time); avg_price: the session averaged
    source_id     TEXT NOT NULL,
    observed_at   TEXT NOT NULL,
    method        TEXT NOT NULL DEFAULT 'secdef',   -- secdef (published) | avg_price (UPCoM: prior session's average)
    PRIMARY KEY (ticker, session_date)
);

-- Rescalings of a ticker's price history found by the backfill: DNSE adjusts its whole history
-- for share-based corporate actions the evening before the ex-date. Prices the exchange published
-- for sessions up to `through_session` (reference / ceiling / floor, the tape's average) are on the
-- old scale; multiply by `factor` to compare them with the stored bars. Stored references stay as
-- published (src/collectors/refprices.py applies the factor when reading).
CREATE TABLE IF NOT EXISTS price_adjustments (
    ticker           TEXT NOT NULL,
    through_session  TEXT NOT NULL,     -- the last session on the old scale (the day before the ex-date)
    factor           REAL NOT NULL,     -- new / old close
    source_id        TEXT NOT NULL,
    observed_at      TEXT NOT NULL,
    PRIMARY KEY (ticker, through_session)
);

CREATE TABLE IF NOT EXISTS ssi_stock_daily (
    trade_date          TEXT NOT NULL,
    ticker              TEXT NOT NULL,
    market              TEXT,
    ref_price           REAL,
    ceiling_price       REAL,
    floor_price         REAL,
    open                REAL,
    high                REAL,
    low                 REAL,
    close               REAL,
    close_adjusted      REAL,
    avg_price           REAL,
    match_volume        REAL,      -- matched board ("khớp lệnh")
    match_value         REAL,
    deal_volume         REAL,      -- put-through ("thỏa thuận")
    deal_value          REAL,
    foreign_buy_volume  REAL,
    foreign_sell_volume REAL,
    foreign_buy_value   REAL,
    foreign_sell_value  REAL,
    foreign_room        REAL,
    source_id           TEXT NOT NULL,
    observed_at         TEXT NOT NULL,
    PRIMARY KEY (trade_date, ticker)
);

CREATE TABLE IF NOT EXISTS ssi_index_daily (
    trade_date      TEXT NOT NULL,
    index_id        TEXT NOT NULL,     -- project name: VNINDEX, HNXINDEX, UPCOMINDEX, VN30
    value           REAL,
    change          REAL,
    change_pct      REAL,
    match_volume    REAL,
    match_value     REAL,
    deal_volume     REAL,
    deal_value      REAL,
    total_volume    REAL,
    total_value     REAL,
    advances        INTEGER,
    declines        INTEGER,
    unchanged       INTEGER,
    ceilings        INTEGER,
    floors          INTEGER,
    session         TEXT,
    source_id       TEXT NOT NULL,
    observed_at     TEXT NOT NULL,
    PRIMARY KEY (trade_date, index_id)
);

-- Whole-market snapshot for the dashboard's "Toàn thị trường" view (src/quant/market_view.py).
-- Display only; rebuilt by `python -m src.main market`, the last few dates kept.
CREATE TABLE IF NOT EXISTS market_snapshot (
    as_of_date  TEXT NOT NULL,        -- newest price date in the snapshot
    ticker      TEXT NOT NULL,
    row_json    TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    PRIMARY KEY (as_of_date, ticker)
);

CREATE TABLE IF NOT EXISTS user_watchlist (
    ticker      TEXT PRIMARY KEY,
    added_at    TEXT NOT NULL,
    note        TEXT
);
