from datetime import date

from src.collectors import logos
from src.collectors.base import ProviderUnavailable

CFG = {"max_bytes": 1_000_000, "min_bytes": 100, "retry_missing_days": 30}
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 200
JPG = b"\xff\xd8\xff\xe0" + b"\x00" * 200
BLANK = b"GIF89a\x01\x00\x01\x00\x80\xff" + b"\x00" * 31          # Vietstock's 43-byte answer to an unknown code


def site(pages):
    calls = []

    def get(url):
        calls.append(url)
        ticker = url.rsplit("/", 1)[-1]
        if ticker == "ERR":
            raise ProviderUnavailable("timeout")
        if ticker not in pages:
            return "https://finance.vietstock.vn/content/images/blank.gif", BLANK
        return url, pages[ticker]
    return get, calls


def test_the_format_comes_from_the_bytes_not_the_header():
    assert logos.sniff(PNG) == "png" and logos.sniff(JPG) == "jpg"
    assert logos.sniff(b"RIFF\x00\x00\x00\x00WEBPVP8 ") == "webp"
    assert logos.sniff(b"<html>not found</html>") is None


def test_unknown_codes_html_and_oversized_files_are_not_logos():
    get, _ = site({"AAA": PNG, "BIG": PNG + b"\x00" * 2_000_000, "TXT": b"<html>" + b"x" * 300})
    assert logos.fetch_logo("AAA", get, CFG) == (PNG, "png")
    assert logos.fetch_logo("ZZZ", get, CFG) is None
    assert logos.fetch_logo("BIG", get, CFG) is None
    assert logos.fetch_logo("TXT", get, CFG) is None


def test_collect_saves_once_remembers_misses_and_retries_them_later(tmp_path):
    get, calls = site({"AAA": PNG, "BBB": JPG})
    r = logos.collect_logos(["aaa", "BBB", "ZZZ", "ERR", "../x"], get, CFG, tmp_path, today=date(2026, 10, 3))
    assert r.saved == ["AAA", "BBB"] and r.missing == ["ZZZ"] and set(r.failed) == {"ERR", "../X"}
    assert (tmp_path / "AAA.png").read_bytes() == PNG and (tmp_path / "BBB.jpg").exists()
    assert logos.available(tmp_path) == ["AAA", "BBB"]

    calls.clear()
    r = logos.collect_logos(["AAA", "ZZZ"], get, CFG, tmp_path, today=date(2026, 10, 20))
    assert r.kept == ["AAA"] and r.skipped == ["ZZZ"] and calls == []       # nothing asked again
    r = logos.collect_logos(["ZZZ"], get, CFG, tmp_path, today=date(2026, 11, 10))
    assert r.missing == ["ZZZ"] and len(calls) == 1                           # asked again after 30 days


def test_a_refresh_replaces_a_logo_whose_format_changed(tmp_path):
    get, _ = site({"AAA": PNG})
    logos.collect_logos(["AAA"], get, CFG, tmp_path)
    get, _ = site({"AAA": JPG})
    logos.collect_logos(["AAA"], get, CFG, tmp_path, refresh=True)
    assert not (tmp_path / "AAA.png").exists() and logos.logo_file("AAA", tmp_path).name == "AAA.jpg"


def test_only_three_character_codes_map_to_a_file(tmp_path):
    (tmp_path / "AAA.png").write_bytes(PNG)
    assert logos.logo_file("AAA", tmp_path) is not None
    assert logos.logo_file("../AAA", tmp_path) is None and logos.logo_file("AAAA", tmp_path) is None
