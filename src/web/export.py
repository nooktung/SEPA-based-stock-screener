"""`python -m src.main export-site`: a read-only static copy of the dashboard (2026-10-12).

For a few viewers the user chooses: the copy is published to Cloudflare Pages behind
Cloudflare Access (scripts/deploy_site.ps1). The computing stays on this machine -- the
database, the DNSE keys and every rule never leave it; what is published is what the
local dashboard would answer, written once after the daily job:

    data/site/index.html, app.js, learn.js, style.css   the page itself (src/web/static)
    data/site/site.json                                  session, version, tickers with a chart
    data/site/data/<route>[/<k>-<v>_...].json            each /api/<route>?<query> the page asks
    data/site/logo/<TICKER>.<ext>                        company logos

Every JSON file is produced by the same `api_*` function the local server calls, so the
copy shows the same numbers. Not published: the position check (it needs the viewer's
cost), the one write route ("Của tôi"), the invariant report. Charts are exported for the
tickers the dashboard monitors; any other ticker shows its row in "Toàn thị trường" only
(~0.6 MB of chart data per ticker: the whole market would be ~800 MB a day).

Nothing is written over the published copy unless the data is the latest closed session
and the latest screen run is of that session; a value from .env found anywhere in the
output stops the export. Paths follow `static_path`, mirrored by app.js `staticPath`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Mapping

from src.config import PROJECT_ROOT, load_config
from src.database import connect

STATIC = PROJECT_ROOT / "src" / "web" / "static"
OUT_DIR = PROJECT_ROOT / "data" / "site"
PAGE_FILES = ("index.html", "app.js", "learn.js", "style.css")
# The queries app.js sends (loadFlow, loadAlerts, loadNews): the copy must hold exactly these.
FLOW_SESSIONS = "30"
ALERTS_LIMIT = "1000"
NEWS_DAYS = "30"
_SAFE = re.compile(r"[^A-Za-z0-9_-]")


class ExportRefused(RuntimeError):
    """The data is not fit to replace the published copy."""


def static_path(route: str, query: Mapping[str, str] | None = None) -> str:
    """'ticker', {'ticker': 'ABB'} -> 'data/ticker/ticker-ABB.json' (keys sorted; app.js staticPath)."""
    safe = lambda s: _SAFE.sub("_", str(s))  # noqa: E731
    items = sorted((query or {}).items())
    if not items:
        return f"data/{safe(route)}.json"
    return f"data/{safe(route)}/" + "_".join(f"{safe(k)}-{safe(v)}" for k, v in items) + ".json"


def expected_session(now: datetime, path: Path | None = None) -> date:
    """The latest session whose bars are final at `now` (Vietnam time), from the stored calendar."""
    from src.market_calendar import is_trading_day  # noqa: PLC0415
    from src.pipeline import _closed_after, settings  # noqa: PLC0415

    day = now.date()
    if not (is_trading_day(day, path) and now.time() >= _closed_after(settings())):
        day -= timedelta(days=1)
        while not is_trading_day(day, path):
            day -= timedelta(days=1)
    return day


def freshness(now: datetime, path: Path | None = None) -> dict[str, Any]:
    """What the gate compares: the expected session against the stored index, screen and states."""
    from src.quant.screener import screen_settings  # noqa: PLC0415

    with connect(path) as conn:
        index = conn.execute("SELECT MAX(trade_date) FROM index_daily WHERE symbol = ?",
                             (screen_settings()["market"]["index"],)).fetchone()[0]
        run = conn.execute("SELECT run_id, as_of_date FROM screen_runs ORDER BY created_at DESC LIMIT 1").fetchone()
        state = conn.execute("SELECT MAX(trade_date) FROM monitor_state").fetchone()[0]
    return {"expected": expected_session(now, path).isoformat(), "index": index,
            "run_id": run[0] if run else None, "run_session": run[1] if run else None, "monitor": state}


def check_fresh(f: Mapping[str, Any]) -> None:
    problems = [f"{name} {f[key]} (cần {f['expected']})" for name, key in
                (("giá VN-Index tới phiên", "index"), ("lượt lọc mới nhất của phiên", "run_session"),
                 ("trạng thái theo dõi tới phiên", "monitor")) if f[key] != f["expected"]]
    if problems:
        raise ExportRefused("dữ liệu chưa đủ mới: " + "; ".join(problems))


def secret_values(env_file: Path | None = None) -> list[str]:
    """Values from .env long enough to recognise (the export must contain none of them)."""
    env_file = env_file or PROJECT_ROOT / ".env"
    out = []
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            m = re.match(r"\s*([A-Za-z_][A-Za-z0-9_]*)\s*=\s*(.*)", line)
            if m:
                value = m.group(2).strip().strip('"').strip("'")
                if len(value) >= 8 and m.group(1).upper() != "TZ":
                    out.append(value)
    return out


@dataclass
class ExportReport:
    out_dir: Path
    session: str
    files: int = 0
    bytes: int = 0
    detail: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)


def export_site(out_dir: Path | None = None, path: Path | None = None, now: datetime | None = None,
                force: bool = False, runs: int | None = None, env_file: Path | None = None,
                logo_dir: Path | None = None) -> ExportReport:
    """Write the static copy into a fresh folder, then swap it in; the old copy stays on any failure."""
    from src.collectors import logos  # noqa: PLC0415
    from src.web import server  # noqa: PLC0415

    cfg = load_config().thresholds.raw.get("site", {})
    out_dir = Path(out_dir or PROJECT_ROOT / cfg.get("out_dir", "data/site"))
    now = now or datetime.now(server.VN_TZ)
    fresh = freshness(now, path)
    if not force:
        check_fresh(fresh)
    n_runs = int(runs or cfg.get("runs", 5))

    work = out_dir.with_name(out_dir.name + ".new")
    if work.exists():
        shutil.rmtree(work)
    (work / "data").mkdir(parents=True)
    report = ExportReport(out_dir, fresh["run_session"] or "")

    def write(route: str, query: Mapping[str, str], fn: Callable[..., Any]) -> bool:
        try:
            payload = server._clean(fn(dict(query), path))
        except KeyError as exc:                     # the local server would answer 404: no file either
            report.skipped[static_path(route, query)] = str(exc)
            return False
        target = work / static_path(route, query)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(json.dumps(payload, ensure_ascii=False, default=str, allow_nan=False), encoding="utf-8")
        return True

    run_list = server.api_runs({}, path)[:n_runs]
    write_json(work / static_path("runs"), server._clean(run_list))
    for q in [{}] + [{"run_id": r["run_id"]} for r in run_list]:
        write("screen", q, server.api_screen)
        write("stocks", q, server.api_stocks)
        write("industries", q, server.api_industries)
    for route, q, fn in (("market", {}, server.api_market), ("alerts", {"limit": ALERTS_LIMIT}, server.api_alerts),
                         ("news", {"days": NEWS_DAYS}, server.api_news), ("index", {}, server.api_index),
                         ("intraday", {}, server.api_intraday),
                         ("bars", {"index": "VNINDEX", "tf": "W"}, server.api_bars),
                         ("bars", {"index": "VNINDEX", "tf": "M"}, server.api_bars)):
        write(route, q, fn)

    # Charts for the monitored tickers (the rows of the latest run's table).
    tickers = sorted({r["ticker"] for r in server.api_stocks({}, path)})
    for t in tickers:
        if write("ticker", {"ticker": t}, server.api_ticker):
            report.detail.append(t)
            for tf in ("W", "M"):
                write("bars", {"ticker": t, "tf": tf}, server.api_bars)
            write("flow", {"ticker": t, "sessions": FLOW_SESSIONS}, server.api_flow)

    # Logos: copied with their own extension; the page reads the name from site.json.
    logo_files: dict[str, str] = {}
    (work / "logo").mkdir()
    for t in logos.available(logo_dir or logos.LOGO_DIR):
        src = logos.logo_file(t, logo_dir or logos.LOGO_DIR)
        if src is not None:
            shutil.copy2(src, work / "logo" / src.name)
            logo_files[t] = src.name
    write_json(work / static_path("logos"), sorted(logo_files))

    for name in PAGE_FILES:
        shutil.copy2(STATIC / name, work / name)

    digest = hashlib.sha1()
    for f in sorted((work / "data").rglob("*.json")):
        digest.update(f.read_bytes())
    for name in PAGE_FILES:
        digest.update((work / name).read_bytes())
    meta = {"static": True, "session": fresh["run_session"], "run_id": fresh["run_id"],
            "generated_at": now.isoformat(timespec="seconds"), "version": digest.hexdigest()[:16],
            "detail": report.detail, "logos": logo_files}
    write_json(work / "site.json", meta)

    # No value from .env may leave this machine.
    secrets = secret_values(env_file)
    for f in work.rglob("*"):
        if f.is_file() and f.suffix in (".json", ".html", ".js", ".css"):
            text = f.read_text(encoding="utf-8", errors="ignore")
            if any(s in text for s in secrets):
                shutil.rmtree(work)
                raise ExportRefused(f"{f.relative_to(work)} chứa một giá trị trong .env — dừng, không xuất bản")

    report.files = sum(1 for f in work.rglob("*") if f.is_file())
    report.bytes = sum(f.stat().st_size for f in work.rglob("*") if f.is_file())
    if out_dir.exists():
        shutil.rmtree(out_dir)
    os.replace(work, out_dir)
    return report


def write_json(target: Path, payload: Any) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, default=str, allow_nan=False), encoding="utf-8")
