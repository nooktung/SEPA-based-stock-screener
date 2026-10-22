"""`python -m src.main export-site`: a read-only static copy of the dashboard (2026-10-12).

For a few viewers the user chooses: the copy is published to Cloudflare Pages behind the
copy's own login (scripts/deploy_site.ps1, cloudflare/functions/_middleware.js). The computing stays on this machine -- the
database, the DNSE keys and every rule never leave it; what is published is what the
local dashboard would answer, written once after the daily job:

    data/site/index.html, app.js, learn.js, style.css   the page itself (src/web/static)
    data/site/site.json                                  session, version, tickers with a chart
    data/site/data/<route>[/<k>-<v>_...].json            each /api/<route>?<query> the page asks
    data/site/logo/<TICKER>.<ext>                        company logos

Every JSON file is produced by the same `api_*` function the local server calls, so the
copy shows the same numbers. Not published: the position check (it needs the viewer's
cost), the one write route ("Của tôi"), the invariant report. Charts are exported for the
tickers the dashboard monitors and every ticker shown in "Toàn thị trường".
The larger snapshot lets viewers open any market row without adding it to a watchlist.

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
PAGE_FILES = ("index.html", "app.js", "learn.js", "news-dates.js", "style.css", "404.html")
# The flow query app.js sends: the copy must hold exactly this.
FLOW_SESSIONS = "30"
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
    mode: str = "full"                    # "update": only what changed (update_site)


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

    write = _api_writer(work, path, report)
    run_list = server.api_runs({}, path)[:n_runs]
    write_json(work / static_path("runs"), server._clean(run_list))
    for q in [{}] + [{"run_id": r["run_id"]} for r in run_list]:
        write("screen", q, server.api_screen)
        write("stocks", q, server.api_stocks)
        write("industries", q, server.api_industries)
    for route, q, fn in (("market", {}, server.api_market),
                         ("news", {}, server.api_news), ("index", {}, server.api_index),
                         ("bars", {"index": "VNINDEX", "tf": "W"}, server.api_bars),
                         ("bars", {"index": "VNINDEX", "tf": "M"}, server.api_bars)):
        write(route, q, fn)

    # Every clickable market row must have the same detail routes as a monitored ticker.
    _write_details(write, sorted(_detail_tickers(server.api_stocks({}, path), path)), report)

    # Logos: copied with their own extension; the page reads the name from site.json.
    logo_files: dict[str, str] = {}
    (work / "logo").mkdir()
    for t in logos.available(logo_dir or logos.LOGO_DIR):
        src = logos.logo_file(t, logo_dir or logos.LOGO_DIR)
        if src is not None:
            shutil.copy2(src, work / "logo" / src.name)
            logo_files[t] = src.name
    write_json(work / static_path("logos"), sorted(logo_files))
    # The tickers the web list may take (cloudflare/functions/api/mylist.js): listed shares with prices.
    with connect(path) as conn:
        write_json(work / "data" / "universe.json", [r[0] for r in conn.execute(
            "SELECT t.ticker FROM tickers t WHERE EXISTS (SELECT 1 FROM prices_daily p WHERE p.ticker = t.ticker) "
            "ORDER BY t.ticker")])

    for name in PAGE_FILES:
        shutil.copy2(STATIC / name, work / name)
    # Cloudflare Pages: every path goes through the login (cloudflare/functions/_middleware.js) except
    # the company logos -- public images, most of a page's requests (the free plan counts invocations).
    write_json(work / "_routes.json", {"version": 1, "include": ["/*"], "exclude": ["/logo/*"]})

    digest = hashlib.sha1()
    for f in sorted((work / "data").rglob("*.json")):
        digest.update(f.read_bytes())
    for name in PAGE_FILES:
        digest.update((work / name).read_bytes())
    meta = {"static": True, "session": fresh["run_session"], "run_id": fresh["run_id"],
            "generated_at": now.isoformat(timespec="seconds"), "version": digest.hexdigest()[:16],
            "detail": report.detail, "logos": logo_files,
            # Read by cloudflare/functions/api/mylist.js: one list per account of the login.
            "local_account": cfg.get("local_account", "admin"), "mylist_max": int(cfg.get("mylist_max_per_account", 40))}
    write_json(work / "site.json", meta)

    _refuse_secrets(work, env_file)
    report.files = sum(1 for f in work.rglob("*") if f.is_file())
    report.bytes = sum(f.stat().st_size for f in work.rglob("*") if f.is_file())
    if out_dir.exists():
        shutil.rmtree(out_dir)
    os.replace(work, out_dir)
    return report


def write_json(target: Path, payload: Any) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(payload, ensure_ascii=False, default=str, allow_nan=False), encoding="utf-8")


def _api_writer(work: Path, path: Path | None, report: ExportReport) -> Callable[..., bool]:
    """Write one /api/<route>?<query> answer under `work` at its static path."""
    from src.web import server  # noqa: PLC0415

    def write(route: str, query: Mapping[str, str], fn: Callable[..., Any]) -> bool:
        try:
            payload = server._clean(fn(dict(query), path))
        except KeyError as exc:                     # the local server would answer 404: no file either
            report.skipped[static_path(route, query)] = str(exc)
            return False
        write_json(work / static_path(route, query), payload)
        return True
    write.export_folder = work
    write.export_db = path
    return write


def _detail_tickers(rows: list[dict[str, Any]], path: Path | None) -> set[str]:
    from src.web import server  # noqa: PLC0415

    return {r["ticker"] for r in rows} | {r["ticker"] for r in server.api_market({}, path)["rows"]}


def _detail_job(args):
    """One process writes one ticker, using the same public API functions."""
    ticker, folder, path = args
    report = ExportReport(Path(folder), "")
    write = _api_writer(Path(folder), path, report)
    _write_details_serial(write, [ticker], report)
    return report.detail, report.skipped


def _write_details(write: Callable[..., bool], tickers: list[str], report: ExportReport) -> None:
    """Bounded parallel CPU work; no network calls or changed chart formulas."""
    from concurrent.futures import ProcessPoolExecutor

    cfg = load_config().thresholds.raw.get("site", {})
    workers = max(1, min(int(cfg.get("export_workers", 4)), os.cpu_count() or 1, 4))
    folder = getattr(write, "export_folder", None)
    if workers == 1 or len(tickers) < 16 or folder is None:
        _write_details_serial(write, tickers, report)
        return
    with ProcessPoolExecutor(max_workers=workers) as pool:
        jobs = ((t, folder, getattr(write, "export_db", None)) for t in tickers)
        for n, (detail, skipped) in enumerate(pool.map(_detail_job, jobs, chunksize=1), 1):
            report.detail.extend(detail)
            report.skipped.update(skipped)
            if n % 100 == 0 or n == len(tickers):
                print(f"Export charts: {n}/{len(tickers)} ({workers} workers)", flush=True)


def _write_details_serial(write: Callable[..., bool], tickers: list[str], report: ExportReport) -> None:
    """A ticker's chart page: its detail, weekly / monthly bars and foreign flow."""
    from src.web import server  # noqa: PLC0415

    for t in tickers:
        if write("ticker", {"ticker": t}, server.api_ticker):
            report.detail.append(t)
            for tf in ("W", "M"):
                write("bars", {"ticker": t, "tf": tf}, server.api_bars)
            write("flow", {"ticker": t, "sessions": FLOW_SESSIONS}, server.api_flow)


def _refuse_secrets(work: Path, env_file: Path | None) -> None:
    """No value from .env may leave this machine: the staged files are dropped on any match."""
    secrets = secret_values(env_file)
    for f in work.rglob("*"):
        if f.is_file() and f.suffix in (".json", ".html", ".js", ".css"):
            text = f.read_text(encoding="utf-8", errors="ignore")
            if any(s in text for s in secrets):
                shutil.rmtree(work)
                raise ExportRefused(f"{f.relative_to(work)} chứa một giá trị trong .env — dừng, không xuất bản")


def update_site(out_dir: Path | None = None, path: Path | None = None, now: datetime | None = None,
                force: bool = False, env_file: Path | None = None, logo_dir: Path | None = None) -> ExportReport:
    """`export-site --update` (2026-10-14): after a list changed (scripts/mylist_poll.ps1), write only
    what a new ticker changes into the published copy -- the latest run's tables (stocks, screen,
    industries), the market table and the news -- plus the chart pages and logos of the tickers the
    copy does not hold yet, and the page files. ~2 minutes instead of rewriting every ticker's charts
    (~1.9 s each, ~12 minutes with ten accounts' lists). Older runs' tables wait for the full export
    of the daily job. Same gate and .env check as the full export: the files are staged, checked,
    then moved in, site.json last. A copy of another session or screen run, or none, gets the full
    export instead (report.mode)."""
    from src.collectors import logos  # noqa: PLC0415
    from src.web import server  # noqa: PLC0415

    cfg = load_config().thresholds.raw.get("site", {})
    out_dir = Path(out_dir or PROJECT_ROOT / cfg.get("out_dir", "data/site"))
    now = now or datetime.now(server.VN_TZ)
    fresh = freshness(now, path)
    if not force:
        check_fresh(fresh)
    try:
        meta = json.loads((out_dir / "site.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        meta = None
    if not isinstance(meta, dict) or meta.get("run_id") != fresh["run_id"] or meta.get("session") != fresh["run_session"]:
        return export_site(out_dir, path, now, force=force, env_file=env_file, logo_dir=logo_dir)

    work = out_dir.with_name(out_dir.name + ".upd")
    if work.exists():
        shutil.rmtree(work)
    (work / "data").mkdir(parents=True)
    report = ExportReport(out_dir, fresh["run_session"] or "", mode="update")
    write = _api_writer(work, path, report)

    rows = server._clean(server.api_stocks({}, path))           # the same answer with the run's id: computed once
    for q in ({}, {"run_id": fresh["run_id"]}):
        write_json(work / static_path("stocks", q), rows)
        write("screen", q, server.api_screen)
        write("industries", q, server.api_industries)
    write("market", {}, server.api_market)
    write("news", {}, server.api_news)
    held = set(meta.get("detail") or [])
    # Refresh embedded news for existing chart pages without recalculating 1,472 charts.
    for ticker in sorted(held):
        relative = static_path("ticker", {"ticker": ticker})
        original = out_dir / relative
        if original.is_file():
            payload = json.loads(original.read_text(encoding="utf-8"))
            current_news = server._clean(server.ticker_news(ticker, path))
            if payload.get("news") != current_news:
                payload["news"] = current_news
                write_json(work / relative, payload)
    _write_details(write, sorted(_detail_tickers(rows, path) - held), report)

    logo_files = dict(meta.get("logos") or {})
    for t in report.detail:
        src = logos.logo_file(t, logo_dir or logos.LOGO_DIR)
        if src is not None and t not in logo_files:
            (work / "logo").mkdir(exist_ok=True)
            shutil.copy2(src, work / "logo" / src.name)
            logo_files[t] = src.name
    if logo_files != (meta.get("logos") or {}):
        write_json(work / static_path("logos"), sorted(logo_files))
    for name in PAGE_FILES:
        shutil.copy2(STATIC / name, work / name)
    _refuse_secrets(work, env_file)

    staged = sorted(f for f in work.rglob("*") if f.is_file())
    digest = hashlib.sha1(str(meta.get("version")).encode("utf-8"))
    for f in staged:
        digest.update(f.read_bytes())
    report.files = len(staged) + 1
    report.bytes = sum(f.stat().st_size for f in staged)
    for f in staged:
        target = out_dir / f.relative_to(work)
        target.parent.mkdir(parents=True, exist_ok=True)
        os.replace(f, target)
    shutil.rmtree(work)
    meta.update({"generated_at": now.isoformat(timespec="seconds"), "version": digest.hexdigest()[:16],
                 "detail": sorted(held | set(report.detail)), "logos": logo_files,
                 "local_account": cfg.get("local_account", "admin"),
                 "mylist_max": int(cfg.get("mylist_max_per_account", 40))})
    tmp = out_dir / "site.json.tmp"
    write_json(tmp, meta)
    os.replace(tmp, out_dir / "site.json")
    return report
