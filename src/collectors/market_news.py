"""Incremental market-wide disclosures, independent of the current screened/watch list."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.database import connect
from src.processors.news_pipeline import process_articles


def collect_market_news(provider, universe, body_focus, since, fetch_body=None, path: Path | None = None):
    started = datetime.now(timezone.utc)
    with connect(path) as conn:
        state = conn.execute("SELECT last_success_at,covered_since FROM news_source_state WHERE source_id = ?",
                             (provider.source_id,)).fetchone()
    boundary = max(since, datetime.fromisoformat(state[0]) - timedelta(days=2)) if state else since
    print(f"  {provider.source_id}: lấy công bố toàn thị trường từ {boundary.isoformat()}", flush=True)
    articles = provider.fetch_market_articles(boundary)
    report = process_articles(articles, universe, fetch_body=fetch_body, body_focus=body_focus, path=path)
    covered = getattr(provider, "market_coverage_since", boundary)
    if state:
        covered = min(covered, datetime.fromisoformat(state[1]))
    # Never move the cursor on a fetch/ingestion exception. Re-read two days to cover late postings.
    with connect(path) as conn:
        conn.execute("INSERT INTO news_source_state (source_id,last_success_at,covered_since) VALUES (?,?,?) "
                     "ON CONFLICT(source_id) DO UPDATE SET last_success_at=excluded.last_success_at, "
                     "covered_since=excluded.covered_since", (provider.source_id, started.isoformat(), covered.isoformat()))
    print(f"  {provider.source_id} toàn thị trường: {len(articles)} mục, {report.new_articles} bài mới, "
          f"{report.events_created} sự kiện mới; lịch sử từ {covered.isoformat()}", flush=True)
    return report
