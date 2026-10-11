"""News pipeline: articles -> tagged, deduplicated, typed, scored events.

Everything here is deterministic. Interpretation -- what an event means for the
company -- is written later by Claude Code in the session, through
`annotate_event`, which checks what it is given rather than trusting it.

Rules this module enforces:

* **Provenance.** Every article keeps its source_id and level; an event's
  assertion type can never be stronger than its best source allows. Level 5 is
  always RUMOUR. An aggregator's repost of a disclosure stays INFERENCE until
  checked against the issuer's own document.
* **The context boundary.** Bodies live on disk (`data/news/...`), not in the
  database, and a brief carries at most `brief_max_events` events.
* **No importance filter at collection.** Every article is stored; ranking only
  orders what a brief shows.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from src.collectors.base import NewsArticle, ProviderError
from src.collectors.providers.web_news import ArticleBody
from src.config import PROJECT_ROOT, load_config
from src.database import connect, utcnow

log = logging.getLogger(__name__)

VN_TZ = timezone(timedelta(hours=7))
NEWS_DIR = PROJECT_ROOT / "data" / "news"
ASSERTION_STRENGTH = {"RUMOUR": 0, "ESTIMATE": 1, "INFERENCE": 2, "FACT": 3}
DIRECTIONS = {"POSITIVE", "NEUTRAL", "NEGATIVE"}
HORIZONS = {"INTRADAY", "DAYS", "WEEKS", "QUARTERS"}


def news_settings(thresholds: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw = thresholds if thresholds is not None else load_config().thresholds.raw
    return dict(raw["news"])


# ---------------------------------------------------------------------------
# Text utilities
# ---------------------------------------------------------------------------

_TOKEN = re.compile(r"\w+", re.UNICODE)
_CODE = re.compile(r"(?<![A-Za-z0-9])([A-Z][A-Z0-9]{2})(?![A-Za-z0-9])")


def tokens(text: str) -> list[str]:
    return [t.lower() for t in _TOKEN.findall(text)]


def simhash64(text: str) -> int:
    """64-bit simhash over word bigrams; near-identical texts differ in few bits."""
    words = tokens(text)
    grams = [" ".join(words[i:i + 2]) for i in range(max(1, len(words) - 1))] or [""]
    v = [0] * 64
    for g in grams:
        h = int.from_bytes(hashlib.md5(g.encode()).digest()[:8], "big")
        for bit in range(64):
            v[bit] += 1 if h >> bit & 1 else -1
    return sum(1 << bit for bit in range(64) if v[bit] > 0)


def hamming(a: int, b: int) -> int:
    return bin(a ^ b).count("1")


def jaccard(a: str, b: str) -> float:
    sa, sb = set(tokens(a)), set(tokens(b))
    return len(sa & sb) / len(sa | sb) if sa and sb else 0.0


def body_hash(text: str) -> str:
    return hashlib.sha1(" ".join(tokens(text)).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Tagging and typing
# ---------------------------------------------------------------------------

def tag_tickers(text: str, universe: set[str], names: Mapping[str, str], ambiguous: set[str],
                aliases: Mapping[str, Sequence[str]] | None = None) -> set[str]:
    """Tickers a text is about. Ambiguous codes need the company's name, one of its `aliases`
    or a cue: "mã X", "cổ phiếu X", "HOSE: X", "(X)", or "X:" opening the text (a disclosure)."""
    found: set[str] = set()
    lower = text.lower()
    for code in set(_CODE.findall(text)):
        if code not in universe:
            continue
        if code in ambiguous:
            name = (names.get(code) or "").lower()
            short = _short_name(name)
            cue = (re.search(rf"(mã|cổ phiếu|cp|hose:|hnx:|upcom:)\s*{code.lower()}\b", lower)
                   or re.search(rf"\(\s*{code}\s*\)", text) or re.match(rf"\s*{code}\s*:", text))
            alias = any(re.search(rf"(?<!\w){re.escape(str(a).lower())}(?!\w)", lower)
                        for a in (aliases or {}).get(code, ()))
            if not (cue or alias or (short and short in lower)):
                continue
        found.add(code)
    return found


def _tag_rules(settings: Mapping[str, Any]) -> tuple[set[str], dict[str, list[str]]]:
    """(ambiguous codes, aliases per code) from the news settings."""
    return ({str(a).upper() for a in settings.get("ambiguous_tickers", [])},
            {str(k).upper(): list(v or []) for k, v in (settings.get("ticker_aliases") or {}).items()})


def _short_name(name: str) -> str:
    """'CTCP Chứng khoán SSI' -> 'chứng khoán ssi' (drop the legal form)."""
    for prefix in ("công ty cổ phần", "ctcp", "tổng công ty", "ngân hàng tmcp", "công ty"):
        if name.startswith(prefix):
            return name[len(prefix):].strip()
    return name.strip()


def classify(text: str, rules: Mapping[str, Sequence[str]]) -> str:
    lower = text.lower()
    for event_type, keywords in rules.items():
        if any(k in lower for k in keywords):
            return event_type
    return "OTHER"


def classify_event(title: str, summary: str, rules: Mapping[str, Sequence[str]]) -> str:
    """Type from the headline; the summary is consulted only if the headline matches nothing.

    Bodies are not scanned: a bond-issue article that mentions last year's
    dividend in paragraph six is a bond issue, and a market wrap that mentions
    a capital increase is not one.
    """
    from_title = classify(title, rules)
    if from_title != "OTHER":
        return from_title
    return classify(f"{title}\n{summary[:400]}", rules)


def reclassify_events(path: Path | None = None, thresholds: Mapping[str, Any] | None = None) -> dict[str, int]:
    """Re-type stored events after the rules change. Returns {'OLD->NEW': count}."""
    settings = news_settings(thresholds)
    changes: dict[str, int] = {}
    with connect(path) as conn:
        for event in conn.execute("SELECT event_id, event_type, headline FROM news_events").fetchall():
            summary = ""
            first = conn.execute("SELECT raw_path FROM news_articles WHERE event_id = ? AND raw_path IS NOT NULL "
                                 "ORDER BY published_at LIMIT 1", (event["event_id"],)).fetchone()
            if first:
                try:
                    body = json.loads((PROJECT_ROOT / first["raw_path"]).read_text(encoding="utf-8"))
                    summary = body.get("summary") or (body.get("text") or "")[:400]
                except (OSError, ValueError):
                    pass
            new = classify_event(event["headline"], summary, settings["event_rules"])
            if new != event["event_type"]:
                conn.execute(
                    "UPDATE news_events SET event_type = ?, company_impact_score = ?, time_sensitivity = ? "
                    "WHERE event_id = ?",
                    (new, float(settings["company_impact_by_type"].get(new, 0.3)),
                     float(settings["time_sensitivity_by_type"].get(new, 0.3)), event["event_id"]))
                key = f"{event['event_type']}->{new}"
                changes[key] = changes.get(key, 0) + 1
    return changes


def assertion_for(level: int, settings: Mapping[str, Any]) -> str:
    if level >= 5:
        return "RUMOUR"
    return "FACT" if level <= int(settings["fact_max_source_level"]) else "INFERENCE"


# ---------------------------------------------------------------------------
# Storage
# ---------------------------------------------------------------------------

def _body_path(article: NewsArticle) -> Path:
    when = article.published_at or datetime.now(VN_TZ)
    return NEWS_DIR / f"{when:%Y}" / f"{when:%m}" / f"{article.article_id}.json"


def _save_body(article: NewsArticle, body: ArticleBody, path: Path | None) -> tuple[str, str]:
    target = path or _body_path(article)
    target.parent.mkdir(parents=True, exist_ok=True)
    doc = {
        "article_id": article.article_id, "title": article.title, "url": article.url,
        "published_at": article.published_at.isoformat() if article.published_at else None,
        "source_id": article.provenance.source_id, "source_level": int(article.provenance.source_level),
        "summary": body.summary, "text": body.text, "origin": body.origin,
        "documents": body.document_links,
    }
    target.write_text(json.dumps(doc, ensure_ascii=False, indent=1), encoding="utf-8")
    try:
        rel = str(target.relative_to(PROJECT_ROOT))
    except ValueError:
        rel = str(target)
    return rel, body_hash(body.summary + "\n" + body.text)


@dataclass
class PipelineReport:
    new_articles: int = 0
    bodies_fetched: int = 0
    body_failures: dict[str, str] = field(default_factory=dict)
    events_created: int = 0
    events_updated: int = 0
    by_ticker: dict[str, int] = field(default_factory=dict)


def process_articles(
    articles: Iterable[NewsArticle],
    focus: set[str],
    fetch_body: Callable[[NewsArticle], ArticleBody] | None = None,
    path: Path | None = None,
    body_dir: Path | None = None,
    now: datetime | None = None,
    thresholds: Mapping[str, Any] | None = None,
) -> PipelineReport:
    settings = news_settings(thresholds)
    now = now or datetime.now(VN_TZ)
    report = PipelineReport()
    with connect(path) as conn:
        universe = {r[0] for r in conn.execute("SELECT ticker FROM tickers")}
        names = {r[0]: r[1] or "" for r in conn.execute("SELECT ticker, name FROM tickers")}
        known = {r[0] for r in conn.execute("SELECT article_id FROM news_articles")}
    ambiguous, aliases = _tag_rules(settings)

    for article in articles:
        if article.article_id in known:
            continue
        known.add(article.article_id)
        headline_tags = set(article.tickers_hint) | tag_tickers(article.title, universe, names, ambiguous, aliases)
        mention_tags = tag_tickers(article.body or "", universe, names, ambiguous, aliases) - headline_tags
        relevant = (headline_tags | mention_tags) & focus

        raw_path = digest = None
        summary = article.body or ""
        text_for_typing = f"{article.title}\n{summary}"
        if relevant and fetch_body is not None:
            try:
                body = fetch_body(article)
                target = (body_dir / f"{article.article_id}.json") if body_dir else None
                raw_path, digest = _save_body(article, body, target)
                summary = body.summary or body.text[:400]
                text_for_typing = f"{article.title}\n{body.summary}\n{body.text[:2000]}"
                report.bodies_fetched += 1
            except ProviderError as exc:
                report.body_failures[article.article_id] = str(exc)

        level = int(article.provenance.source_level)
        with connect(path) as conn:
            conn.execute(
                "INSERT INTO news_articles (article_id, title, url, source_id, source_level, published_at, "
                "collected_at, body_hash, simhash, language, raw_path) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (article.article_id, article.title, article.url, article.provenance.source_id, level,
                 article.published_at.isoformat() if article.published_at else None, utcnow(), digest,
                 format(simhash64(text_for_typing), "016x"), article.language, raw_path),
            )
        report.new_articles += 1
        if not relevant:
            continue

        event_type = classify_event(article.title, summary, settings["event_rules"])
        for ticker in sorted(relevant):
            subject = ticker in headline_tags
            role_relevance = settings["relevance_subject"] if subject else settings["relevance_mention"]
            created = _attach_to_event(article, ticker, event_type, float(role_relevance), level,
                                       text_for_typing, digest, settings, now, path,
                                       role="subject" if subject else "mention")
            report.events_created += int(created)
            report.events_updated += int(not created)
            report.by_ticker[ticker] = report.by_ticker.get(ticker, 0) + 1
    return report


def _attach_to_event(article: NewsArticle, ticker: str, event_type: str, relevance: float, level: int,
                     text: str, digest: str | None, settings: Mapping[str, Any], now: datetime,
                     path: Path | None, role: str = "subject") -> bool:
    """Join an existing near-duplicate event for this ticker, or create one. True if created."""
    published = article.published_at or now
    window = timedelta(days=int(settings["dedup_window_days"]))
    this_hash = simhash64(text)
    with connect(path) as conn:
        candidates = conn.execute(
            "SELECT e.event_id, e.headline, e.first_seen_at, e.occurred_at, e.best_source_level, e.article_count "
            "FROM news_events e JOIN event_tickers t ON t.event_id = e.event_id "
            "WHERE t.ticker = ? AND e.event_type = ?", (ticker, event_type)).fetchall()
        match = None
        for c in candidates:
            occurred = datetime.fromisoformat(c["occurred_at"]) if c["occurred_at"] else None
            if occurred is not None and abs(occurred - published) > window:
                continue
            rows = conn.execute("SELECT body_hash, simhash FROM news_articles WHERE event_id = ?",
                                (c["event_id"],)).fetchall()
            same_body = digest is not None and any(r["body_hash"] == digest for r in rows)
            near = any(r["simhash"] and hamming(int(r["simhash"], 16), this_hash) <= int(settings["simhash_max_hamming"])
                       for r in rows)
            if same_body or near or jaccard(c["headline"], article.title) >= float(settings["dedup_similarity_threshold"]):
                match = c
                break

        if match is not None:
            best = min(int(match["best_source_level"]), level)
            conn.execute(
                "UPDATE news_events SET article_count = article_count + 1, best_source_level = ?, "
                "assertion_type = ?, credibility_score = ?, relevance_score = MAX(COALESCE(relevance_score, 0), ?) "
                "WHERE event_id = ?",
                (best, assertion_for(best, settings), _credibility(best, settings), relevance, match["event_id"]))
            conn.execute("UPDATE news_articles SET event_id = ? WHERE article_id = ?",
                         (match["event_id"], article.article_id))
            return False

        event_id = hashlib.sha1(f"{ticker}|{event_type}|{article.article_id}".encode()).hexdigest()[:20]
        conn.execute(
            "INSERT INTO news_events (event_id, event_type, headline, occurred_at, first_seen_at, article_count, "
            "primary_source_id, best_source_level, assertion_type, relevance_score, credibility_score, "
            "novelty_score, company_impact_score, time_sensitivity) VALUES (?, ?, ?, ?, ?, 1, ?, ?, ?, ?, ?, ?, ?, ?)",
            (event_id, event_type, article.title, published.isoformat(), utcnow(), article.provenance.source_id,
             level, assertion_for(level, settings), relevance, _credibility(level, settings), 1.0,
             float(settings["company_impact_by_type"].get(event_type, 0.3)),
             float(settings["time_sensitivity_by_type"].get(event_type, 0.3))))
        conn.execute("INSERT OR IGNORE INTO event_tickers (event_id, ticker, role) VALUES (?, ?, ?)",
                     (event_id, ticker, role))
        conn.execute("UPDATE news_articles SET event_id = ? WHERE article_id = ?", (event_id, article.article_id))
        return True


def _credibility(level: int, settings: Mapping[str, Any]) -> float:
    table = settings["credibility_by_source_level"]
    return float(table.get(level, table.get(str(level), 0.2)))


# ---------------------------------------------------------------------------
# Brief and annotation
# ---------------------------------------------------------------------------

def rank_events(ticker: str, days: int, path: Path | None = None, now: datetime | None = None,
                thresholds: Mapping[str, Any] | None = None) -> list[dict[str, Any]]:
    settings = news_settings(thresholds)
    now = now or datetime.now(VN_TZ)
    since = (now - timedelta(days=days)).isoformat()
    w = settings["rank_weights"]
    half_life = float(settings["novelty_half_life_days"])
    with connect(path) as conn:
        events = [dict(r) for r in conn.execute(
            "SELECT e.* FROM news_events e JOIN event_tickers t ON t.event_id = e.event_id "
            "WHERE t.ticker = ? AND e.occurred_at >= ?", (ticker.upper(), since))]
        for e in events:
            e["articles"] = [dict(r) for r in conn.execute(
                "SELECT article_id, title, url, source_id, source_level, published_at, raw_path "
                "FROM news_articles WHERE event_id = ? ORDER BY published_at", (e["event_id"],))]
    for e in events:
        age = max(0.0, (now - datetime.fromisoformat(e["occurred_at"])).total_seconds() / 86400)
        novelty = math.pow(0.5, age / half_life)
        e["novelty_now"] = round(novelty, 3)
        e["rank"] = round((e["relevance_score"] or 0) * (e["credibility_score"] or 0) * (
            w["company_impact"] * (e["company_impact_score"] or 0)
            + w["time_sensitivity"] * (e["time_sensitivity"] or 0) + w["novelty"] * novelty), 4)
    return sorted(events, key=lambda e: -e["rank"])[: int(settings["brief_max_events"])]


def render_news_brief(ticker: str, events: Sequence[Mapping[str, Any]], days: int) -> str:
    lines = [f"# Tin tức {ticker.upper()} — {days} ngày gần nhất", "",
             f"{len(events)} sự kiện đã gộp trùng, xếp theo điểm (tối đa theo `news.brief_max_events`). "
             "Cấp nguồn: 1 = sàn/doanh nghiệp, 3 = báo tài chính. INFERENCE = chưa đối chiếu với văn bản gốc.", ""]
    if not events:
        lines.append("Không có sự kiện nào trong khoảng này.")
    for i, e in enumerate(events, 1):
        lines += [f"## {i}. [{e['event_type']}] {e['headline']}",
                  f"- event_id: `{e['event_id']}` · ngày {str(e['occurred_at'])[:16]} · {e['article_count']} bài · "
                  f"cấp nguồn tốt nhất {e['best_source_level']} · {e['assertion_type']} · điểm {e['rank']}"]
        if e.get("what_happened"):
            lines.append(f"- Đã diễn giải: {e['what_happened']} → {e.get('why_it_matters') or ''} "
                         f"({e.get('direction') or '?'}, {e.get('time_horizon') or '?'})")
        for a in e["articles"]:
            docs = ""
            if a.get("raw_path"):
                try:
                    body = json.loads((PROJECT_ROOT / a["raw_path"]).read_text(encoding="utf-8"))
                    if body.get("documents"):
                        docs = " · văn bản gốc: " + ", ".join(body["documents"])
                    if body.get("origin"):
                        docs += f" · theo {body['origin']}"
                except (OSError, ValueError):
                    pass
            lines.append(f"  - `{a['article_id']}` {a['source_id']} (cấp {a['source_level']}) "
                         f"{str(a['published_at'])[:16]} — {a['title']} — {a['url']}"
                         + (f" · nội dung: `{a['raw_path']}`" if a.get("raw_path") else "") + docs)
        lines.append("")
    return "\n".join(lines) + "\n"


class AnnotationError(ValueError):
    pass


def annotate_event(event_id: str, annotation: Mapping[str, Any], path: Path | None = None) -> None:
    """Store an in-session interpretation after checking it against the event.

    Required: what_happened, why_it_matters, direction, time_horizon,
    evidence_article_ids (each must belong to this event). Optional:
    expected_impact, assertion_type (may only lower the stored strength).
    """
    for key in ("what_happened", "why_it_matters", "direction", "time_horizon", "evidence_article_ids"):
        if not annotation.get(key):
            raise AnnotationError(f"missing '{key}'")
    direction = str(annotation["direction"]).upper()
    horizon = str(annotation["time_horizon"]).upper()
    if direction not in DIRECTIONS:
        raise AnnotationError(f"direction must be one of {sorted(DIRECTIONS)}")
    if horizon not in HORIZONS:
        raise AnnotationError(f"time_horizon must be one of {sorted(HORIZONS)}")
    with connect(path) as conn:
        row = conn.execute("SELECT assertion_type FROM news_events WHERE event_id = ?", (event_id,)).fetchone()
        if row is None:
            raise AnnotationError(f"no event {event_id}")
        members = {r[0] for r in conn.execute("SELECT article_id FROM news_articles WHERE event_id = ?", (event_id,))}
        cited = set(annotation["evidence_article_ids"])
        if not cited <= members:
            raise AnnotationError(f"evidence not in this event: {sorted(cited - members)}")
        stored = row["assertion_type"]
        requested = str(annotation.get("assertion_type") or stored).upper()
        if requested not in ASSERTION_STRENGTH:
            raise AnnotationError(f"unknown assertion_type {requested}")
        if ASSERTION_STRENGTH[requested] > ASSERTION_STRENGTH[stored]:
            raise AnnotationError(f"cannot raise {stored} to {requested}: the sources do not support it")
        conn.execute(
            "UPDATE news_events SET what_happened = ?, why_it_matters = ?, direction = ?, expected_impact = ?, "
            "time_horizon = ?, assertion_type = ? WHERE event_id = ?",
            (str(annotation["what_happened"]), str(annotation["why_it_matters"]), direction,
             annotation.get("expected_impact"), horizon, requested, event_id))


def reclassify_roles(path: Path | None = None, thresholds: Mapping[str, Any] | None = None) -> int:
    """Mark stored ticker links as "mention" where the ticker is not the subject (idempotent).

    Before 2026-10-11 every link was stored as "subject". A link stays "subject" when the
    event's source is the ticker's own news page (`ticker_page_sources`) or its headline
    names the ticker; otherwise the ticker was only found in the summary.
    """
    settings = news_settings(thresholds)
    pages = {str(s) for s in settings.get("ticker_page_sources", [])}
    ambiguous, aliases = _tag_rules(settings)
    changed = 0
    with connect(path) as conn:
        universe = {r[0] for r in conn.execute("SELECT ticker FROM tickers")}
        names = {r[0]: r[1] or "" for r in conn.execute("SELECT ticker, name FROM tickers")}
        rows = conn.execute("SELECT t.event_id, t.ticker, e.headline, e.primary_source_id FROM event_tickers t "
                            "JOIN news_events e ON e.event_id = t.event_id WHERE t.role = 'subject'").fetchall()
        for event_id, ticker, headline, source in rows:
            if source in pages or ticker in tag_tickers(headline or "", universe, names, ambiguous, aliases):
                continue
            conn.execute("UPDATE event_tickers SET role = 'mention' WHERE event_id = ? AND ticker = ?", (event_id, ticker))
            changed += 1
    return changed


def retag_ambiguous(path: Path | None = None, thresholds: Mapping[str, Any] | None = None) -> dict[str, int]:
    """Drop stored links of ambiguous codes that the current rules no longer make (idempotent).

    "Công an TP.HCM bắt nhiều giám đốc..." was filed under HCM (Chứng khoán HSC; tester N03).
    A link is dropped only when the code shows in the event's stored texts (headline,
    article titles, saved bodies and summaries) and none of them tags it under the current
    rules; a link whose reason is not stored (a ticker page's own list) is kept.
    Returns the number of links dropped per ticker.
    """
    settings = news_settings(thresholds)
    ambiguous, aliases = _tag_rules(settings)
    dropped: dict[str, int] = {}
    with connect(path) as conn:
        universe = {r[0] for r in conn.execute("SELECT ticker FROM tickers")}
        names = {r[0]: r[1] or "" for r in conn.execute("SELECT ticker, name FROM tickers")}
        marks = ",".join("?" * len(ambiguous)) or "''"
        links = conn.execute(f"SELECT t.event_id, t.ticker, e.headline FROM event_tickers t JOIN news_events e "
                             f"ON e.event_id = t.event_id WHERE t.ticker IN ({marks})", tuple(ambiguous)).fetchall()
        for event_id, ticker, headline in links:
            texts = [headline or ""]
            for title, raw in conn.execute("SELECT title, raw_path FROM news_articles WHERE event_id = ?", (event_id,)):
                texts.append(title or "")
                if raw:
                    try:
                        body = json.loads((PROJECT_ROOT / raw).read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        continue
                    texts += [str(body.get("summary") or ""), str(body.get("text") or "")]
            if not any(ticker in _CODE.findall(t) for t in texts):
                continue                                     # the reason for the link is not stored: keep it
            if any(ticker in tag_tickers(t, universe, names, ambiguous, aliases) for t in texts):
                continue
            conn.execute("DELETE FROM event_tickers WHERE event_id = ? AND ticker = ?", (event_id, ticker))
            dropped[ticker] = dropped.get(ticker, 0) + 1
    return dropped
