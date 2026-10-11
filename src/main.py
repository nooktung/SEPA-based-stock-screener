"""Entry point.

Usage:
    python -m src.main doctor     # inspect environment and validate config
    python -m src.main initdb     # create schema and sync reference data
    python -m src.main universe   # fetch HOSE/HNX/UPCOM share list from DNSE into tickers
    python -m src.main backfill [--tickers SSI VIX] [--recheck-gaps] [--deepen]   # incremental daily bars
    python -m src.main screen [--as-of 2026-09-25] [--dry-run]       # SEPA screen -> focus list (<= 80)
    python -m src.main monitor [--no-fetch] [--keep-events]          # daily check of the focus list
    python -m src.main news [--days 30] [--tickers SSI] [--no-rss]   # collect + dedupe news for the focus list
    python -m src.main news-brief SSI [--days 30]                    # <= 7 ranked events, for in-session reading
    python -m src.main news-annotate <event_id> <file.json>          # store a checked interpretation
    python -m src.main start [--no-update] [--plan] [--update-only] [--port 8765] [--no-browser]  # local web dashboard
    python -m src.main backtest-sepa [--limit 50] [--step 5]         # test the SEPA rules on 5 years of VN data
    python -m src.main backtest-swing [--limit 50]                   # short-term setups (<= 2 weeks), VN rules
    python -m src.main fundamentals [--tickers HPG] [--refresh]      # quarterly income from CafeF + growth grade
    python -m src.main industry [--tickers HPG] [--refresh]          # industry classification from Vietstock
    python -m src.main flow [--days 30] [--tickers PHP]              # foreign buy / sell per session (DNSE)
    python -m src.main logos [--tickers PHP] [--all] [--refresh]     # company logos for the dashboard (Vietstock)
    python -m src.main market                                        # whole-market snapshot for the dashboard
    python -m src.main refprices [--tickers PHP] [--all] [--market]  # reference / ceiling / floor per session (DNSE)
    python -m src.main ssi [--days 5]                                # SSI FastConnect Data snapshots (cross-check only)
    python -m src.main crosscheck [--days 10]                        # DNSE vs SSI: closes, volumes, foreign, index, ex-dates
    python -m src.main index-session [--days 300]                    # VN-Index matched volume, breadth + DNSE calendar
    python -m src.main index-check [--sessions 60] [--no-fetch]      # VN-Index bars vs HOSE's own values (report only)
    python -m src.main check                                         # invariants on what the dashboard shows (report)
"""

from __future__ import annotations

import sys

from src.config import ConfigError, load_config
from src.database import init_db, sync_reference_data


def doctor() -> int:
    print("vn-stock-agent :: environment check\n")
    ok = True

    try:
        cfg = load_config()
        print(f"  config          OK   {len(cfg.watchlist.active_tickers)} active tickers, "
              f"{len(cfg.sources.specs)} sources declared")
        print(f"  watchlist       {', '.join(cfg.watchlist.active_tickers)}")
        print(f"  benchmarks      {', '.join(cfg.watchlist.benchmarks)}")
        weights = cfg.thresholds.scoring_weights
        print(f"  score weights   sum={sum(weights.values())} {weights}")
    except ConfigError as exc:
        print(f"  config          FAIL {exc}")
        return 1

    live_by_kind = {}
    for kind in ("market_data", "news", "financial", "macro", "flow"):
        specs = cfg.sources.for_kind(kind)
        live = [s.id for s in specs if not s.id.startswith("fixture")]
        live_by_kind[kind] = live
        status = "LIVE" if live else "FIXTURE ONLY"
        print(f"  source:{kind:<12} {status:<12} enabled={[s.id for s in specs]}")

    for spec in cfg.sources.for_kind("crosscheck", enabled_only=False):
        state = ("LIVE" if spec.has_credentials else f"chưa có khóa (${spec.auth_env})") if spec.enabled else "tắt"
        print(f"  source:crosscheck   {spec.id:<12} {state} -- chỉ để đối chiếu, không thay nguồn chính")

    if not any(live_by_kind.values()):
        print("\n  WARNING: every enabled source is a fixture. Reports are NOT publishable.")
        ok = False

    return 0 if ok else 2


def initdb() -> int:
    path = init_db()
    tickers, sources = sync_reference_data()
    print(f"database initialised at {path}")
    print(f"  tickers synced  {tickers}")
    print(f"  sources synced  {sources}")
    return 0


def universe() -> int:
    from collections import Counter

    from src.collectors.base import ProviderError
    from src.collectors.providers.dnse_client import DnseCredentials, DnseHttpClient
    from src.collectors.universe import build_universe, sync_universe
    from src.env import load_dotenv

    load_dotenv()
    try:
        members = build_universe(DnseHttpClient(DnseCredentials.from_env()))
    except (ConfigError, ProviderError) as exc:
        print(f"universe FAIL {exc}")
        return 1
    init_db()
    stats = sync_universe(members)
    by_exchange = Counter(m.exchange for m in members)
    print(f"universe  {len(members)} shares  " + "  ".join(f"{k}={v}" for k, v in sorted(by_exchange.items())))
    print(f"  tickers table  added={stats['added']} deactivated={stats['deactivated']}")
    return 0


def backfill(args: list[str]) -> int:
    import argparse
    import time

    from src.collectors.backfill import run_backfill, universe_tickers
    from src.collectors.base import ProviderError
    from src.env import load_dotenv

    parser = argparse.ArgumentParser(prog="python -m src.main backfill")
    parser.add_argument("--tickers", nargs="*", help="limit to these tickers (default: every active ticker)")
    parser.add_argument("--recheck-gaps", action="store_true",
                        help="recompute band-breaking gap flags from stored bars; no network")
    parser.add_argument("--deepen", action="store_true",
                        help="read the whole backfill_history_days window once for series stored with less (~30 min)")
    opts = parser.parse_args(args)

    load_dotenv()
    init_db()
    if opts.recheck_gaps:
        from src.collectors.backfill import recheck_gaps

        stats = recheck_gaps()
        print(f"gap recheck  flagged={stats['flagged']}  clean={stats['clean']}")
        return 0
    scope = universe_tickers()
    if opts.tickers:
        wanted = {t.upper() for t in opts.tickers}
        scope = [(t, ex) for t, ex in scope if t in wanted]
        unknown = wanted - {t for t, _ in scope}
        if unknown:
            print(f"not in the tickers table (run `universe` first): {sorted(unknown)}")
    started = time.monotonic()

    def progress(done: int, total: int, outcome) -> None:
        if done % 50 == 0 or done == total or outcome.stale:
            rate = done / max(time.monotonic() - started, 1e-9) * 60
            flag = f"  !! {outcome.scope}: {outcome.note[:110]}" if outcome.stale else ""
            print(f"  [{done:>5}/{total}] {rate:5.0f}/min{flag}", flush=True)

    try:
        if opts.deepen:
            from src.collectors.backfill import deepen_history

            report = deepen_history(scope, progress=progress)
            added = [o for o in report.outcomes if o.persisted]
            print(f"Lịch sử sâu hơn cho {len(added)} mã · {sum(o.bars for o in added):,} phiên đã ghi · "
                  f"{sum(1 for o in report.outcomes if o.stale)} mã không dùng được lượt đọc · "
                  f"{(time.monotonic() - started) / 60:.1f} phút")
            return 0 if report.outcomes and not all(o.stale for o in report.outcomes) else 1
        report = run_backfill(tickers=scope, progress=progress)
    except (ConfigError, ProviderError) as exc:
        print(f"backfill FAIL {exc}")
        return 1
    ok = sum(1 for o in report.outcomes if o.ok)
    stale = [o for o in report.outcomes if o.stale]
    print(f"backfill {report.status}  ok={ok}  stale={len(stale)}  bars={report.bars_written}  "
          f"in {(time.monotonic() - started) / 60:.1f} min")
    return 0 if not stale else 3


def screen(args: list[str]) -> int:
    import argparse
    import csv
    from datetime import date
    from pathlib import Path

    from src.market_calendar import price_limit_pct
    from src.quant.screener import (LIQUIDITY_TIER_TEXT, liquidity_tier, locked_worst_case_pct, run_screen,
                                    screen_settings)
    from src.quant.setup_status import market_note

    parser = argparse.ArgumentParser(prog="python -m src.main screen")
    parser.add_argument("--as-of", type=date.fromisoformat, help="screen as the market stood on this date")
    parser.add_argument("--dry-run", action="store_true", help="do not store the run")
    opts = parser.parse_args(args)

    init_db()
    result = run_screen(as_of=opts.as_of, persist=not opts.dry_run)
    settings = screen_settings()
    m = result.market
    print(f"SEPA screen {result.run_id}  as of {result.as_of}  (ứng viên nghiên cứu -- không phải khuyến nghị mua)")
    print(f"  thị trường  {m.get('status')}  {m.get('index', '')} {m.get('close', '')}  "
          f"MA50 {m.get('ma50', '')}  MA200 {m.get('ma200', '')}  "
          f"ngày phân phối {m.get('distribution_days', '?')}/{m.get('window', '?')}"
          + _volume_basis_text(m.get("volume_basis")))
    print("  phễu  " + "  ->  ".join(f"{k}={v}" for k, v in result.funnel.items()))
    for reason, n in sorted(result.exclusions.items(), key=lambda kv: -kv[1]):
        print(f"        loại {n:>5}  {reason}")
    ftd = m.get("ftd") or {}
    if ftd.get("date"):
        print(f"  FTD {ftd['date']} (+{ftd.get('gain_pct')}%)"
              + (f", {ftd.get('distribution_since')} ngày phân phối từ đó" if ftd.get("distribution_since") is not None else ""))
    elif ftd.get("last_ftd"):
        lf = ftd["last_ftd"]
        print(f"  FTD {lf['date']} " + ("thất bại" if lf["outcome"] == "failed" else f"hết hiệu lực ({lf['distribution_since']} ngày phân phối)")
              + f" ngày {lf['on']}; trạng thái: {ftd.get('state_text', ftd.get('state'))}")
    level, note = market_note(m)
    print(f"  >> {note}")
    header = (f"{'#':>3} {'N':1} {'Mã':<4} {'Sàn':<5} {'Giá':>9} {'RS':>3} {'Tầng':<6} {'TT':>3} {'Thiếu':<12} "
              f"{'Nền':<10} {'Pivot':>9} {'Cách':>6} {'Trạng thái':<14} {'Rủi ro':>6} {'T+2':>5} {'GTGD':>7} {'TK':<4} {'Điểm':>5} "
              f"{'CB':<7} {'Ngành (RS)':<22}")
    tier_short = {"STRONG_LEADER": "Mạnh", "LEADER": "Dẫn", "QUALIFIED": "Đạt", None: "-"}
    rows = []

    def industry_cell(row) -> str:
        if not row["industry"]:
            return "-"
        rs = "-" if row["industry_rs"] is None else f"{row['industry_rs']:.0f}"
        return f"{row['industry'][:17]} ({rs})"

    def table(title: str, members) -> None:
        print(f"\n{title}")
        print(header)
        print("-" * len(header))
        for c in members:
            b = c.base
            rank = result.members.index(c) + 1
            risk = (b.pivot - b.stop) / b.pivot * 100 if b.pivot and b.stop else None
            band = price_limit_pct(c.exchange)
            locked = locked_worst_case_pct(band) if band else None
            status = (c.setup or {}).get("status_text", "")
            row = {
                "rank": rank, "group": c.group, "ticker": c.ticker, "exchange": c.exchange, "close": c.close,
                "rs": c.rs_rating, "rs_tier": c.rs_tier, "tt_passed": c.passed, "missing": ",".join(c.failed),
                "base": b.kind, "pivot": b.pivot, "to_pivot_pct": b.distance_to_pivot_pct, "status": status,
                "entry": (c.setup or {}).get("entry_text"), "risk_pct": risk, "locked_t2_pct": locked,
                "value_bn": c.liquidity_vnd / 1e9, "score": c.score,
                "liquidity_tier": LIQUIDITY_TIER_TEXT.get(liquidity_tier(c.liquidity_vnd, settings), "-"),
                "contractions": "/".join(f"{x:.0f}" for x in b.contractions),
                "fundamental_grade": (c.fundamentals or {}).get("grade"),
                "industry": (c.industry or {}).get("name"), "industry_rs": (c.industry or {}).get("rs_percentile"),
            }
            rows.append(row)
            # A missing value keeps the column's width so the table stays aligned.
            f = lambda v, fmt: format("-", fmt.split(",")[0].split(".")[0].replace("+", "")) if v is None else format(v, fmt)  # noqa: E731
            print(f"{rank:>3} {c.group:1} {c.ticker:<4} {c.exchange:<5} {c.close:>9,.0f} {f(c.rs_rating, '>3.0f')} "
                  f"{tier_short[c.rs_tier]:<6} {c.passed:>3} {row['missing'][:12]:<12} {b.kind[:10]:<10} "
                  f"{f(b.pivot, '>9,.0f')} {f(b.distance_to_pivot_pct, '>+6.1f')} {status[:14]:<14} "
                  f"{f(risk, '>6.1f')} {f(locked, '>5.1f')} {row['value_bn']:>7.1f} {row['liquidity_tier']:<4} {c.score:>5.1f} "
                  f"{(row['fundamental_grade'] or '-'):<7} {industry_cell(row):<22}")

    main_list = [c for c in result.members if c.group in ("A", "B")]
    extended = [c for c in result.members if c.group == "C"]
    table(f"DANH SÁCH CHÍNH ({len(main_list)} mã: A = đạt 8/8, B = gần đạt)", main_list)
    demoted = [c for c in result.members if c.demoted]
    if demoted:
        print("\nBị loại khỏi danh sách chính vì cơ bản FAIL:")
        for c in demoted:
            print(f"  {c.ticker}: {c.demoted}")
    if extended:
        table(f"THEO DÕI MỞ RỘNG ({len(extended)} mã, nhóm C -- đang trên MA50/150/200 nhưng CHƯA đạt chuẩn SEPA, không phải ứng viên)", extended)
    out_dir = Path("data/screens")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"screen_{result.as_of.isoformat()}_{result.run_id}.csv"
    with out.open("w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["rank"])
        writer.writeheader()
        writer.writerows(rows)
    if result.flagged_liquid:
        print("\nĐủ thanh khoản nhưng bị loại vì chuỗi giá có biến động vượt biên độ (cần người kiểm tra;")
        print("thường là chuyển sàn -- nếu đúng, thêm vào sepa_screen.gap_waivers kèm lý do):")
        print("  " + ", ".join(f"{t} ({ex}, {v / 1e9:.1f} tỷ)" for t, ex, v in result.flagged_liquid))
    if result.themes:
        print("\nSÓNG NGÀNH (config/themes.yaml; RS nhóm so với mọi ngành, ≥ 80 = đang mạnh)")
        for t in result.themes:
            rs = "-" if t["rs_percentile"] is None else f"{t['rs_percentile']:.0f}"
            r1 = "-" if t["return_1m"] is None else f"{t['return_1m']:+.1f}%"
            r3 = "-" if t["return_3m"] is None else f"{t['return_3m']:+.1f}%"
            print(f"  {rs:>3} {'MẠNH' if t['hot'] else '    '} {t['name'][:24]:<24} {t['count']:>2}/{t['configured']:<2} mã  "
                  f"1T {r1:>7}  3T {r3:>7}  A/B: {', '.join(t['leaders']) or '-'}")
        hot_c = [c.ticker for c in result.members if c.group == "C" and any(x["hot"] for x in c.themes)]
        if hot_c:
            print(f"  Nhóm C thuộc sóng đang mạnh (xếp đầu nhóm C): {', '.join(hot_c)}")
    ranked = [i for i in result.industries if i.get("rs_percentile") is not None]
    if ranked:
        print(f"\nNGÀNH MẠNH NHẤT (RS ngành trong {len(ranked)} ngành có đủ mã thanh khoản; chỉ hiển thị, không tính điểm)")
        for i in ranked[:8]:
            ma50 = "-" if i["above_ma50_pct"] is None else f"{i['above_ma50_pct']:.0f}"
            print(f"  {i['rs_percentile']:>3.0f}  {i['name'][:34]:<34} {i['count']:>3} mã  trên MA50 {ma50}%  "
                  f"A/B: {', '.join(i['leaders']) or '-'}")
    elif "classified_liquid" in result.funnel:
        print("\nChưa có phân ngành (chạy: python -m src.main industry)")
    print(f"\nCSV: {out}")
    print("Tầng RS: Mạnh ≥ 90, Dẫn 80-89, Đạt 70-79. Cách = % so với pivot (âm: dưới pivot). Trạng thái: "
          "Ở vùng pivot / Đang theo dõi / Vừa breakout / Mất hiệu lực -- không phải tín hiệu mua. "
          "Rủi ro = pivot tới stop. T+2 = lỗ nếu giảm sàn liên tiếp tới chiều T+2 (không phải giới hạn trên). "
          "TK = hạng thanh khoản: Cao ≥ 10 tỷ, Khá 8-10 tỷ, Thấp 3-8 tỷ/phiên (Thấp: khó mua bán lượng lớn, dễ kẹt khi giảm sàn).")
    return 0


def monitor(args: list[str]) -> int:
    import argparse
    from pathlib import Path

    from src.collectors.backfill import run_backfill
    from src.collectors.base import ProviderError
    from src.database import connect
    from src.env import load_dotenv
    from src.quant.monitor import monitored_tickers, render_brief, run_monitor

    parser = argparse.ArgumentParser(prog="python -m src.main monitor")
    parser.add_argument("--no-fetch", action="store_true", help="use stored bars; do not call DNSE")
    parser.add_argument("--keep-events", action="store_true",
                        help="recompute the states, keep the session's stored events (re-run after a rule change)")
    opts = parser.parse_args(args)

    load_dotenv()
    init_db()
    tickers, _, _ = monitored_tickers()
    if not opts.no_fetch:
        with connect() as conn:
            exchanges = {r[0]: r[1] for r in conn.execute("SELECT ticker, exchange FROM tickers")}
        scope = [(t, exchanges[t]) for t in tickers if t in exchanges]
        try:
            report = run_backfill(tickers=scope)
        except (ConfigError, ProviderError) as exc:
            print(f"cập nhật giá thất bại ({exc}); dùng dữ liệu đã lưu")
        else:
            stale = [o.scope for o in report.outcomes if o.stale]
            print(f"cập nhật giá {len(scope)} mã + chỉ số: {report.status}"
                  + (f"  (stale: {', '.join(stale)})" if stale else ""))

    result = run_monitor(keep_events=opts.keep_events)
    brief = render_brief(result)
    out_dir = Path("data/briefs")
    out_dir.mkdir(parents=True, exist_ok=True)
    out = out_dir / f"monitor_{result.as_of}.md"
    out.write_text(brief, encoding="utf-8")

    m = result.market
    print(f"Theo dõi {len(result.states)}/{len(result.tickers)} mã, phiên {result.as_of}  "
          f"thị trường {m.get('status')} (hôm trước {result.market_before.get('status', '?')})")
    if result.missing:
        print("  thiếu dữ liệu: " + ", ".join(result.missing))
    if not result.events:
        print("  không có sự kiện mới")
    for e in sorted(result.events, key=lambda e: (e.event_type, e.ticker)):
        print(f"  {e.ticker:<4} {e.event_type:<24} {e.what_changed}")
    print(f"\nTóm tắt: {out}")
    return 0


def news(args: list[str]) -> int:
    import argparse
    from datetime import datetime, timedelta, timezone

    from src.collectors.base import ProviderError
    from src.collectors.providers.web_news import PoliteFetcher, extract_body
    from src.collectors.registry import instantiate
    from src.processors.news_pipeline import news_settings, process_articles, reclassify_roles, retag_ambiguous
    from src.database import connect
    from src.quant.monitor import monitored_tickers

    parser = argparse.ArgumentParser(prog="python -m src.main news")
    parser.add_argument("--days", type=int, default=None, help="look back this many days (default from config)")
    parser.add_argument("--tickers", nargs="*", help="default: watchlist + latest screen")
    parser.add_argument("--no-rss", action="store_true", help="skip market-wide RSS feeds")
    opts = parser.parse_args(args)

    init_db()
    sync_reference_data()
    settings = news_settings()
    days = opts.days or int(settings["lookback_days_default"])
    focus = [t.upper() for t in opts.tickers] if opts.tickers else monitored_tickers()[0]
    since = datetime.now(timezone(timedelta(hours=7))) - timedelta(days=days)
    body_fetcher = PoliteFetcher(2.5, 20.0)

    def fetch_body(article):
        return extract_body(article.url, body_fetcher(article.url))

    for spec in load_config().sources.for_kind("news"):
        if spec.id.startswith("fixture") or (opts.no_rss and "feeds" in spec.config):
            continue
        provider = instantiate(spec)
        try:
            articles = provider.fetch_articles(focus, since)
        except ProviderError as exc:
            print(f"  {spec.id}: FAIL {exc}")
            continue
        report = process_articles(articles, set(focus), fetch_body=fetch_body)
        print(f"  {spec.id:<10} bài mới {report.new_articles:>4}  lấy nội dung {report.bodies_fetched:>3}  "
              f"sự kiện mới {report.events_created:>3}  gộp {report.events_updated:>3}"
              + (f"  lỗi nội dung {len(report.body_failures)}" if report.body_failures else ""), flush=True)
    moved = reclassify_roles()              # links stored before roles existed (idempotent)
    if moved:
        print(f"  {moved} liên kết tin chỉ nhắc tới mã (không phải tin của mã) được đánh dấu lại")
    dropped = retag_ambiguous()             # links of ambiguous codes the current rules no longer make
    if dropped:
        print("  bỏ liên kết tin gắn nhầm mã: " + ", ".join(f"{t} {n}" for t, n in sorted(dropped.items())))
    print(f"\nTin theo mã ({days} ngày, {len(focus)} mã theo dõi):")
    since_iso = since.isoformat()
    with connect() as conn:
        for t in focus:
            n = conn.execute("SELECT COUNT(*) FROM news_events e JOIN event_tickers x ON x.event_id = e.event_id "
                             "WHERE x.ticker = ? AND x.role = 'subject' AND e.occurred_at >= ?",
                             (t, since_iso)).fetchone()[0]
            print(f"  {t:<4} {n:>3} sự kiện")
    print("\nĐọc: python -m src.main news-brief <MÃ>")
    return 0


def news_brief(args: list[str]) -> int:
    import argparse
    from pathlib import Path

    from src.processors.news_pipeline import news_settings, rank_events, render_news_brief

    parser = argparse.ArgumentParser(prog="python -m src.main news-brief")
    parser.add_argument("ticker")
    parser.add_argument("--days", type=int, default=None)
    opts = parser.parse_args(args)
    days = opts.days or int(news_settings()["lookback_days_default"])
    brief = render_news_brief(opts.ticker, rank_events(opts.ticker, days), days)
    out = Path("data/briefs") / f"{opts.ticker.upper()}_news.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(brief, encoding="utf-8")
    print(brief)
    print(f"(đã lưu {out})")
    return 0


def news_annotate(args: list[str]) -> int:
    import argparse
    import json

    from src.processors.news_pipeline import AnnotationError, annotate_event

    parser = argparse.ArgumentParser(prog="python -m src.main news-annotate")
    parser.add_argument("event_id")
    parser.add_argument("json_file", help="what_happened, why_it_matters, direction, time_horizon, evidence_article_ids")
    opts = parser.parse_args(args)
    with open(opts.json_file, encoding="utf-8") as fh:
        annotation = json.load(fh)
    try:
        annotate_event(opts.event_id, annotation)
    except AnnotationError as exc:
        print(f"từ chối: {exc}")
        return 1
    print(f"đã ghi diễn giải cho {opts.event_id}")
    return 0


def fundamentals(args: list[str]) -> int:
    import argparse
    import time
    from collections import Counter
    from datetime import date

    from src.collectors.base import ProviderError
    from src.collectors.providers.cafef_financial_provider import (
        fetch_quarterly_income, latest_completed_quarter, quarter_end)
    from src.collectors.providers.web_news import PoliteFetcher
    from src.quant.fundamentals import fetch_log, grade_ticker, log_fetch, store_income
    from src.quant.screener import liquid_tickers, screen_settings

    parser = argparse.ArgumentParser(prog="python -m src.main fundamentals")
    parser.add_argument("--tickers", nargs="*", help="default: every ticker that clears the liquidity gate")
    parser.add_argument("--refresh", action="store_true", help="re-download even if the latest quarter is stored")
    opts = parser.parse_args(args)

    init_db()
    sync_reference_data()
    settings = screen_settings()
    tickers = [t.upper() for t in opts.tickers] if opts.tickers else liquid_tickers()
    wanted = quarter_end(*reversed(latest_completed_quarter(date.today()))).isoformat()
    fetcher = PoliteFetcher(2.5, 20.0)
    started = time.monotonic()
    fetched = skipped = 0
    for n, t in enumerate(tickers, 1):
        log = fetch_log(t)
        recent = log and log.get("checked_at", "")[:10] >= date.today().isoformat()[:8] + "01"
        if not opts.refresh and log and (log.get("latest_quarter") == wanted or (recent and not log.get("accepted_quarters"))):
            skipped += 1
            continue
        try:
            data = fetch_quarterly_income(t, date.today(), fetch=fetcher)
        except ProviderError as exc:
            print(f"  {t}: {exc}")
            continue
        store_income(data)
        log_fetch(data)
        fetched += 1
        if n % 25 == 0:
            print(f"  [{n}/{len(tickers)}] {time.monotonic() - started:.0f}s", flush=True)
    grades = {t: grade_ticker(t, settings) for t in tickers}
    count = Counter(g.grade for g in grades.values())
    print(f"BCTC: tải {fetched}, bỏ qua {skipped} (đã có quý {wanted}), {len(tickers)} mã")
    print("  hạng: " + "  ".join(f"{k}={count.get(k, 0)}" for k in ("A+", "A", "B", "WEAK", "FAIL", "NO_DATA")))
    print("  (số liệu CafeF, chưa đối chiếu BCTC gốc; LNST công ty mẹ thay cho EPS)")
    return 0


def _industry_names(cfg: dict) -> int:
    """One request per ticker whose name DNSE gives without diacritics (~190 of 1,663 on 2026-10-11)."""
    import time

    from src.collectors.base import ProviderError
    from src.collectors.providers.vietstock_industry_provider import fetch_profile
    from src.collectors.providers.web_news import PoliteFetcher
    from src.collectors.universe import has_diacritics, set_display_name
    from src.database import connect
    from src.quant.industry import store_industry

    with connect() as conn:
        tickers = [t for t, n in conn.execute("SELECT ticker, name FROM tickers WHERE active = 1 ORDER BY ticker")
                   if not has_diacritics(n)]
    print(f"Đọc tên công ty có dấu từ Vietstock cho {len(tickers)} mã (một lượt, cách nhau "
          f"{float(cfg.get('request_interval_seconds', 2.5))} giây)")
    fetcher = PoliteFetcher(float(cfg.get("request_interval_seconds", 2.5)), 20.0)
    started, named, failed = time.monotonic(), 0, 0
    for n, t in enumerate(tickers, 1):
        try:
            name, path_ = fetch_profile(t, fetch=fetcher)
        except ProviderError as exc:
            failed += 1
            print(f"  {t}: {exc}")
            continue
        if path_ is not None:
            store_industry(path_)
            named += has_diacritics(name)
        elif set_display_name(t, name):
            named += 1
        if n % 25 == 0:
            print(f"  [{n}/{len(tickers)}] {time.monotonic() - started:.0f}s", flush=True)
    print(f"Xong: {named} mã có tên có dấu; {len(tickers) - named - failed} mã có tên vốn không dấu "
          f"(vd CTCP Clever Group) hoặc trang không có tên; {failed} lỗi")
    return 0


def industry(args: list[str]) -> int:
    import argparse
    import time
    from collections import Counter

    from src.collectors.base import ProviderError
    from src.collectors.providers.vietstock_industry_provider import fetch_industry
    from src.collectors.providers.web_news import PoliteFetcher
    from src.quant.industry import load_industries, needs_refresh, store_industry
    from src.quant.screener import liquid_tickers, screen_settings

    parser = argparse.ArgumentParser(prog="python -m src.main industry")
    parser.add_argument("--tickers", nargs="*", help="default: every ticker that clears the liquidity gate")
    parser.add_argument("--refresh", action="store_true", help="re-read even if the classification is recent")
    parser.add_argument("--names", action="store_true",
                        help="read the profile page of every ticker whose stored name has no diacritics, once")
    opts = parser.parse_args(args)

    init_db()
    sync_reference_data()
    cfg = screen_settings()["industry"]
    if opts.names:
        return _industry_names(cfg)
    tickers = [t.upper() for t in opts.tickers] if opts.tickers else liquid_tickers()
    known = load_industries()
    fetcher = PoliteFetcher(float(cfg.get("request_interval_seconds", 2.5)), 20.0)
    started = time.monotonic()
    fetched = skipped = failed = 0
    for n, t in enumerate(tickers, 1):
        if not opts.refresh and not needs_refresh(t, known, int(cfg["refresh_days"])):
            skipped += 1
            continue
        try:
            store_industry(fetch_industry(t, fetch=fetcher))
            fetched += 1
        except ProviderError as exc:
            failed += 1
            print(f"  {t}: {exc}")
        if n % 25 == 0:
            print(f"  [{n}/{len(tickers)}] {time.monotonic() - started:.0f}s", flush=True)
    rows = load_industries()
    level_key = {1: "sector", 2: "industry_group", 3: "industry"}[int(cfg["level"])]
    count = Counter(rows[t][level_key] or rows[t]["sector"] for t in tickers if t in rows)
    print(f"Phân ngành: đọc {fetched}, bỏ qua {skipped} (còn mới), lỗi {failed}, {len(tickers)} mã")
    for name, n in count.most_common():
        print(f"  {n:>3}  {name}")
    print("  (nguồn Vietstock, chưa đối chiếu)")
    return 0


def flow(args: list[str]) -> int:
    import argparse

    from src.collectors.runner import collect_foreign_flows
    from src.env import load_dotenv
    from src.quant.monitor import monitored_tickers

    parser = argparse.ArgumentParser(prog="python -m src.main flow")
    parser.add_argument("--days", type=int, default=10, help="calendar days back (sessions already held are skipped)")
    parser.add_argument("--tickers", nargs="*", help="default: every monitored ticker")
    opts = parser.parse_args(args)
    load_dotenv()
    init_db()
    tickers = [t.upper() for t in opts.tickers] if opts.tickers else monitored_tickers()[0]
    report = collect_foreign_flows(lookback_days=opts.days, tickers=tickers)
    failed = [o.scope for o in report.outcomes if o.stale]
    print(f"Khối ngoại: {len(tickers)} mã, {opts.days} ngày gần nhất · lỗi: {', '.join(failed) or 'không'}")
    return 0


def logos(args: list[str]) -> int:
    import argparse

    from src.collectors.logos import LOGO_DIR, collect_logos
    from src.collectors.providers.web_news import PoliteFetcher
    from src.quant.monitor import monitored_tickers

    parser = argparse.ArgumentParser(prog="python -m src.main logos")
    parser.add_argument("--tickers", nargs="*", help="default: every monitored ticker")
    parser.add_argument("--all", action="store_true", help="every ticker with stored prices (the whole market)")
    parser.add_argument("--refresh", action="store_true", help="download again even if a logo is stored")
    opts = parser.parse_args(args)
    init_db()
    if opts.tickers:
        tickers = [t.upper() for t in opts.tickers]
    elif opts.all:
        from src.database import connect

        with connect() as conn:
            tickers = monitored_tickers()[0] + [r[0] for r in conn.execute(
                "SELECT DISTINCT p.ticker FROM prices_daily p JOIN tickers t ON t.ticker = p.ticker "
                "WHERE t.active = 1 ORDER BY p.ticker")]
    else:
        tickers = monitored_tickers()[0]
    cfg = load_config().thresholds["logos"]
    report = collect_logos(tickers, PoliteFetcher().fetch_bytes, cfg, refresh=opts.refresh)
    print(f"Logo ({LOGO_DIR}): {len(report.saved)} mới, {len(report.kept)} đã có, "
          f"{len(report.missing) + len(report.skipped)} không có logo trên Vietstock"
          + (f" ({', '.join(report.missing + report.skipped)})" if report.missing or report.skipped else "")
          + (f" · lỗi: {', '.join(f'{t} ({e})' for t, e in report.failed.items())}" if report.failed else ""))
    return 0


def _volume_basis_text(vb: dict | None) -> str:
    """" (KL khớp lệnh)" -- and how many comparisons fell back to total volume."""
    if not vb:
        return ""
    if vb.get("basis") != "matched":
        return " (tổng KL)"
    n = vb.get("fallback_sessions") or 0
    return " (KL khớp lệnh" + (f"; {n} phiên so bằng tổng KL vì chưa có số khớp lệnh" if n else "") + ")"


def index_session(args: list[str]) -> int:
    """Matched / put-through volume and breadth of each index session, plus DNSE's trading calendar."""
    import argparse
    import time

    from src.collectors import trading_calendar
    from src.collectors.backfill import live_market_provider, run_backfill
    from src.collectors.base import ProviderError
    from src.collectors.index_session import collect_index_sessions
    from src.env import load_dotenv
    from src.market_calendar import now_vn

    parser = argparse.ArgumentParser(prog="python -m src.main index-session")
    parser.add_argument("--days", type=int, help="newest sessions to look at (default: index_session.backfill_sessions)")
    parser.add_argument("--symbols", nargs="*", help="default: index_session.symbols")
    opts = parser.parse_args(args)
    load_dotenv()
    init_db()
    try:
        provider = live_market_provider()
    except ProviderError as exc:
        print(f"Phiên chỉ số: không đọc được ({exc})")
        return 1
    if not hasattr(provider, "get_market_index_close"):
        print(f"Phiên chỉ số: nguồn {provider.source_id} không có market-index")
        return 1
    thresholds = load_config().thresholds
    cfg = thresholds["index_session"]
    now = now_vn()
    code = 0
    try:
        print(trading_calendar.refresh(provider.client.get, now.date(), provider.source_id))
    except ProviderError as exc:
        print(f"lịch giao dịch: không đọc được ({exc}) — dùng bản đã lưu / thứ Hai–Sáu")
    symbols = [str(x).upper() for x in (opts.symbols or cfg["symbols"])]
    # The figures attach to the session's index_daily row, so bring the index bars up to date
    # first (one OHLC request per index): run before `monitor` or `backfill`, today's row would
    # not exist yet and the session would be skipped.
    try:
        bars = run_backfill(tickers=[], benchmarks=symbols, provider=provider)
        if bars.failed_scopes:
            print("nến chỉ số: " + "; ".join(f"{o.scope} ({o.note})" for o in bars.failed_scopes))
    except ProviderError as exc:
        print(f"nến chỉ số: không tải được ({exc}) — chỉ gắn số liệu cho các phiên đã có nến")
    for symbol in symbols:
        started = time.monotonic()
        rep = collect_index_sessions(provider.get_market_index_close, cfg, thresholds["pipeline"], symbol=symbol,
                                     sessions=opts.days, now=now)
        span = f"{rep.written[-1]} → {rep.written[0]}" if rep.written else "không phiên nào mới"
        print(f"{rep.symbol}: lưu KL khớp lệnh / thỏa thuận và độ rộng {len(rep.written)} phiên ({span}) · "
              f"{rep.requests} request · {time.monotonic() - started:.0f} giây")
        if rep.history_starts_after:
            print(f"  DNSE không còn dữ liệu market-index từ phiên {rep.history_starts_after} trở về trước "
                  "— các phiên đó giữ tổng KL (gồm thỏa thuận)")
        if rep.not_final:
            print(f"  chưa chốt phiên: {', '.join(rep.not_final)}")
        if rep.mismatched:
            print("  không lưu vì giá đóng cửa lệch nhau: "
                  + "; ".join(f"{d} ({why})" for d, why in rep.mismatched.items()))
        if rep.errors:
            code = 1
            print("  lỗi: " + "; ".join(f"{d} ({why})" for d, why in list(rep.errors.items())[:3]))
    return code


def index_check(args: list[str]) -> int:
    """VN-Index bars vs HOSE's published values: a report; nothing is changed (user, 2026-10-09)."""
    import argparse

    from src.collectors.base import ProviderError
    from src.collectors.index_check import collect_hose, compare, write_report
    from src.collectors.providers.hose_index_provider import hose_provider
    from src.market_calendar import now_vn
    from src.quant.screener import screen_settings

    parser = argparse.ArgumentParser(prog="python -m src.main index-check")
    parser.add_argument("--sessions", type=int, help="recent sessions in the table (default: index_check.sessions)")
    parser.add_argument("--no-fetch", action="store_true", help="compare what is stored, without reading HOSE")
    opts = parser.parse_args(args)
    init_db()
    cfg = dict(load_config().thresholds["index_check"])
    if opts.sessions:
        cfg["sessions"] = opts.sessions
    code = 0
    for symbol in cfg["symbols"]:
        if not opts.no_fetch:
            try:
                provider = hose_provider()
                n = collect_hose(provider.index_bars, symbol, cfg, provider.source_id, now_vn().date())
                print(f"HOSE {symbol}: đọc {n} phiên (1 request)")
            except (ProviderError, PermissionError) as exc:
                code = 1
                print(f"HOSE {symbol}: không đọc được ({exc}) — so với dữ liệu HOSE đã lưu")
        r = compare(symbol, cfg, screen_settings())
        if r.get("error"):
            print(f"{symbol}: {r['error']}")
            code = 1
            continue
        path = write_report(r)
        a, m, h = r["agreement_recent"]["dnse_ohlc"], r["agreement_recent"]["dnse_market_index"], r["history"]
        print(f"{symbol} {r['session']} — {a['compared']} phiên gần nhất, khớp HOSE (±{r['tolerance_points']:g} điểm):")
        print(f"  nến DNSE (đang dùng):   cao {a['high']}/{a['compared']} · thấp {a['low']}/{a['compared']} · "
              f"đóng cửa {a['close']}/{a['compared']}")
        if m["compared"]:
            print(f"  market-index DNSE:      cao {m['high']}/{m['compared']} · thấp {m['low']}/{m['compared']}")
        print(f"  toàn lịch sử từ {h['from']}: lệch giá cao {h['high_differs']}, giá thấp {h['low_differs']}, "
              f"đóng cửa {len(h['close_differs'])} / {h['compared']} phiên; DNSE thiếu {len(h['missing_in_dnse'])} phiên HOSE có")
        rules = r["rules"]
        if rules["changed"]:
            now, alt = rules["as_stored"], rules["with_hose_high_low"]
            print("  nếu dùng giá cao / thấp của HOSE, quy tắc thị trường đổi ở: " + "; ".join(
                f"{k}: {now[k]} → {alt[k]}" for k in rules["changed"]))
        else:
            print("  dùng giá cao / thấp của HOSE thì trạng thái thị trường, ngày phân phối và FTD không đổi")
        print(f"  báo cáo: {path}")
    return code


def refprices(args: list[str]) -> int:
    import argparse
    import time

    from src.collectors.backfill import live_market_provider
    from src.collectors.base import ProviderError
    from src.collectors.refprices import collect_reference_prices
    from src.env import load_dotenv
    from src.quant.monitor import monitored_tickers

    parser = argparse.ArgumentParser(prog="python -m src.main refprices")
    parser.add_argument("--tickers", nargs="*", help="default: every monitored ticker")
    parser.add_argument("--all", action="store_true", help="every active ticker with stored prices (~1,500 requests; DNSE allows 1,000 secdef reads an hour, so over an hour)")
    parser.add_argument("--market", action="store_true",
                        help="also fill every UPCoM ticker's latest session from the previous average (~230 requests)")
    opts = parser.parse_args(args)
    load_dotenv()
    init_db()
    if opts.tickers:
        tickers = [t.upper() for t in opts.tickers]
    elif opts.all:
        from src.database import connect

        with connect() as conn:
            tickers = monitored_tickers()[0] + [r[0] for r in conn.execute(
                "SELECT DISTINCT p.ticker FROM prices_daily p JOIN tickers t ON t.ticker = p.ticker "
                "WHERE t.active = 1 ORDER BY p.ticker")]
    else:
        tickers = monitored_tickers()[0]
    try:
        provider = live_market_provider()
    except ProviderError as exc:
        print(f"Giá tham chiếu: không đọc được ({exc})")
        return 1
    started = time.monotonic()
    cfg = load_config().thresholds["reference_prices"]
    report = collect_reference_prices(tickers, provider.get_reference_prices, cfg, provider.source_id)
    by_session = ", ".join(f"phiên {d:%d/%m}: {n} mã" for d, n in report.sessions().items()) or "không mã nào"
    print(f"Giá tham chiếu (DNSE): {by_session} · {time.monotonic() - started:.0f} giây"
          + (f" · không lưu {len(report.failed)} mã: " + "; ".join(f"{t} ({why})" for t, why in list(report.failed.items())[:10])
             if report.failed else ""))

    # UPCoM sessions DNSE no longer serves: the exchange rule on the previous session's average,
    # used only while it reproduces DNSE's published references.
    from src.collectors.refprices import check_average_rule, fill_from_averages, fill_targets
    from src.database import connect
    from src.market_calendar import price_limit_pct

    band = float(price_limit_pct("UPCOM"))
    with connect() as conn:
        exchanges = {r[0]: str(r[1]).upper() for r in conn.execute("SELECT ticker, exchange FROM tickers")}
    check = check_average_rule([t for t in tickers if exchanges.get(t) == "UPCOM"], provider.get_session_average, band)
    share = check.share
    print(f"Quy tắc UPCoM (tham chiếu = giá bình quân phiên trước, làm tròn 100 đ): khớp "
          f"{len(check.checked) - len(check.mismatched)}/{len(check.checked)} giá DNSE công bố"
          + (f" · lệch: {', '.join(f'{t} {check.checked[t][0]:,.0f} vs {check.checked[t][1]:,.0f}' for t in sorted(check.mismatched))}"
             if check.mismatched else ""))
    filled = None
    if share is None or share < float(cfg["average_rule_min_share"]):
        print("  → không bù giá tham chiếu từ giá bình quân lần này (chưa đủ bằng chứng quy tắc còn đúng)")
    else:
        targets = fill_targets(tickers, int(cfg["fill_sessions"]))
        if opts.market:
            with connect() as conn:
                everyone = [r[0] for r in conn.execute(
                    "SELECT DISTINCT p.ticker FROM prices_daily p JOIN tickers t ON t.ticker = p.ticker "
                    "WHERE t.active = 1 AND t.exchange = 'UPCOM'")]
            targets += [x for x in fill_targets(everyone, 1, latest_only=True) if x not in targets]
        targets = [x for x in targets if x[0] not in check.mismatched]
        filled = fill_from_averages(targets, provider.get_session_average, cfg, band, provider.source_id)
        days = ", ".join(f"phiên {d:%d/%m}: {n} mã" for d, n in filled.sessions().items()) or "không cần bù"
        print(f"  → bù từ giá bình quân phiên trước (UPCoM): {days}"
              + (f" · bỏ qua {len(filled.failed)} mã (vd {'; '.join(list(filled.failed.values())[:3])})" if filled.failed else "")
              + f" · tổng {time.monotonic() - started:.0f} giây")
    return 0 if report.stored or (filled and filled.stored) else 1


def market(args: list[str]) -> int:
    import argparse
    import time

    from src.quant.market_view import build_snapshot, store_snapshot

    parser = argparse.ArgumentParser(prog="python -m src.main market")
    parser.parse_args(args)
    init_db()
    started = time.monotonic()
    rows = build_snapshot()
    as_of = store_snapshot(rows)
    fresh = sum(1 for r in rows if r["trade_date"] == as_of)
    flagged = sum(1 for r in rows if r.get("flagged"))
    print(f"Toàn thị trường: {len(rows)} mã, giá tới {as_of} ({fresh} mã có phiên này) · "
          f"{flagged} mã dữ liệu cần kiểm tra · {time.monotonic() - started:.0f} giây")
    return 0


def ssi(args: list[str]) -> int:
    import argparse
    from datetime import date as _date

    from src.collectors.providers.ssi_fcdata_provider import SsiAuthError, ssi_provider
    from src.collectors.ssi_collect import collect_ssi
    from src.env import load_dotenv

    parser = argparse.ArgumentParser(prog="python -m src.main ssi")
    parser.add_argument("--days", type=int, default=5, help="calendar days back (weekends skipped)")
    parser.add_argument("--end", help="last day, YYYY-MM-DD (default: today)")
    opts = parser.parse_args(args)
    load_dotenv()
    init_db()
    try:
        provider = ssi_provider()
    except SsiAuthError as exc:
        print(f"SSI: {exc}")
        return 3
    report = collect_ssi(provider, days=opts.days, end=_date.fromisoformat(opts.end) if opts.end else None)
    print("SSI cổ phiếu (mã trong danh sách theo ngày): " + ", ".join(f"{d} {n}" for d, n in report.stock_rows.items()))
    print("SSI chỉ số: " + ", ".join(f"{k} {n}" for k, n in report.index_rows.items())
          + f" · bỏ {report.skipped_outside_universe} mã ngoài danh sách (ETF, chứng quyền, trái phiếu…)")
    if report.errors:
        print("Lỗi: " + "; ".join(report.errors))
    return 0 if not report.errors else 1


def crosscheck(args: list[str]) -> int:
    import argparse
    import json as _json
    from pathlib import Path

    from src.collectors.crosscheck import crosscheck as run_crosscheck
    from src.database import PROJECT_ROOT

    parser = argparse.ArgumentParser(prog="python -m src.main crosscheck")
    parser.add_argument("--days", type=int, default=10)
    opts = parser.parse_args(args)
    init_db()
    r = run_crosscheck(days=opts.days)
    if not r.get("ok"):
        print(r.get("note"))
        return 3
    out = PROJECT_ROOT / "data" / "audit" / f"crosscheck_{r['to']}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(_json.dumps(r, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    s, f, i = r["stocks"], r["foreign"], r["index"]
    print(f"DNSE ↔ SSI, {r['from']} → {r['to']}")
    print(f"  Giá đóng cửa ({s['compared']} cặp): {s['closes']}")
    print(f"  Khối lượng cổ phiếu: {s['volumes']}")
    print(f"  Khối ngoại ({f['compared']} cặp): {f['result']}")
    print(f"  Khối lượng VN-Index của DNSE ({i['sessions']} phiên): {i['dnse_volume_is']}")
    for k, v in i["distribution_days"].items():
        print(f"    ngày phân phối {k}: {len(v)} ({', '.join(v)})")
    acts = r["corporate_actions"]
    print(f"  Ngày có quyền (giá tham chiếu ≠ giá trước đó): {len(acts)}"
          + "".join(f"\n    {a['ticker']} {a['date']} {a['change_pct']:+.2f}% — DNSE {a['dnse_history']}" for a in acts[:15]))
    print(f"Báo cáo: {out}")
    return 0


def backtest_sepa(args: list[str]) -> int:
    import argparse
    import time
    from datetime import date
    from pathlib import Path

    from src.backtest.sepa import render_report, run_sepa_backtest

    parser = argparse.ArgumentParser(prog="python -m src.main backtest-sepa")
    parser.add_argument("--limit", type=int, default=None, help="only the first N tickers (quick check)")
    parser.add_argument("--step", type=int, default=5, help="sample baseline / hold signals every N sessions")
    opts = parser.parse_args(args)
    started = time.monotonic()

    def progress(done: int, total: int) -> None:
        if done % 100 == 0 or done == total:
            print(f"  [{done}/{total}] {time.monotonic() - started:.0f}s", flush=True)

    bt = run_sepa_backtest(limit=opts.limit, step=opts.step, progress=progress)
    report = render_report(bt)
    out = Path("data/backtest") / f"sepa_{date.today().isoformat()}{'_limit' + str(opts.limit) if opts.limit else ''}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(report, encoding="utf-8")
    print(report)
    print(f"({time.monotonic() - started:.0f}s) đã lưu {out}")
    return 0


def backtest_swing(args: list[str]) -> int:
    """Short-term setups (<= 2 weeks) measured under Vietnam's rules; nothing reaches the dashboard."""
    import argparse
    import time
    from datetime import date
    from pathlib import Path

    from src.backtest.swing import render_report, run_swing_backtest, save_json

    parser = argparse.ArgumentParser(prog="python -m src.main backtest-swing")
    parser.add_argument("--limit", type=int, default=None, help="only the first N tickers (quick check)")
    opts = parser.parse_args(args)
    started = time.monotonic()

    def progress(done: int, total: int) -> None:
        if done % 200 == 0 or done == total:
            print(f"  [{done}/{total}] {time.monotonic() - started:.0f}s", flush=True)

    bt = run_swing_backtest(limit=opts.limit, progress=progress)
    stem = f"swing_{date.today().isoformat()}{'_limit' + str(opts.limit) if opts.limit else ''}"
    out = Path("data/backtest") / f"{stem}.md"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_report(bt), encoding="utf-8")
    save_json(bt, out.with_suffix(".json"))
    print(render_report(bt))
    print(f"({time.monotonic() - started:.0f}s) đã lưu {out} và {out.with_suffix('.json').name}")
    return 0


def start(args: list[str]) -> int:
    """Update everything (src/pipeline.py), then open the dashboard."""
    import argparse
    import socket
    import webbrowser

    from src import pipeline as pl
    from src.web.server import serve

    parser = argparse.ArgumentParser(prog="python -m src.main start")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    parser.add_argument("--no-update", action="store_true", help="open the dashboard at once, without updating")
    parser.add_argument("--plan", action="store_true", help="show which steps would run, run nothing")
    parser.add_argument("--update-only", action="store_true",
                        help="update (screen included when due), then exit without serving: the 15:30 job's last step")
    opts = parser.parse_args(args)
    init_db()
    if opts.plan:
        now = pl.vn_now()
        print(pl.summary(pl.plan_steps(now, index_dates=pl.dnse_index_dates(now))))
        return 0
    if not opts.no_update:
        cfg, now = pl.settings(), pl.vn_now()
        holder = pl.acquire_lock(now, cfg)
        if holder:
            print(f"Đang có một lượt cập nhật khác chạy ({holder}) — mở web với dữ liệu hiện có.")
            if opts.update_only:
                return 1
        else:
            try:
                plan = pl.plan_steps(now, index_dates=pl.dnse_index_dates(now))
                print("Kế hoạch cập nhật trước khi mở web:\n" + pl.summary(plan))
                pl.run_steps(plan)
                print("\nĐã cập nhật xong:\n" + pl.summary(plan))
            except KeyboardInterrupt:
                print("\nĐã dừng cập nhật (Ctrl+C). Chạy lại start để tiếp tục — các bước đã xong sẽ được bỏ qua.")
                return 130
            finally:
                pl.release_lock()
        if opts.update_only:
            return 0
    url = f"http://127.0.0.1:{opts.port}/"
    with socket.socket() as probe:
        busy = probe.connect_ex(("127.0.0.1", opts.port)) == 0
    if busy:
        # A dashboard is already serving: it reads the database on every request, so the new data show on reload.
        print(f"Dashboard đã chạy sẵn ở {url} — tải lại trang (Ctrl+F5) để thấy dữ liệu mới.")
        if not opts.no_browser:
            webbrowser.open(url)
        return 0
    serve(port=opts.port, open_browser=not opts.no_browser)
    return 0


def check(args: list[str]) -> int:
    """Invariants over the stored states and screen runs (src/web/checks.py). Exit 1 when one
    is broken; the pipeline and scripts/daily.ps1 go on to the next step either way."""
    import argparse

    from src.web.checks import run_checks, write_report

    parser = argparse.ArgumentParser(prog="python -m src.main check")
    parser.add_argument("--no-zones", action="store_true", help="skip the support / resistance tick check")
    opts = parser.parse_args(args)
    report = run_checks(zones=not opts.no_zones)
    out = write_report(report)
    print(f"Kiểm tra bất biến phiên {report['session']}: {report['checked']} mã, "
          f"{len(report['violations'])} vi phạm, "
          f"{len({r['ticker'] for r in report['transition']})} mã đang chuyển (số theo quy tắc nền cũ)")
    for v in report["violations"]:
        print(f"  {v['ticker']:<4} {v['rule']:<22} {v['detail']}")
    for i in report["info"]:
        print(f"  (ghi nhận) {i['ticker']:<4} {i['detail']}")
    print(f"Báo cáo: {out}")
    return 1 if report["violations"] else 0


COMMANDS = {
    "doctor": lambda args: doctor(),
    "initdb": lambda args: initdb(),
    "universe": lambda args: universe(),
    "backfill": backfill,
    "screen": screen,
    "monitor": monitor,
    "news": news,
    "news-brief": news_brief,
    "news-annotate": news_annotate,
    "start": start,
    "backtest-sepa": backtest_sepa,
    "backtest-swing": backtest_swing,
    "fundamentals": fundamentals,
    "industry": industry,
    "flow": flow,
    "logos": logos,
    "market": market,
    "refprices": refprices,
    "index-session": index_session,
    "index-check": index_check,
    "check": check,
    "ssi": ssi,
    "crosscheck": crosscheck,
}


def _utf8_console() -> None:
    """Windows consoles default to cp1252, which cannot print Vietnamese."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv: list[str]) -> int:
    _utf8_console()
    if len(argv) < 2 or argv[1] not in COMMANDS:
        print(__doc__)
        return 64
    return COMMANDS[argv[1]](argv[2:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
