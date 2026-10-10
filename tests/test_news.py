"""News collection and pipeline, offline, on fragments shaped like the live pages (2026-09-27)."""
from datetime import datetime, timedelta, timezone

import pytest

from src.collectors.base import NewsArticle, Provenance, SourceLevel
from src.collectors.providers.cafef_news import CafefTickerNewsProvider, parse_list
from src.collectors.providers.rss_provider import RssNewsProvider, parse_feed
from src.collectors.providers.web_news import ArticleBody, article_id_for, canonical_url, extract_body
from src.database import connect, init_db, sync_reference_data
from src.processors.news_pipeline import (
    AnnotationError,
    annotate_event,
    assertion_for,
    classify,
    news_settings,
    process_articles,
    rank_events,
    render_news_brief,
    tag_tickers,
)

VN = timezone(timedelta(hours=7))
NOW = datetime(2026, 9, 26, 12, 0, tzinfo=VN)
SETTINGS = news_settings()

LIST_FRAGMENT = """
<li><span class="timeTitle">25/09/2026 16:31</span>
<a class='docnhanhTitle' href="/du-lieu/SSI-2984336/ssi-giay-chung-nhan.chn?utm_source=du-lieu" title="SSI: Giấy chứng nhận đăng ký doanh nghiệp thay đổi lần thứ 38">x</a></li>
<li><span class="timeTitle">20/07/2026 18:22</span>
<a class='docnhanhTitle' href="https://cafef.vn/ssi-cong-bo-loi-nhuan-188260720182228464.chn" title="SSI công bố lợi nhuận trước thuế quý 2 tăng 32%">x</a></li>
"""

DISCLOSURE_PAGE = """<html><body><div id="newscontent"><div class="content"><p>Sở Giao dịch Chứng khoán
TP.HCM Quyết định chấp thuận cho SSI thay đổi đăng ký niêm yết.</p></div>
<div class="FileWrapper"><a href="https://cafefnew.mediacdn.vn/download/170926/ssi-qd.pdf?v=1">QD.pdf</a></div>
<p>Theo HOSE</p></div></body></html>"""

ARTICLE_PAGE = """<html><body><h1 class="title">SSI công bố lợi nhuận</h1><p class="sapo">Lũy kế 6 tháng
lợi nhuận trước thuế 3.122 tỷ đồng.</p><div class="detail-content"><p>SSI:</p><p>Giá hiện tại</p>
<p>CTCP Chứng khoán SSI (HOSE: SSI) vừa công bố báo cáo tài chính quý II.</p><script>x()</script></div></body></html>"""

RSS = """<?xml version="1.0" encoding="utf-8"?><rss version="2.0"><channel>
<item><title>GMD lãi quý 3 tăng mạnh</title><link>http://vietstock.vn/2026/09/gmd-lai-737-1.htm</link>
<description>&lt;img src='x.jpg'/&gt;Gemadept (HOSE: GMD) báo lãi tăng.</description>
<pubDate>Sat, 26 Sep 2026 09:00:00 +0700</pubDate></item>
<item><title>Tỷ giá USD hôm nay</title><link>http://vietstock.vn/2026/09/ty-gia-1.htm</link>
<description>Giá USD tăng nhẹ so với VND.</description><pubDate>Sat, 26 Sep 26 08:00:00 +0700</pubDate></item>
<item><title>Tin cũ</title><link>http://vietstock.vn/2026/01/cu.htm</link><description>x</description>
<pubDate>Mon, 05 Jan 2026 08:00:00 +0700</pubDate></item>
</channel></rss>"""


# -- collection ------------------------------------------------------------------

def test_cafef_list_rows_are_parsed_with_vietnam_time_and_clean_urls():
    rows = parse_list(LIST_FRAGMENT, "https://cafef.vn")
    assert rows[0][0] == datetime(2026, 9, 25, 16, 31, tzinfo=VN)
    assert rows[0][1] == "https://cafef.vn/du-lieu/SSI-2984336/ssi-giay-chung-nhan.chn"   # query dropped
    assert rows[1][2].startswith("SSI công bố")


def test_cafef_provider_stops_at_since_and_hints_the_ticker():
    p = CafefTickerNewsProvider("cafef", 3, {"page_size": 30}, fetcher=lambda url: LIST_FRAGMENT)
    arts = p.fetch_articles(["ssi"], since=datetime(2026, 9, 1, tzinfo=VN))
    assert [a.tickers_hint for a in arts] == [("SSI",)]
    assert arts[0].provenance.source_level == SourceLevel.MAJOR_FINANCIAL_NEWS


def test_same_url_with_different_tracking_gives_one_id():
    a = "https://cafef.vn/du-lieu/SSI-1/x.chn?utm_source=a"
    b = "http://cafef.vn/du-lieu/SSI-1/x.chn?utm_source=b"
    assert article_id_for(a) == article_id_for(b)
    assert canonical_url("//cafef.vn/a.chn") == "https://cafef.vn/a.chn"


def test_disclosure_body_keeps_origin_and_original_document():
    body = extract_body("https://cafef.vn/du-lieu/SSI-1/x.chn", DISCLOSURE_PAGE)
    assert "đăng ký niêm yết" in body.text
    assert body.origin == "HOSE"
    assert body.document_links == ["https://cafefnew.mediacdn.vn/download/170926/ssi-qd.pdf"]


def test_article_body_drops_scripts_and_price_widget():
    body = extract_body("https://cafef.vn/ssi-188260720182228464.chn", ARTICLE_PAGE)
    assert body.summary.startswith("Lũy kế")
    assert "Giá hiện tại" not in body.text and "x()" not in body.text
    assert "HOSE: SSI" in body.text


def test_rss_feed_parses_two_digit_years_and_filters_by_date():
    items = parse_feed(RSS)
    assert items[1][0].year == 2026                      # "26 Sep 26" as CafeF writes it
    assert "img" not in items[0][3]
    p = RssNewsProvider("vietstock", 3, {"feeds": ["f"]}, fetcher=lambda url: RSS)
    arts = p.fetch_articles([], since=datetime(2026, 9, 20, tzinfo=VN))
    assert len(arts) == 2


# -- tagging, typing, assertion ------------------------------------------------------

UNIVERSE = {"SSI", "GMD", "VND", "HPG", "USD"}
NAMES = {"VND": "CTCP Chứng khoán VNDIRECT", "USD": "CTCP Công trình Đô thị Sóc Trăng"}
AMBIGUOUS = {"VND", "USD"}


def test_ambiguous_codes_need_a_cue_or_the_company_name():
    assert tag_tickers("Giá USD tăng nhẹ so với VND.", UNIVERSE, NAMES, AMBIGUOUS) == set()
    assert tag_tickers("Cổ phiếu VND tăng trần", UNIVERSE, NAMES, AMBIGUOUS) == {"VND"}
    assert tag_tickers("Chứng khoán VNDIRECT (VND) báo lãi", UNIVERSE, NAMES, AMBIGUOUS) == {"VND"}
    assert tag_tickers("Gemadept (HOSE: GMD) và SSI", UNIVERSE, NAMES, AMBIGUOUS) == {"GMD", "SSI"}


def test_codes_inside_longer_words_are_not_tickers():
    assert tag_tickers("SSIxyz và HPG2026", UNIVERSE, NAMES, AMBIGUOUS) == set()


def test_event_rules_are_ordered():
    rules = SETTINGS["event_rules"]
    assert classify("Thông báo nhận Quyết định xử phạt vi phạm hành chính về thuế", rules) == "LEGAL"
    assert classify("Ông Nam đăng ký mua 5.000.000 cp", rules) == "INSIDER"
    assert classify("Ngày GDKHQ chi trả cổ tức năm 2025", rules) == "DIVIDEND"
    assert classify("Công bố lợi nhuận quý 2 tăng 32%", rules) == "EARNINGS"
    assert classify("Chuyện trò cùng CEO", rules) == "OTHER"


@pytest.mark.parametrize("title, summary, expected", [
    # Real headlines that the first rule set got wrong (2026-09-27).
    ("Vinhomes phát hành lô trái phiếu trị giá 1.000 tỷ đồng", "Năm ngoái công ty trả cổ tức...", "CAPITAL_RAISING"),
    ("VPI: CBTT lập DSCĐ lấy ý kiến cổ đông bằng văn bản và hủy thông báo ngày ĐKCC", "", "SHAREHOLDER_MEETING"),
    ("BAF: Thông báo thay đổi nhân sự", "", "GOVERNANCE"),
    ("PET: Đính chính Báo cáo tình hình quản trị 6 tháng đầu năm 2026", "", "GOVERNANCE"),
    ("HCM: Thông báo thay đổi số lượng cổ phiếu có quyền biểu quyết đang lưu hành", "", "CAPITAL_RAISING"),
    ("HCM: Nghị quyết HĐQT về việc triển khai phương án chào bán cổ phiếu riêng lẻ", "", "CAPITAL_RAISING"),
    ("VietinBank Capital bán ra 7,25 triệu cổ phiếu PET", "", "INSIDER"),
    # No cue in the headline -> the summary decides.
    ("Hàng qua cảng tăng gần 22%, ba doanh nghiệp được dự báo lãi tăng", "lợi nhuận quý III", "EARNINGS"),
])
def test_headline_decides_the_type(title, summary, expected):
    from src.processors.news_pipeline import classify_event
    assert classify_event(title, summary, SETTINGS["event_rules"]) == expected


def test_assertion_follows_source_level():
    assert assertion_for(1, SETTINGS) == "FACT"
    assert assertion_for(3, SETTINGS) == "INFERENCE"
    assert assertion_for(5, SETTINGS) == "RUMOUR"


# -- pipeline --------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path):
    path = tmp_path / "news.db"
    init_db(path)
    sync_reference_data(path)
    with connect(path) as conn:
        for t, name in (("GMD", "CTCP Gemadept"), ("HPG", "CTCP Tập đoàn Hòa Phát")):
            conn.execute("INSERT OR IGNORE INTO tickers (ticker, exchange, name, created_at, updated_at) "
                         "VALUES (?, 'HOSE', ?, 'x', 'x')", (t, name))
    return path


def art(title, url, source="cafef", level=3, hint=(), body=None, when=NOW - timedelta(hours=3)):
    return NewsArticle(article_id=article_id_for(url), title=title, url=url, published_at=when,
                       provenance=Provenance(source, SourceLevel(level), NOW, url), tickers_hint=hint, body=body)


def fake_body(article):
    return ArticleBody(text=f"{article.title}. Nội dung chi tiết về kết quả kinh doanh.", summary="Tóm tắt")


def run(db, articles, tmp_path, focus=frozenset({"SSI", "GMD"})):
    return process_articles(articles, set(focus), fetch_body=fake_body, path=db,
                            body_dir=tmp_path / "bodies", now=NOW)


def test_articles_become_events_and_duplicates_collapse(db, tmp_path):
    report = run(db, [
        art("SSI công bố lợi nhuận quý 2 tăng 32%", "https://cafef.vn/a1.chn", hint=("SSI",)),
        art("SSI công bố lợi nhuận quý 2 tăng 32%", "https://vietstock.vn/a2.htm", source="vietstock"),
        art("Gemadept (GMD) lãi quý 3 tăng mạnh", "https://vietstock.vn/a3.htm", source="vietstock"),
    ], tmp_path)
    assert report.new_articles == 3 and report.events_created == 2 and report.events_updated == 1
    events = rank_events("SSI", 30, path=db, now=NOW)
    assert len(events) == 1 and events[0]["article_count"] == 2
    assert events[0]["event_type"] == "EARNINGS" and events[0]["assertion_type"] == "INFERENCE"


def test_unrelated_articles_are_stored_but_bodies_not_fetched(db, tmp_path):
    report = run(db, [art("Tỷ giá USD hôm nay", "https://vietstock.vn/fx.htm", source="vietstock")], tmp_path)
    assert report.new_articles == 1 and report.bodies_fetched == 0 and report.events_created == 0


def test_rerun_is_idempotent(db, tmp_path):
    items = [art("SSI công bố lợi nhuận", "https://cafef.vn/a1.chn", hint=("SSI",))]
    run(db, items, tmp_path)
    again = run(db, items, tmp_path)
    assert again.new_articles == 0 and again.events_created == 0


def test_brief_is_capped_and_cites_articles(db, tmp_path):
    items = [art(f"SSI sự kiện số {i} khác hẳn nhau về nội dung {i * 7}", f"https://cafef.vn/e{i}.chn", hint=("SSI",),
                 when=NOW - timedelta(days=i)) for i in range(10)]
    run(db, items, tmp_path)
    events = rank_events("SSI", 30, path=db, now=NOW)
    assert len(events) == SETTINGS["brief_max_events"]
    brief = render_news_brief("SSI", events, 30)
    assert events[0]["event_id"] in brief and events[0]["articles"][0]["article_id"] in brief


def test_annotation_is_checked_before_it_is_stored(db, tmp_path):
    run(db, [art("SSI công bố lợi nhuận quý 2 tăng 32%", "https://cafef.vn/a1.chn", hint=("SSI",))], tmp_path)
    e = rank_events("SSI", 30, path=db, now=NOW)[0]
    good = {"what_happened": "LNTT quý 2 tăng 32%", "why_it_matters": "Tự doanh và môi giới cùng tăng",
            "direction": "POSITIVE", "time_horizon": "QUARTERS",
            "evidence_article_ids": [e["articles"][0]["article_id"]]}
    with pytest.raises(AnnotationError, match="cannot raise"):
        annotate_event(e["event_id"], {**good, "assertion_type": "FACT"}, path=db)
    with pytest.raises(AnnotationError, match="evidence"):
        annotate_event(e["event_id"], {**good, "evidence_article_ids": ["not-in-event"]}, path=db)
    with pytest.raises(AnnotationError, match="direction"):
        annotate_event(e["event_id"], {**good, "direction": "BUY"}, path=db)
    annotate_event(e["event_id"], good, path=db)
    assert rank_events("SSI", 30, path=db, now=NOW)[0]["what_happened"] == "LNTT quý 2 tăng 32%"


def test_forum_sources_are_always_rumour(db, tmp_path):
    run(db, [art("Tin đồn SSI sắp được thâu tóm", "https://f319.com/t1", source="forum_sentiment", level=5,
                 hint=("SSI",))], tmp_path)
    assert rank_events("SSI", 30, path=db, now=NOW)[0]["assertion_type"] == "RUMOUR"


def test_no_module_calls_the_claude_api():
    """User decision 2026-09-27: analysis happens in the Claude Code session, not via the API."""
    from pathlib import Path
    src = Path(__file__).resolve().parent.parent / "src"
    offenders = [p for p in src.rglob("*.py") if "import anthropic" in p.read_text(encoding="utf-8")
                 or "from anthropic" in p.read_text(encoding="utf-8")]
    assert offenders == []


def test_a_ticker_found_only_in_the_summary_is_a_mention_not_the_subject(db, tmp_path):
    """"Một cổ phiếu bị khối ngoại xả 9.600 tỷ" (MCH) was filed as VHM news (tester report B06)."""
    from src.processors.news_pipeline import reclassify_roles

    run(db, [art("Một cổ phiếu bất ngờ bị khối ngoại xả hơn 9.600 tỷ đồng", "https://cafef.vn/rss1.chn",
                 source="cafef_rss", body="Trong khi đó GMD được mua ròng mạnh.")], tmp_path)
    with connect(db) as conn:
        assert [tuple(r) for r in conn.execute("SELECT ticker, role FROM event_tickers")] == [("GMD", "mention")]
        # links stored before roles existed are re-marked; a ticker-page source keeps "subject"
        conn.execute("UPDATE event_tickers SET role = 'subject'")
    assert reclassify_roles(db) == 1 and reclassify_roles(db) == 0
