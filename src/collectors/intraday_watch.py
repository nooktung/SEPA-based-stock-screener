"""`python -m src.main watch`: intraday alerts for the focus list during the session (Phase 11).

Reads matched trades of the monitored tickers from DNSE's market websocket (read-only
channels, src/collectors/providers/dnse_stream.py) and checks each against the levels of
the last close (src/quant/intraday.py). An alert is stored once per ticker, type and day in
`intraday_alerts`; a status file (data/intraday/status_<day>.json) tells the dashboard what
is being watched, which tickers are left out and why, the last price of each, and when the
feed last spoke -- a silent feed reads as stale, never as "no alerts".

Nothing is fetched to fill a gap: a disconnection leaves trades unseen, and an alert that
would have fired in the gap does not appear. The daily monitor after the close stays the
record of what the session did.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Mapping

from src.config import PROJECT_ROOT, load_config
from src.database import connect
from src.quant.intraday import ALERT_TEXT, Alert, IntradayWatcher, Levels

log = logging.getLogger(__name__)

VN_TZ = timezone(timedelta(hours=7))
STATUS_DIR = PROJECT_ROOT / "data" / "intraday"
SOURCE_ID = "dnse_openapi"


def intraday_settings(thresholds: Mapping[str, Any] | None = None) -> dict[str, Any]:
    th = thresholds or load_config().thresholds.raw
    return {**th["intraday"], "pace_breakout": float(th["monitor"]["breakout_volume_ratio"])}


def build_levels(day: date, path: Path | None = None, thresholds: Mapping[str, Any] | None = None) -> dict[str, Levels]:
    """Each monitored ticker's levels for `day`, from what was stored after the previous close."""
    from src.quant.monitor import monitored_tickers  # noqa: PLC0415
    from src.quant.screener import FORMING_BASES  # noqa: PLC0415

    cfg = intraday_settings(thresholds)
    n_ma, n_vol = int(cfg["ma_sessions"]), int(cfg["avg_volume_sessions"])
    tol = float(cfg["reference_tolerance_pct"])
    tickers, _, _ = monitored_tickers(path)
    out: dict[str, Levels] = {}
    with connect(path) as conn:
        exchanges = dict(conn.execute("SELECT ticker, exchange FROM tickers"))
        refs = dict(conn.execute("SELECT ticker, reference FROM reference_prices WHERE session_date = ?",
                                 (day.isoformat(),)))
        for t in tickers:
            ex = exchanges.get(t)
            bars = conn.execute("SELECT trade_date, close, volume FROM prices_daily WHERE ticker = ? AND trade_date < ? "
                                "ORDER BY trade_date DESC LIMIT ?", (t, day.isoformat(), max(n_ma, n_vol))).fetchall()
            if not ex or not bars:
                continue
            row = conn.execute("SELECT state_json FROM monitor_state WHERE ticker = ? ORDER BY trade_date DESC LIMIT 1",
                               (t,)).fetchone()
            st = json.loads(row[0]) if row and row[0] else {}
            prev_close = float(bars[0][1])
            closes = [float(b[1]) for b in bars]
            vols = [float(b[2] or 0) for b in bars]
            ex_date = st.get("ex_date")
            if ex_date:                                     # DNSE rescales prices, not volumes, on an ex-date
                vols = [v for (d, _, _), v in zip(bars, vols) if d >= ex_date]
            setup = st.get("setup") or {}
            lv = Levels(
                ticker=t, exchange=ex, prev_close=prev_close,
                pivot=st.get("pivot"), stop=st.get("stop"),
                ma50=sum(closes[:n_ma]) / n_ma if len(closes) >= n_ma else None,
                avg_volume=sum(vols[:n_vol]) / len(vols[:n_vol]) if len(vols) >= 10 else None,
                base_kind=st.get("base_kind"), status=setup.get("status"),
                forming=st.get("base_kind") in FORMING_BASES,
                stop_active=bool(st.get("stop")) and not st.get("stop_breached") and not st.get("stop_pending"),
            )
            if st and st.get("trade_date") != bars[0][0]:
                lv.skip_reason = (f"trạng thái lưu theo phiên {st.get('trade_date')}, giá đã có phiên {bars[0][0]}: "
                                  "chạy monitor trước")
            ref = refs.get(t)
            if ex.upper() in ("HOSE", "HNX") and ref and abs(float(ref) / prev_close - 1) * 100.0 > tol:
                lv.skip_reason = (f"giá tham chiếu hôm nay {float(ref):,.0f} khác giá đóng cửa đã lưu {prev_close:,.0f}: "
                                  "có thể có sự kiện quyền, các mức giá chưa cùng thang").replace(",", ".")
            if not st:
                lv.skip_reason = "chưa có trạng thái theo dõi (chạy monitor)"
            out[t] = lv
    return out


def fired_today(day: date, path: Path | None = None) -> set[tuple[str, str]]:
    with connect(path) as conn:
        return {(t, k) for t, k in conn.execute("SELECT ticker, alert_type FROM intraday_alerts WHERE session_date = ?",
                                               (day.isoformat(),))}


def store_alert(alert: Alert, day: date, path: Path | None = None, source_level: int = 2) -> bool:
    """Append one alert; False when that ticker / type already has one today."""
    with connect(path) as conn:
        cur = conn.execute(
            "INSERT OR IGNORE INTO intraday_alerts (alert_id, session_date, ticker, alert_type, price, level, traded_at, "
            "volume, volume_pace, what_changed, source_id, source_level, observed_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (f"{day.isoformat()}:{alert.ticker}:{alert.alert_type}", day.isoformat(), alert.ticker, alert.alert_type,
             alert.price, alert.level, alert.at, alert.volume, alert.volume_pace, alert.text, SOURCE_ID, source_level,
             datetime.now(timezone.utc).isoformat(timespec="seconds")))
        return cur.rowcount > 0


def trade_from_payload(payload: Mapping[str, Any], scale: float,
                       volume_scale: float) -> tuple[str, float, float | None, datetime] | None:
    """(ticker, price in VND, session volume so far in shares, time) from a DNSE trade frame, or None.
    Both scales come from sources.yaml: prices in thousands, volumes in units of 10 shares."""
    from src.collectors.providers.dnse_provider import normalize_price  # noqa: PLC0415

    symbol, raw = payload.get("symbol"), payload.get("matchPrice")
    if not symbol or raw is None:
        return None
    stamp = payload.get("time")
    seconds = stamp.get("Seconds") if isinstance(stamp, Mapping) else stamp
    at = (datetime.fromtimestamp(float(seconds), VN_TZ) if seconds else datetime.now(VN_TZ))
    volume = payload.get("totalVolumeTraded")
    return (str(symbol).upper(), float(normalize_price(raw, scale)),
            (float(volume) * volume_scale if volume is not None else None), at)


@dataclass
class WatchStatus:
    session_date: str
    started_at: str
    watched: list[str] = field(default_factory=list)
    skipped: dict[str, str] = field(default_factory=dict)
    trades: int = 0
    alerts: int = 0
    last_message_at: str | None = None
    ended_at: str | None = None
    error: str | None = None
    last: dict[str, Any] = field(default_factory=dict)
    written_at: str | None = None

    def write(self, path: Path | None = None) -> Path:
        self.written_at = datetime.now(VN_TZ).isoformat(timespec="seconds")
        STATUS_DIR.mkdir(parents=True, exist_ok=True)
        out = path or STATUS_DIR / f"status_{self.session_date}.json"
        tmp = out.with_suffix(".tmp")
        tmp.write_text(json.dumps(self.__dict__, ensure_ascii=False, default=str), encoding="utf-8")
        tmp.replace(out)
        return out


def read_status(day: date) -> dict[str, Any] | None:
    f = STATUS_DIR / f"status_{day.isoformat()}.json"
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


async def run_watch(until: time | None = None, path: Path | None = None, dry_run: bool = False,
                    stream: Any | None = None, now: Callable[[], datetime] | None = None,
                    echo: Callable[[str], None] = print) -> WatchStatus:
    """Watch until `until` (VN time, default `intraday.stop`). `stream` is injectable for tests."""
    clock = now or (lambda: datetime.now(VN_TZ))
    th = load_config().thresholds.raw
    cfg = intraday_settings(th)
    day = clock().date()
    levels = build_levels(day, path, th)
    status = WatchStatus(day.isoformat(), clock().isoformat(timespec="seconds"),
                         watched=sorted(t for t, lv in levels.items() if not lv.skip_reason),
                         skipped={t: lv.skip_reason for t, lv in levels.items() if lv.skip_reason})
    watcher = IntradayWatcher(levels, cfg, fired=fired_today(day, path))
    if not status.watched:
        status.error = "không có mã nào để theo dõi"
        status.ended_at = clock().isoformat(timespec="seconds")
        status.write()
        return status

    src = next((s for s in load_config().sources.specs if s.id == SOURCE_ID), None)
    if src is None or "price_scale" not in src.config or "ws_volume_scale" not in src.config:
        raise ValueError(f"sources.yaml: {SOURCE_ID}.price_scale and ws_volume_scale are required "
                         "(never inferred from the numbers)")
    scale, volume_scale = float(src.config["price_scale"]), float(src.config["ws_volume_scale"])
    source_level = int(src.source_level)
    if stream is None:
        from src.collectors.providers.dnse_client import DnseCredentials  # noqa: PLC0415
        from src.collectors.providers.dnse_stream import DnseMarketStream  # noqa: PLC0415

        stream = DnseMarketStream(DnseCredentials.from_env())
    from src.collectors.providers.dnse_stream import trade_channel  # noqa: PLC0415

    stream.add_subscription(trade_channel(str(cfg["board_id"])), status.watched)
    last_write = [0.0]

    def handle(ev: Any) -> None:
        if ev.event != "trade":
            return
        trade = trade_from_payload(ev.payload, scale, volume_scale)
        if trade is None:
            return
        ticker, price, volume, at = trade
        status.trades += 1
        status.last_message_at = clock().isoformat(timespec="seconds")
        for alert in watcher.on_trade(ticker, price, volume, at):
            if dry_run or store_alert(alert, day, path, source_level):
                status.alerts += 1
                echo(f"{at:%H:%M:%S} {ticker:<4} {ALERT_TEXT[alert.alert_type]}: {alert.text}")
        stamp = clock().timestamp()
        if stamp - last_write[0] >= float(cfg["status_every_seconds"]):
            status.last = dict(watcher.last)
            status.write()
            last_write[0] = stamp

    end = until or time.fromisoformat(str(cfg["stop"]))
    seconds = (datetime.combine(day, end, VN_TZ) - clock()).total_seconds()
    status.write()
    try:
        if seconds > 0:
            await asyncio.wait_for(stream.run(handle), timeout=seconds)
    except asyncio.TimeoutError:
        pass
    except Exception as exc:  # noqa: BLE001 - recorded for the dashboard, then re-raised to the CLI
        status.error = str(exc)
        raise
    finally:
        try:
            await stream.close()
        finally:
            status.last = dict(watcher.last)
            status.ended_at = clock().isoformat(timespec="seconds")
            status.write()
    return status
