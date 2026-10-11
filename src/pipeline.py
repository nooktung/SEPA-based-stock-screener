"""`python -m src.main start`: bring everything up to date, then open the dashboard.

Runs, one after another, the daily update and the full-market re-screen from
run.md (sections 1 and 2) plus the two quick steps the dashboard depends on:

    backfill -> index-session -> industry -> fundamentals -> screen -> refprices
             -> monitor --no-fetch -> flow -> news -> logos -> market   -> open the web

The user's choices (2026-10-05): re-screen only when the latest closed session
has not been screened yet; open the web once every step has finished; include
`market` and the logos of new tickers. Each step is skipped when the data it
would produce is already there, so a second `start` the same day opens fast:

* backfill   -- when the latest closed session already has bars for at least
                `coverage_ratio` of the tickers that traded the session before;
* index-session -- when that session already has its market-index figures
                (matched volume, breadth) and the trading calendar was read
                today; it runs before the screen, whose market status counts
                distribution days on matched volume;
* index-check -- when today's report for that session exists (VN-Index bars vs
                HOSE's own values; a report only, user decision 2026-10-09);
* industry, fundamentals, screen -- when a screen run was created after that
                session closed (they only feed the screen);
* refprices  -- when reference prices were read less than
                `refprices_min_interval_hours` ago and the list did not change;
* monitor    -- when the screen was not re-run and the monitored tickers
                already have a state for that session;
* news       -- when articles were collected less than `news_min_interval_hours` ago;
* market     -- when the snapshot already has the latest stored prices;
* flow, logos -- always: they only request what is missing.

Each step is the same CLI command run in its own process, so a failing step is
reported and the next one still runs; nothing is filled in for what failed.
The latest closed session comes from one DNSE request for VN-Index; when DNSE
cannot be reached the stored data decide. Settings: `pipeline` in
config/thresholds.yaml.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

from src.config import load_config
from src.database import PROJECT_ROOT, connect

VN_TZ = timezone(timedelta(hours=7))
LOCK = PROJECT_ROOT / "data" / "pipeline.lock"


@dataclass
class Step:
    key: str
    label: str
    args: list[str]
    estimate: str
    run: bool = True
    why: str = ""                       # why it runs or is skipped
    ok_codes: tuple[int, ...] = (0,)
    result: str = ""
    seconds: float = 0.0


@dataclass
class Plan:
    session: date | None                # latest closed session
    session_source: str                 # "DNSE" or "dữ liệu đã lưu"
    steps: list[Step] = field(default_factory=list)


def vn_now() -> datetime:
    return datetime.now(VN_TZ)


def settings() -> Mapping[str, Any]:
    return load_config().thresholds["pipeline"]


def _closed_after(cfg: Mapping[str, Any]) -> dtime:
    h, m = (int(x) for x in str(cfg["session_closed_after"]).split(":"))
    return dtime(h, m)


def latest_closed_session(now: datetime, index_dates: Sequence[date], cfg: Mapping[str, Any]) -> date | None:
    """The last session whose bar is final: today's counts only after `session_closed_after`."""
    closed = [d for d in index_dates if d < now.date() or (d == now.date() and now.timetz().replace(tzinfo=None) >= _closed_after(cfg))]
    return max(closed) if closed else None


def dnse_index_dates(now: datetime) -> list[date] | None:
    """Session dates DNSE reports for VN-Index over the last two weeks (one request), or None."""
    try:
        from src.collectors.backfill import live_market_provider  # noqa: PLC0415
        from src.env import load_dotenv  # noqa: PLC0415

        load_dotenv()
        bars = live_market_provider().get_index_ohlcv("VNINDEX", now.date() - timedelta(days=14), now.date())
        return [b.trade_date for b in bars]
    except Exception:  # noqa: BLE001 -- no network / no key: the stored data decide
        return None


def _session_close_utc(session: date, cfg: Mapping[str, Any]) -> datetime:
    return datetime.combine(session, _closed_after(cfg), tzinfo=VN_TZ).astimezone(timezone.utc)


def _parse_ts(text: str | None) -> datetime | None:
    if not text:
        return None
    try:
        ts = datetime.fromisoformat(str(text).replace("Z", "+00:00"))
    except ValueError:
        return None
    return ts if ts.tzinfo else ts.replace(tzinfo=timezone.utc)


def plan_steps(now: datetime, path: Path | None = None, index_dates: Sequence[date] | None = None,
               cfg: Mapping[str, Any] | None = None) -> Plan:
    cfg = cfg or settings()
    with connect(path) as conn:
        stored = [date.fromisoformat(r[0]) for r in conn.execute(
            "SELECT DISTINCT trade_date FROM index_daily WHERE symbol = 'VNINDEX' ORDER BY trade_date DESC LIMIT 15")]
        source = "DNSE" if index_dates else "dữ liệu đã lưu"
        session = latest_closed_session(now, index_dates or stored, cfg)
        counts = dict(conn.execute("SELECT trade_date, COUNT(*) FROM prices_daily WHERE trade_date >= ? GROUP BY trade_date",
                                   ((now.date() - timedelta(days=20)).isoformat(),)).fetchall())
        screen = conn.execute("SELECT created_at, as_of_date FROM screen_runs ORDER BY created_at DESC LIMIT 1").fetchone()
        news_last = conn.execute("SELECT MAX(collected_at) FROM news_articles").fetchone()[0]
        ref_last = conn.execute("SELECT MAX(observed_at) FROM reference_prices").fetchone()[0]
        snap = conn.execute("SELECT MAX(as_of_date) FROM market_snapshot").fetchone()[0]
        snap_built = conn.execute("SELECT MAX(created_at) FROM market_snapshot").fetchone()[0]
        price_max = conn.execute("SELECT MAX(trade_date) FROM prices_daily").fetchone()[0]
        state_n = conn.execute("SELECT COUNT(*) FROM monitor_state WHERE trade_date = ?",
                               (session.isoformat() if session else "",)).fetchone()[0]
        idx_done = conn.execute("SELECT session_observed_at FROM index_daily WHERE symbol = 'VNINDEX' AND trade_date = ?",
                                (session.isoformat() if session else "",)).fetchone()
    plan = Plan(session=session, session_source=source)
    day = session.isoformat() if session else None

    # prices for the whole market
    prev = max((d for d in counts if day and d < day), default=None)
    have, before = counts.get(day, 0) if day else 0, counts.get(prev, 0) if prev else 0
    prices_ok = bool(day and before and have >= float(cfg["coverage_ratio"]) * before)
    plan.steps.append(Step("backfill", "Tải giá toàn thị trường", ["backfill"], "~30 phút", run=not prices_ok,
                           why=(f"phiên {_dm(session)} đã có giá {have} mã" if prices_ok else
                                f"phiên {_dm(session)} mới có giá {have}/{before} mã" if session else "chưa xác định được phiên"),
                           ok_codes=(0, 3)))

    # matched volume / breadth of the index (before the screen: distribution days use it)
    from src.collectors.trading_calendar import load as load_calendar

    calendar_today = load_calendar().get("fetched_on") == now.date().isoformat()
    idx_ok = bool(idx_done and idx_done[0]) and calendar_today
    plan.steps.append(Step("index-session", "KL khớp lệnh & độ rộng VN-Index", ["index-session"], "vài giây",
                           run=not idx_ok,
                           why=(f"phiên {_dm(session)} đã có" if idx_ok else "chỉ lấy phiên còn thiếu")))
    from src.collectors.index_check import report_path

    checked = bool(session) and report_path(session.isoformat()).exists()
    plan.steps.append(Step("index-check", "Đối chiếu VN-Index với HOSE (báo cáo)", ["index-check"], "vài giây",
                           run=not checked,
                           why=(f"đã có báo cáo phiên {_dm(session)}" if checked else "chưa có báo cáo phiên này")))

    # re-screen when the latest closed session has not been screened
    screened = None
    if screen:
        created = _parse_ts(screen["created_at"])
        screened = bool(session and created and created >= _session_close_utc(session, cfg))
    need_screen = not screened
    why_screen = (f"đã lọc sau phiên {_dm(session)} (lượt lọc {screen['as_of_date']})" if screened
                  else f"chưa lọc phiên {_dm(session)}" if session else "chưa có lượt lọc")
    plan.steps += [
        Step("industry", "Phân ngành mã mới", ["industry"], "vài phút", run=need_screen, why=why_screen),
        Step("fundamentals", "BCTC quý mã mới", ["fundamentals"], "vài phút", run=need_screen, why=why_screen),
        Step("screen", "Lọc lại toàn thị trường", ["screen"], "~2 phút", run=need_screen, why=why_screen),
    ]
    # Before monitor: its "at the ceiling" check uses the stored ceiling.
    ref_at = _parse_ts(ref_last)
    fresh_ref = bool(ref_at and now.astimezone(timezone.utc) - ref_at < timedelta(hours=float(cfg["refprices_min_interval_hours"])))
    plan.steps.append(Step("refprices", "Giá tham chiếu / trần / sàn", ["refprices", "--market"], "~2–6 phút",
                           run=need_screen or not fresh_ref,
                           why=("danh sách mới" if need_screen else f"đã đọc lúc {ref_at.astimezone(VN_TZ):%H:%M %d/%m}"
                                if fresh_ref else "chưa đọc gần đây")))
    monitored = _monitored_count(path)
    state_ok = bool(monitored and state_n >= float(cfg["coverage_ratio"]) * monitored)
    plan.steps.append(Step("monitor", "Theo dõi danh sách (sự kiện, trạng thái)", ["monitor", "--no-fetch"], "~2 phút",
                           run=need_screen or not state_ok,
                           why="danh sách mới" if need_screen else (f"đã có trạng thái phiên {_dm(session)}" if state_ok
                                                                     else f"chưa có trạng thái phiên {_dm(session)}")))
    plan.steps.append(Step("flow", "Khối ngoại", ["flow", "--days", str(int(cfg["flow_days"]))], "~2 phút",
                           why="chỉ lấy phiên còn thiếu"))
    last = _parse_ts(news_last)
    fresh_news = bool(last and now.astimezone(timezone.utc) - last < timedelta(hours=float(cfg["news_min_interval_hours"])))
    plan.steps.append(Step("news", "Tin tức", ["news"], "~8 phút", run=not fresh_news,
                           why=(f"đã thu lúc {last.astimezone(VN_TZ):%H:%M %d/%m}" if fresh_news else "chưa thu gần đây")))
    plan.steps.append(Step("logos", "Logo mã mới", ["logos"], "vài giây", why="chỉ tải mã chưa có"))
    # Its RS comes from the latest screen run: rebuild after a new run too (it once showed the
    # previous run's RS because it was built at 16:56 and the screen ran at 17:16).
    built = _parse_ts(snap_built)
    rs_fresh = not screen or bool(built and _parse_ts(screen["created_at"]) and built >= _parse_ts(screen["created_at"]))
    snap_ok = bool(snap and price_max and snap >= price_max) and prices_ok and rs_fresh and not need_screen
    plan.steps.append(Step("market", "Bảng Toàn thị trường", ["market"], "~15 giây", run=not snap_ok,
                           why=(f"đã có giá {_dm(date.fromisoformat(snap))}" if snap_ok
                                else "dựng lại theo lượt lọc mới" if snap and not (rs_fresh and not need_screen)
                                else "dựng lại theo giá mới")))
    # Report only (src/web/checks.py): a broken invariant is listed, the update is not stopped.
    plan.steps.append(Step("check", "Kiểm tra bất biến (pivot, stop, bước giá…)", ["check"], "~30 giây",
                           why="báo cáo, không dừng các bước khác"))
    return plan


def _dm(d: date | None) -> str:
    return d.strftime("%d/%m") if d else "–"


def _monitored_count(path: Path | None) -> int:
    try:
        from src.quant.monitor import monitored_tickers  # noqa: PLC0415

        return len(monitored_tickers(path)[0])
    except Exception:  # noqa: BLE001
        return 0


# ---------------------------------------------------------------- lock
def acquire_lock(now: datetime, cfg: Mapping[str, Any], lock: Path = LOCK) -> str | None:
    """None when this run may go ahead; otherwise who holds the lock."""
    if lock.exists():
        try:
            held = json.loads(lock.read_text(encoding="utf-8"))
            started = _parse_ts(held.get("started"))
        except (OSError, ValueError):
            held, started = {}, None
        if started and now.astimezone(timezone.utc) - started < timedelta(hours=float(cfg["lock_stale_hours"])):
            return f"PID {held.get('pid')}, bắt đầu {started.astimezone(VN_TZ):%H:%M %d/%m}"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": os.getpid(), "started": now.astimezone(timezone.utc).isoformat()}), encoding="utf-8")
    return None


def release_lock(lock: Path = LOCK) -> None:
    try:
        lock.unlink()
    except OSError:
        pass


# ---------------------------------------------------------------- run
def run_steps(plan: Plan, runner: Callable[..., Any] = subprocess.run, out: Callable[[str], None] = print,
              clock: Callable[[], float] = time.monotonic) -> Plan:
    todo = [s for s in plan.steps if s.run]
    for s in plan.steps:
        if not s.run:
            s.result = "bỏ qua"
    for k, s in enumerate(todo, 1):
        out(f"\n[{k}/{len(todo)}] {s.label} ({s.estimate}) — {s.why}")
        started = clock()
        try:
            code = runner([sys.executable, "-m", "src.main", *s.args], cwd=str(PROJECT_ROOT)).returncode
        except OSError as exc:
            code, s.result = -1, f"lỗi: {exc}"
        s.seconds = clock() - started
        if not s.result:
            s.result = ("xong" if code == 0 else "xong (một số mã bị gắn cờ dữ liệu)" if code in s.ok_codes
                        else f"LỖI (mã thoát {code}) — các bước sau vẫn chạy với dữ liệu đã có")
        out(f"      → {s.result} · {_dur(s.seconds)}")
    return plan


def _dur(seconds: float) -> str:
    m, s = divmod(int(round(seconds)), 60)
    return f"{m} phút {s} giây" if m else f"{s} giây"


def summary(plan: Plan) -> str:
    lines = [f"Phiên mới nhất đã đóng cửa: {_dm(plan.session)} (theo {plan.session_source})"]
    for s in plan.steps:
        lines.append(f"  {'•' if s.run else '–'} {s.label:<42} {s.result or ('sẽ chạy' if s.run else 'bỏ qua')}"
                     + (f" — {s.why}" if not s.run or not s.result else ""))
    return "\n".join(lines)
