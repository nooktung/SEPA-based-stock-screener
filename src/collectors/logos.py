"""Company logos for the dashboard (display only, never used in any score).

Source: Vietstock's company image, the same picture its profile page shows
(found 2026-10-03 on https://finance.vietstock.vn/<TICKER>/ho-so-doanh-nghiep.htm
as ``<img src=/image/PHP>``):

    https://finance.vietstock.vn/image/<TICKER>

The server labels every image ``image/jpeg`` whatever it is (PHP is a PNG), so
the format is read from the file's own magic bytes. An unknown code is
redirected to ``/content/images/blank.gif`` (1 x 1 pixel, 43 bytes): that, or
anything that is not an image, is recorded as "no logo" and asked again only
after ``logos.retry_missing_days``. Logos are saved once under ``data/logos/``
and served by the local dashboard, so pages load without calling Vietstock.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from pathlib import Path
from typing import Callable, Iterable, Mapping

from src.collectors.base import ProviderError
from src.database import PROJECT_ROOT

LOGO_URL = "https://finance.vietstock.vn/image/{t}"
LOGO_DIR = PROJECT_ROOT / "data" / "logos"
MISSING_FILE = "_missing.json"
TICKER = re.compile(r"^[A-Z0-9]{3}$")
# magic bytes -> (file extension, content type)
FORMATS: tuple[tuple[bytes, str, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", "png", "image/png"),
    (b"\xff\xd8\xff", "jpg", "image/jpeg"),
    (b"GIF87a", "gif", "image/gif"),
    (b"GIF89a", "gif", "image/gif"),
)
CONTENT_TYPES = {ext: ctype for _, ext, ctype in FORMATS} | {"webp": "image/webp"}


def sniff(content: bytes) -> str | None:
    """File extension from the bytes themselves, or None when it is not an image."""
    for magic, ext, _ in FORMATS:
        if content.startswith(magic):
            return ext
    if content[:4] == b"RIFF" and content[8:12] == b"WEBP":
        return "webp"
    return None


def logo_file(ticker: str, folder: Path | None = None) -> Path | None:
    """The stored logo of a ticker, whatever its format."""
    if not TICKER.match(ticker or ""):
        return None
    folder = folder or LOGO_DIR
    for ext in CONTENT_TYPES:
        path = folder / f"{ticker}.{ext}"
        if path.is_file():
            return path
    return None


def available(folder: Path | None = None) -> list[str]:
    folder = folder or LOGO_DIR
    if not folder.is_dir():
        return []
    return sorted({p.stem for p in folder.iterdir() if p.suffix[1:] in CONTENT_TYPES and TICKER.match(p.stem)})


def fetch_logo(ticker: str, get: Callable[[str], tuple[str, bytes]], cfg: Mapping[str, int]) -> tuple[bytes, str] | None:
    """(image bytes, extension), or None when Vietstock has no logo for the code."""
    final_url, content = get(LOGO_URL.format(t=ticker))
    if "blank" in final_url.lower() or not (int(cfg["min_bytes"]) <= len(content) <= int(cfg["max_bytes"])):
        return None
    ext = sniff(content)
    return (content, ext) if ext else None


@dataclass
class LogoReport:
    saved: list[str] = field(default_factory=list)
    kept: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)      # known to have no logo, asked recently
    failed: dict[str, str] = field(default_factory=dict)


def collect_logos(tickers: Iterable[str], get: Callable[[str], tuple[str, bytes]], cfg: Mapping[str, int],
                  folder: Path | None = None, refresh: bool = False, today: date | None = None) -> LogoReport:
    folder = folder or LOGO_DIR
    folder.mkdir(parents=True, exist_ok=True)
    today = today or date.today()
    missing_path = folder / MISSING_FILE
    try:
        known_missing: dict[str, str] = json.loads(missing_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        known_missing = {}
    retry_before = today - timedelta(days=int(cfg["retry_missing_days"]))
    report = LogoReport()
    for ticker in dict.fromkeys(t.upper() for t in tickers):
        if not TICKER.match(ticker):
            report.failed[ticker] = "không phải mã 3 ký tự"
            continue
        if not refresh and logo_file(ticker, folder):
            report.kept.append(ticker)
            continue
        asked = known_missing.get(ticker)
        if not refresh and asked and date.fromisoformat(asked) > retry_before:
            report.skipped.append(ticker)
            continue
        try:
            found = fetch_logo(ticker, get, cfg)
        except ProviderError as exc:
            report.failed[ticker] = str(exc)
            continue
        if found is None:
            known_missing[ticker] = today.isoformat()
            report.missing.append(ticker)
            continue
        content, ext = found
        old = logo_file(ticker, folder)
        if old and old.suffix[1:] != ext:
            old.unlink()
        (folder / f"{ticker}.{ext}").write_bytes(content)
        known_missing.pop(ticker, None)
        report.saved.append(ticker)
    missing_path.write_text(json.dumps(known_missing, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8")
    return report
