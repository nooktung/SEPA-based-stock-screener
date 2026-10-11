"""Daily monitoring of the focus list.

For each monitored ticker the state is computed twice from stored bars -- as of
today's session and as of the session before -- and the events are the
difference. Nothing depends on what a previous run happened to store, so a
re-run gives the same answer, and a breakout is always judged against the pivot
that was knowable the day before, never one drawn through today's bar.

Events describe what the bars did. They carry no suggested action: whether a
breakout is worth acting on is the user's decision, made with the rest of the
evidence (market status, news, fundamentals) in view.
"""

from __future__ import annotations

import hashlib
import json
import math
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Sequence

import pandas as pd

from src.collectors.refprices import _adjustments
from src.collectors.refprices import lookup as reference_lookup
from src.config import load_config
from src.database import connect, init_db
from src.market_calendar import price_limit_pct
from src.quant import indicators as ind
from src.quant.screener import (BASE_RULES, BaseRead, market_status, read_base, screen_settings, stop_breached_on,
                                tick_size, tracked_base, trend_template)
from src.quant.setup_status import assess, market_note
from src.quant.vnfmt import dmy as _dmy
from src.quant.vnfmt import num as _n

# Event types, stored in alerts.alert_type
BREAKOUT = "BREAKOUT"
BREAKOUT_LOW_VOLUME = "BREAKOUT_LOW_VOLUME"   # kept so older stored alerts still read
BREAKOUT_WEAK = "BREAKOUT_WEAK"
NEW_HIGH_NO_BASE = "NEW_HIGH_NO_BASE"
BREAKOUT_FAILED = "BREAKOUT_FAILED"
STOP_BREACHED = "STOP_BREACHED"
TREND_TEMPLATE_BROKEN = "TREND_TEMPLATE_BROKEN"
TREND_TEMPLATE_RESTORED = "TREND_TEMPLATE_RESTORED"
MA50_HEAVY_VOLUME = "MA50_BREAK_HEAVY_VOLUME"
LARGEST_DOWN_DAY = "LARGEST_DOWN_DAY"
SETUP_READY = "SETUP_READY"
CLIMAX_WARNING = "CLIMAX_WARNING"

# Criteria recomputable from one ticker's bars. TT8 (RS rating) is a rank across
# the whole universe and only changes when the universe is screened again.
PRICE_CRITERIA = ("TT1", "TT2", "TT3", "TT4", "TT5", "TT6", "TT7")


@dataclass
class TickerState:
    ticker: str
    trade_date: date
    close: float
    change_pct: float | None
    volume_ratio: float | None
    price_criteria_passed: list[str]
    criteria: dict[str, Any]
    base_kind: str
    pivot: float | None
    stop: float | None
    distance_to_pivot_pct: float | None
    volume_dryup: bool | None
    contractions: list[float]
    rs_vs_index_63d: float | None       # percentage points over the benchmark
    above_ma200_pct: float | None
    at_ceiling: bool
    setup: dict[str, Any] = field(default_factory=dict)    # three questions + status (setup_status)
    tech: dict[str, Any] = field(default_factory=dict)     # TradingView-style ratings + candle (display only)
    stop_breached: bool = False         # the close is at or below the (held) stop
    held_since: str | None = None       # pivot / stop held from this session (screener.tracked_base)
    held_by: str | None = None          # "BREAKOUT" or "STOP"
    ex_date: str | None = None          # a recorded ex-rights date within the volume window
    volume_note: str | None = None      # why the volume ratio could not be measured
    stop_breached_on: str | None = None  # the session the close went to or below the stop
    base_rules: int | None = None       # screener.BASE_RULES the base was read with
    legacy_hold: bool = False           # pivot / stop carried from the earlier rules (legacy_hold)

    def as_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["trade_date"] = self.trade_date.isoformat()
        return d


@dataclass
class Event:
    ticker: str
    trade_date: date
    event_type: str
    what_changed: str
    why_it_matters: str

    @property
    def alert_id(self) -> str:
        key = f"{self.ticker}|{self.trade_date.isoformat()}|{self.event_type}"
        return hashlib.sha1(key.encode()).hexdigest()[:20]


@dataclass
class MonitorResult:
    as_of: date | None
    tickers: list[str]
    states: dict[str, TickerState] = field(default_factory=dict)
    events: list[Event] = field(default_factory=list)
    market: dict[str, Any] = field(default_factory=dict)
    market_before: dict[str, Any] = field(default_factory=dict)
    missing: dict[str, str] = field(default_factory=dict)
    screen_rs: dict[str, float] = field(default_factory=dict)
    screen_group: dict[str, str] = field(default_factory=dict)


def monitor_settings(thresholds: Mapping[str, Any] | None = None) -> dict[str, Any]:
    raw = thresholds if thresholds is not None else load_config().thresholds.raw
    return dict(raw["monitor"])


# ---------------------------------------------------------------------------
# State
# ---------------------------------------------------------------------------

def ex_dates(frame: pd.DataFrame, actions: Sequence[tuple[str, float]]) -> list[pd.Timestamp]:
    """First session after each recorded rescale's last old-scale session (the ex-rights date)."""
    out = []
    for through, _factor in actions:
        later = frame.index[frame.index > pd.Timestamp(through)]
        if len(later):
            out.append(later[0])
    return sorted(out)


# Base labels as the dashboard shows them (app.js BASE_TEXT), for event texts (tester B15).
BASE_TEXT = {"VCP": "VCP", "FLAT_BASE": "nền phẳng", "CUP_HANDLE": "cốc tay cầm", "CUP": "cốc chưa có tay cầm",
             "BASE": "nền", "NEAR_HIGH": "sát đỉnh", "SHARP_PULLBACK": "vừa giảm mạnh", "EXTENDED": "đã chạy xa",
             "TOO_DEEP": "điều chỉnh quá sâu", "BROKEN": "thủng nền", "NONE": "–"}


def compute_state(
    ticker: str,
    exchange: str,
    frame: pd.DataFrame,
    index_close: pd.Series | None,
    rs_rating: float | None,
    screen: Mapping[str, Any],
    mon: Mapping[str, Any],
    with_tech: bool = True,
    ceiling: float | None = None,
    actions: Sequence[tuple[str, float]] = (),
) -> TickerState:
    """`ceiling`: the exchange's ceiling price for the last session when stored
    (src/collectors/refprices.py); otherwise it is derived from the previous close.
    `actions`: the ticker's recorded rescales, (last old-scale session, factor)."""
    close = frame["close"].astype("float64")
    vol = frame["volume"].astype("float64")
    last = float(close.iloc[-1])
    prev = float(close.iloc[-2]) if len(close) > 1 else None
    change = (last / prev - 1) * 100.0 if prev else None
    n = int(mon["volume_avg_sessions"])
    window = vol.iloc[-n - 1:-1]
    volume_note = ex_date = None
    # DNSE does not rescale past volumes on an ex-rights date: average only from the ex-date on.
    recent = [d for d in ex_dates(frame, actions) if len(window) and d >= window.index[0]]
    if recent:
        ex_date = recent[-1].date().isoformat()
        window = window[window.index >= recent[-1]]
        if len(window) < int(mon.get("min_sessions_after_action", 10)):
            window = window.iloc[0:0]
            volume_note = (f"KL không so được: ngày GDKHQ {_dmy(ex_date)}, DNSE điều chỉnh giá quá khứ "
                           "nhưng không điều chỉnh khối lượng")
    avg = float(window.mean()) if len(window) and (len(vol) > n or recent) else float("nan")
    volume_ratio = float(vol.iloc[-1]) / avg if avg == avg and avg > 0 else None

    criteria = trend_template(frame, rs_rating, screen)
    min_hist = int(screen["min_history_sessions"])
    if len(frame) < min_hist:
        # A recent listing: only the distance from its post-listing high (TT7)
        # can be measured; the rest are "not yet assessable", not failures.
        for code, c in criteria.items():
            if code != "TT7":
                c.assessable = False
                c.passed = False
                c.threshold = f"cần ≥ {min_hist} phiên (mới {len(frame)})"
    base = tracked_base(frame, exchange, screen)

    rs_vs_index = None
    if index_close is not None and len(close) > 63:
        idx = index_close.reindex(close.index).ffill()
        if idx.notna().iloc[-64:].all():
            rs_vs_index = ((last / float(close.iloc[-64])) - (float(idx.iloc[-1]) / float(idx.iloc[-64]))) * 100.0

    ma200 = ind.sma(close, 200).iloc[-1]
    band = price_limit_pct(exchange)
    at_ceiling = False
    if ceiling:
        at_ceiling = last >= ceiling - 0.5          # the published ceiling: exact, in whole dong
    elif prev and band:
        # No published ceiling: the band from the previous close (UPCoM's real reference is the
        # previous session's average price, so this is close but not exact there).
        derived = prev * (1 + band / 100.0)
        # Within one tick of the ceiling: the close was pinned by the band.
        at_ceiling = last >= derived - tick_size(derived, exchange)

    return TickerState(
        ticker=ticker,
        trade_date=frame.index[-1].date(),
        close=last,
        change_pct=change,
        volume_ratio=volume_ratio,
        price_criteria_passed=[k for k in PRICE_CRITERIA if criteria[k].passed],
        criteria={k: c.as_dict() for k, c in criteria.items()},
        base_kind=base.kind,
        pivot=base.pivot,
        stop=base.stop,
        distance_to_pivot_pct=base.distance_to_pivot_pct,
        volume_dryup=base.volume_dryup,
        contractions=[round(x, 2) for x in base.contractions],
        rs_vs_index_63d=rs_vs_index,
        above_ma200_pct=(last / float(ma200) - 1) * 100.0 if ma200 == ma200 and ma200 else None,
        at_ceiling=at_ceiling,
        tech=technicals(frame, band) if with_tech else {},
        stop_breached=bool(base.stop) and last <= base.stop,
        held_since=base.held_since,
        held_by=base.held_by,
        ex_date=ex_date,
        volume_note=volume_note,
        stop_breached_on=stop_breached_on(frame, base),
        base_rules=BASE_RULES,
    )


def legacy_hold(stored: Mapping[str, Any] | None, frame: pd.DataFrame, hold: int,
                actions: Sequence[tuple[str, float]] = ()) -> dict[str, Any] | None:
    """The pivot / stop the earlier base rules were holding, while that hold lasts.

    A change of rules must not flip a status by itself: GMD held the 52,800 pivot of its
    07/10 breakout, which the rules of 2026-10-11 read as 55,600. A state stored by the
    earlier rules (no `base_rules`), or carried from one, keeps its held pivot, stop and
    label until the hold ends (`base.hold_sessions` from `held_since`, as tracked_base),
    a breakout hold's stop is broken, or a rescale puts the old numbers on another scale.
    """
    if not stored or not stored.get("held_since") or not stored.get("pivot"):
        return None
    if stored.get("base_rules") and not stored.get("legacy_hold"):
        return None
    since = str(stored["held_since"])
    if pd.Timestamp(since) > frame.index[-1] or int((frame.index >= pd.Timestamp(since)).sum()) > hold:
        return None
    if any(str(through) >= since for through, _ in actions):
        return None
    stop = stored.get("stop")
    if stored.get("held_by") == "BREAKOUT" and stop and float(frame["close"].iloc[-1]) < float(stop):
        return None
    return {"base_kind": stored.get("base_kind"), "pivot": float(stored["pivot"]),
            "stop": float(stop) if stop else None, "held_since": since, "held_by": stored.get("held_by"),
            "contractions": list(stored.get("contractions") or [])}


def apply_legacy_hold(state: TickerState, frame: pd.DataFrame, held: Mapping[str, Any]) -> None:
    close = float(frame["close"].iloc[-1])
    state.base_kind, state.pivot, state.stop = held["base_kind"], held["pivot"], held["stop"]
    state.held_since, state.held_by = held["held_since"], held["held_by"]
    state.contractions = held["contractions"]
    state.distance_to_pivot_pct = (close / state.pivot - 1) * 100.0
    state.stop_breached = bool(state.stop) and close <= state.stop
    state.stop_breached_on = stop_breached_on(frame, BaseRead(stop=state.stop, held_since=state.held_since,
                                                              held_by=state.held_by))
    state.legacy_hold = True


def technicals(frame: pd.DataFrame, band_pct: float | None) -> dict[str, Any]:
    """TradingView-style ratings and the latest candle pattern -- shown, never scored."""
    from src.quant.candles import latest_pattern  # noqa: PLC0415
    from src.quant.tv_ratings import ratings  # noqa: PLC0415

    thresholds = load_config().thresholds
    try:
        out = ratings(frame, thresholds)
    except Exception:  # noqa: BLE001 - a display extra must never stop the monitor
        return {}
    out["pattern"] = latest_pattern(frame, thresholds["candles"], band_pct)
    return out


# ---------------------------------------------------------------------------
# Events
# ---------------------------------------------------------------------------

def detect_events(
    ticker: str,
    exchange: str,
    frame: pd.DataFrame,
    today: TickerState,
    before: TickerState,
    screen: Mapping[str, Any],
    mon: Mapping[str, Any],
    market: Mapping[str, Any] | None = None,
    actions: Sequence[tuple[str, float]] = (),
) -> list[Event]:
    out: list[Event] = []
    d = today.trade_date
    # sessions since the latest recorded ex-rights date (0 = the ex-date itself)
    exd = [x for x in ex_dates(frame, actions) if x <= frame.index[-1]]
    since_ex = int((frame.index > exd[-1]).sum()) if exd else None
    quiet = since_ex is not None and since_ex <= int(mon.get("corporate_action_quiet_sessions", 2))

    def add(kind: str, what: str, why: str) -> None:
        out.append(Event(ticker, d, kind, what, why))

    vol_note = ""
    if today.at_ceiling:
        vol_note = (" Phiên đóng cửa ở giá trần: khối lượng khớp có thể thấp giả tạo vì thiếu người bán, "
                    "nên KL thấp không phải bằng chứng phủ định [skill 05 §6.2].")

    # Breakout through yesterday's pivot
    if before.pivot and before.base_kind in {"VCP", "FLAT_BASE", "CUP_HANDLE", "CUP", "BASE", "NEAR_HIGH"}:
        prev_close = float(frame["close"].iloc[-2])
        if today.close > before.pivot >= prev_close:
            ratio = today.volume_ratio
            vol_text = (f"KL = {_n(ratio, 2)}× TB{mon['volume_avg_sessions']}." if ratio
                        else f"{today.volume_note or 'KL không xác định'}.")
            if before.base_kind == "NEAR_HIGH":
                # A high set only weeks ago is not a base: no contraction, no
                # dry-up, so this is a new high rather than a base breakout.
                add(NEW_HIGH_NO_BASE,
                    f"Đóng cửa {_n(today.close)} vượt đỉnh gần {_n(before.pivot)}; {vol_text}",
                    "Đỉnh cũ mới hình thành vài tuần, chưa có nền đạt chuẩn -- không phải điểm mua "
                    "breakout khỏi nền theo SEPA [SÁCH]." + vol_note)
            else:
                bar = frame.iloc[-1]
                rng = float(bar["high"]) - float(bar["low"])
                close_pos = (today.close - float(bar["low"])) / rng if rng > 0 else 1.0
                zone = float(screen["base"]["buy_zone_max_pct"])
                strong_vol = ratio is not None and ratio >= float(mon["breakout_volume_ratio"])
                checks = {
                    # A limit-up close starves the tape of sellers; low volume there is not evidence.
                    "volume": strong_vol or today.at_ceiling,
                    "close_high": close_pos >= float(mon["breakout_min_close_position"]),
                    "not_extended": today.close <= before.pivot * (1 + zone / 100.0),
                    # On and just after an ex-rights date prices and volumes are distorted.
                    "corporate_action": not quiet,
                }
                missing = {
                    "volume": (f"KL chỉ {_n(ratio, 2)}× trung bình (cần ≥ {_n(float(mon['breakout_volume_ratio']), 1)}×)" if ratio
                               else (today.volume_note or "thiếu KL")),
                    "close_high": f"đóng cửa ở {close_pos:.0%} biên độ phiên (cần ≥ {mon['breakout_min_close_position']:.0%})",
                    "not_extended": f"đã vượt pivot hơn {zone:g}% ngay phiên breakout",
                    "corporate_action": (f"sát ngày GDKHQ {_dmy(exd[-1]) if exd else ''}: giá quá khứ đã điều chỉnh, "
                                         "khối lượng thì chưa, nên tín hiệu cần kiểm tra lại"),
                }
                valid = all(checks.values())
                level, note = market_note(market)
                market_text = "" if level == "ok" else f" Thị trường: {note}."
                if valid:
                    add(BREAKOUT,
                        f"Breakout hợp lệ: đóng cửa {_n(today.close)} vượt pivot {_n(before.pivot)} của "
                        f"{BASE_TEXT.get(before.base_kind, before.base_kind)}; {vol_text} Đóng cửa ở {close_pos:.0%} biên độ phiên.",
                        "Đủ các điều kiện: KL ≥ 40% trên trung bình [TRANG GỐC IBD], đóng cửa ở nửa trên phiên, "
                        "chưa chạy xa khỏi pivot." + market_text + vol_note)
                else:
                    gaps = "; ".join(missing[k] for k, ok in checks.items() if not ok)
                    add(BREAKOUT_WEAK,
                        f"Breakout yếu: đóng cửa {_n(today.close)} vượt pivot {_n(before.pivot)} của "
                        f"{BASE_TEXT.get(before.base_kind, before.base_kind)} nhưng {gaps}.",
                        "Breakout thiếu xác nhận dễ thất bại [SÁCH]." + market_text + vol_note)

    # A recent breakout that has closed back under its pivot
    window = int(mon["breakout_failure_sessions"])
    for k in range(1, min(window, len(frame) - 3) + 1):
        f_prior = frame.iloc[: -k - 1]
        if len(f_prior) < 60:
            break
        base_prior = read_base(f_prior, exchange, screen)
        broke_close = float(frame["close"].iloc[-k - 1])
        if base_prior.pivot and broke_close > base_prior.pivot >= float(f_prior["close"].iloc[-1]):
            if today.close < base_prior.pivot <= float(frame["close"].iloc[-2]):
                add(BREAKOUT_FAILED,
                    f"Breakout ngày {_dmy(frame.index[-k - 1])} qua pivot {_n(base_prior.pivot)} "
                    f"đã đóng cửa trở lại dưới pivot ({_n(today.close)}).",
                    "Giá quay lại dưới pivot là một trong các dấu hiệu thất bại của SEPA [SÁCH].")
            break

    if before.stop and today.close < before.stop <= float(frame["close"].iloc[-2]):
        add(STOP_BREACHED,
            f"Đóng cửa {_n(today.close)} dưới mức stop tham chiếu {_n(before.stop)} (đáy nhịp co hẹp cuối "
            f"hoặc pivot − {screen['base']['max_stop_pct']}%).",
            "Mức stop là điểm vô hiệu hóa của nền giá; thủng nó là nền đã hỏng theo cách đo hiện tại.")

    # A new listing has no 52-week template to lose or regain yet.
    template_measurable = len(frame) >= int(screen["min_history_sessions"])
    lost = [k for k in before.price_criteria_passed if k not in today.price_criteria_passed] if template_measurable else []
    gained = [k for k in today.price_criteria_passed if k not in before.price_criteria_passed] if template_measurable else []
    if lost:
        add(TREND_TEMPLATE_BROKEN,
            "Mất tiêu chí " + ", ".join(f"{k} ({today.criteria[k]['text']})" for k in lost) + ".",
            f"Trend Template còn {len(today.price_criteria_passed)}/7 tiêu chí giá (TT8 giữ theo lần lọc).")
    if gained and len(today.price_criteria_passed) == len(PRICE_CRITERIA):
        add(TREND_TEMPLATE_RESTORED,
            "Đạt lại đủ 7 tiêu chí giá của Trend Template (thêm " + ", ".join(gained) + ").",
            "Cấu trúc xu hướng Stage 2 được khôi phục theo cách đo hiện tại.")

    close = frame["close"].astype("float64")
    ma50 = ind.sma(close, 50)
    heavy = today.volume_ratio is not None and today.volume_ratio >= float(mon["heavy_volume_ratio"])
    if len(ma50.dropna()) > 1 and close.iloc[-1] < ma50.iloc[-1] and close.iloc[-2] >= ma50.iloc[-2] and heavy:
        add(MA50_HEAVY_VOLUME,
            f"Đóng cửa dưới MA50 ({_n(ma50.iloc[-1])}) với KL {_n(today.volume_ratio, 2)}× trung bình.",
            "IBD thường coi đóng cửa dưới MA50 với KL lớn là tín hiệu bán khi yếu [THỨ CẤP].")

    lookback = int(mon["largest_down_lookback_sessions"])
    changes = close.pct_change(fill_method=None).iloc[-lookback:]
    if len(changes.dropna()) >= 20 and changes.iloc[-1] < 0 and changes.iloc[-1] <= changes.min():
        add(LARGEST_DOWN_DAY,
            f"Phiên giảm {_n(changes.iloc[-1] * 100, 1)}%, mạnh nhất trong {lookback} phiên.",
            "Minervini xem phiên giảm lớn nhất kể từ đầu Stage 2 là dấu hiệu bán khi yếu [SÁCH]; "
            "cửa sổ đo là lựa chọn triển khai.")

    within = float(mon["setup_ready_within_pct"])

    def ready(s: TickerState) -> bool:
        return (s.base_kind in {"VCP", "FLAT_BASE", "CUP_HANDLE", "CUP"} and s.volume_dryup is True
                and s.distance_to_pivot_pct is not None and -within <= s.distance_to_pivot_pct <= 0)

    if ready(today) and not ready(before):
        add(SETUP_READY,
            f"{BASE_TEXT.get(today.base_kind, today.base_kind).capitalize()} ({_legs(today.contractions)}) "
            f"cách pivot {_n(today.distance_to_pivot_pct, 1)}%, KL đã cạn.",
            "Đủ các điều kiện đo được của một điểm pivot đang hình thành [TRIỂN KHAI]; "
            "chưa phải breakout.")

    up_window, up_days = int(mon["climax_up_window"]), int(mon["climax_up_days"])
    ups = int((close.diff().iloc[-up_window:] > 0).sum())
    above = today.above_ma200_pct
    reasons = []
    if above is not None and above >= float(mon["climax_above_ma200_pct"]):
        reasons.append(f"giá cao hơn MA200 {above:.0f}%")
    if ups >= up_days:
        reasons.append(f"{ups}/{up_window} phiên tăng")
    before_ups = int((close.diff().iloc[-up_window - 1:-1] > 0).sum())
    before_above = before.above_ma200_pct
    was = (before_above is not None and before_above >= float(mon["climax_above_ma200_pct"])) or before_ups >= up_days
    if reasons and not was:
        add(CLIMAX_WARNING, "Dấu hiệu tăng tốc kiểu climax: " + "; ".join(reasons) + ".",
            "IBD liệt kê các dấu hiệu này trong nhóm đỉnh climax [TRANG GỐC]; đây là cảnh báo, không phải kết luận.")
    return out


def _legs(contractions: Sequence[float], shown: int = 4) -> str:
    """'n nhịp, gần nhất a/b/c/d%' -- long bases produce many small legs."""
    if not contractions:
        return "chưa đo được nhịp"
    tail = "/".join(f"{x:.0f}" for x in contractions[-shown:])
    return f"{len(contractions)} nhịp, gần nhất {tail}%"


# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

def monitored_tickers(path: Path | None = None) -> tuple[list[str], dict[str, float], dict[str, str]]:
    """watchlist.yaml, the latest screen run (with its RS and group), its industry leaders, then the user's picks."""
    cfg = load_config()
    tickers = list(cfg.watchlist.active_tickers)
    rs: dict[str, float] = {}
    group: dict[str, str] = {}
    with connect(path) as conn:
        row = conn.execute("SELECT run_id FROM screen_runs ORDER BY created_at DESC LIMIT 1").fetchone()
        if row:
            for t, r, g in conn.execute(
                "SELECT ticker, rs_rating, grp FROM screen_members WHERE run_id = ? ORDER BY rank", (row[0],)
            ):
                if r is not None:
                    rs[t] = float(r)
                group[t] = g
                if t not in tickers:
                    tickers.append(t)
            # Industry leaders ("Đầu ngành" tab) are watched too; they carry no group.
            for (t,) in conn.execute(
                "SELECT ticker FROM screen_industry_leaders WHERE run_id = ? ORDER BY rowid", (row[0],)
            ):
                if t not in tickers:
                    tickers.append(t)
            # Names off the list still get the RS the screen ranked them at, if they were liquid.
            funnel = conn.execute("SELECT funnel_json FROM screen_runs WHERE run_id = ?", (row[0],)).fetchone()[0]
            for t, r in ((json.loads(funnel or "{}") or {}).get("rs_ratings") or {}).items():
                rs.setdefault(t, float(r))
        # The user's own picks ("Danh mục của tôi").
        for (t,) in conn.execute("SELECT ticker FROM user_watchlist ORDER BY added_at"):
            if t not in tickers:
                tickers.append(t)
    return tickers, rs, group


def _frames(tickers: Sequence[str], path: Path | None) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    frames: dict[str, pd.DataFrame] = {}
    with connect(path) as conn:
        exchanges = {r[0]: r[1] for r in conn.execute("SELECT ticker, exchange FROM tickers")}
        for t in tickers:
            rows = conn.execute(
                "SELECT trade_date, open, high, low, close, volume FROM ("
                "  SELECT * FROM prices_daily WHERE ticker = ? ORDER BY trade_date DESC LIMIT 400"
                ") ORDER BY trade_date", (t,)).fetchall()
            if rows:
                df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume"])
                frames[t] = df.set_index(pd.to_datetime(df["trade_date"])).drop(columns="trade_date").astype("float64")
    return frames, exchanges


def run_monitor(persist: bool = True, path: Path | None = None,
                thresholds: Mapping[str, Any] | None = None, keep_events: bool = False) -> MonitorResult:
    """`keep_events`: recompute the states but keep the session's stored events (a re-run
    after a rule change: what happened on that session stays as it was recorded)."""
    screen = screen_settings(thresholds)
    mon = monitor_settings(thresholds)
    tickers, screen_rs, group = monitored_tickers(path)
    frames, exchanges = _frames(tickers, path)
    hold = int(screen["base"].get("hold_sessions", 0) or 0)
    stored = _latest_states(tickers, path)

    index_symbol = screen["market"]["index"]
    with connect(path) as conn:
        idx_rows = conn.execute(
            "SELECT trade_date, close, volume, low, high, matched_volume FROM (SELECT * FROM index_daily WHERE symbol = ? "
            "ORDER BY trade_date DESC LIMIT 400) ORDER BY trade_date", (index_symbol,)).fetchall()
    idx = pd.DataFrame(idx_rows, columns=["trade_date", "close", "volume", "low", "high", "matched_volume"])
    index_close = (pd.Series(idx["close"].astype("float64").to_numpy(), index=pd.to_datetime(idx["trade_date"]))
                   if len(idx) else None)

    result = MonitorResult(as_of=None, tickers=tickers, screen_rs=screen_rs, screen_group=group)
    result.market = market_status(idx, screen)
    result.market_before = market_status(idx.iloc[:-1], screen) if len(idx) > 1 else {}

    # Recent listings are monitored from `recent_listings.min_sessions`; the
    # Trend Template events wait until a year of history exists.
    min_bars = int((screen.get("recent_listings") or {}).get("min_sessions", 210)) + 1
    limits = reference_lookup([(t, d.date().isoformat()) for t, f in frames.items() for d in f.index[-2:]], path)
    with connect(path) as conn:
        actions = _adjustments(conn)                # recorded ex-rights rescales (src/collectors/refprices.py)
    for t in tickers:
        f = frames.get(t)
        ex = exchanges.get(t)
        if f is None or ex is None or len(f) < min_bars:
            result.missing[t] = f"không đủ dữ liệu giá (cần ≥ {min_bars} phiên)"
            continue
        days = [d.date().isoformat() for d in f.index[-2:]]
        ceil = [(limits.get((t, d)) or {}).get("ceiling") for d in days]
        acts = actions.get(t, [])
        today = compute_state(t, ex, f, index_close, screen_rs.get(t), screen, mon, ceiling=ceil[-1], actions=acts)
        before = compute_state(t, ex, f.iloc[:-1], index_close, screen_rs.get(t), screen, mon, with_tech=False,
                               ceiling=ceil[0] if len(ceil) > 1 else None, actions=acts)
        for state, frame in ((today, f), (before, f.iloc[:-1])):
            held = legacy_hold(stored.get(t), frame, hold, acts)
            if held:
                apply_legacy_hold(state, frame, held)
        result.states[t] = today
        if not keep_events:
            result.events.extend(detect_events(t, ex, f, today, before, screen, mon, result.market, actions=acts))

    dates = [s.trade_date for s in result.states.values()]
    result.as_of = max(dates) if dates else None
    _assess_statuses(result, screen, mon, path, keep_events)
    if persist:
        _persist(result, path, keep_events)
    return result


def _latest_states(tickers: Sequence[str], path: Path | None) -> dict[str, dict[str, Any]]:
    """Each ticker's most recent stored state (for legacy_hold)."""
    if not tickers:
        return {}
    marks = ",".join("?" * len(tickers))
    with connect(path) as conn:
        rows = conn.execute(
            f"SELECT s.ticker, s.state_json FROM monitor_state s JOIN (SELECT ticker, MAX(trade_date) d FROM monitor_state "
            f"WHERE ticker IN ({marks}) GROUP BY ticker) x ON x.ticker = s.ticker AND x.d = s.trade_date",
            tuple(tickers)).fetchall()
    return {t: json.loads(j) for t, j in rows}


def _finite(value: Any) -> Any:
    """Replace NaN/Infinity (a recent listing has no MA200 yet) with None."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, dict):
        return {k: _finite(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_finite(v) for v in value]
    return value


def _assess_statuses(result: MonitorResult, screen: Mapping[str, Any], mon: Mapping[str, Any],
                     path: Path | None, keep_events: bool = False) -> None:
    """Status per ticker from today's events plus stored alerts of the previous sessions.

    Stored alerts for today's session are ignored in favour of the fresh events,
    so a re-run after a rule change is decided by the current rules -- unless
    `keep_events`, where the session's stored alerts stand.
    """
    window = int(mon["status_window_sessions"])
    today_events: dict[str, list[tuple[str, str]]] = {}
    for e in result.events:
        today_events.setdefault(e.ticker, []).append((e.trade_date.isoformat(), e.event_type))
    with connect(path) as conn:
        for t, state in result.states.items():
            sessions = [r[0] for r in conn.execute(
                "SELECT trade_date FROM prices_daily WHERE ticker = ? AND trade_date < ? "
                "ORDER BY trade_date DESC LIMIT ?", (t, state.trade_date.isoformat(), window - 1))]
            if keep_events:
                sessions.insert(0, state.trade_date.isoformat())
            earlier: list[tuple[str, str]] = []
            for day in sessions:
                for (kind,) in conn.execute(
                        "SELECT alert_type FROM alerts WHERE ticker = ? AND what_changed LIKE ?", (t, f"[{day}]%")):
                    earlier.append((day, kind))
            state.setup = assess(result.screen_group.get(t), state.base_kind, state.distance_to_pivot_pct,
                                 screen, result.market, earlier + today_events.get(t, []),
                                 stop_breached=state.stop_breached)


def _persist(result: MonitorResult, path: Path | None, keep_events: bool = False) -> None:
    init_db(path)
    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    with connect(path) as conn:
        for s in result.states.values():
            conn.execute(
                "INSERT INTO monitor_state (ticker, trade_date, state_json, computed_at) VALUES (?, ?, ?, ?) "
                "ON CONFLICT(ticker, trade_date) DO UPDATE SET state_json=excluded.state_json, "
                "computed_at=excluded.computed_at",
                (s.ticker, s.trade_date.isoformat(), json.dumps(_finite(s.as_dict()), ensure_ascii=False), now),
            )
        if keep_events:
            return
        # A re-run replaces the session's alerts for the monitored tickers, so an
        # event the current rules no longer see (e.g. after a rule change) does
        # not linger. Other sessions' alerts are untouched.
        current = {e.alert_id for e in result.events}
        for s in result.states.values():
            prefix = f"[{s.trade_date.isoformat()}]%"
            stale = [r[0] for r in conn.execute(
                "SELECT alert_id FROM alerts WHERE ticker = ? AND what_changed LIKE ?", (s.ticker, prefix))
                if r[0] not in current]
            conn.executemany("DELETE FROM alerts WHERE alert_id = ?", [(a,) for a in stale])
        for e in result.events:
            # Same ticker, session and type -> same alert_id: a re-run refreshes the wording, never duplicates.
            conn.execute(
                "INSERT INTO alerts (alert_id, ticker, created_at, alert_type, what_changed, why_it_matters) "
                "VALUES (?, ?, ?, ?, ?, ?) ON CONFLICT(alert_id) DO UPDATE SET "
                "what_changed = excluded.what_changed, why_it_matters = excluded.why_it_matters",
                (e.alert_id, e.ticker, now, e.event_type, f"[{e.trade_date}] {e.what_changed}", e.why_it_matters),
            )


# ---------------------------------------------------------------------------
# Brief for in-session reading
# ---------------------------------------------------------------------------

def render_brief(result: MonitorResult) -> str:
    m = result.market
    lines = [
        f"# Theo dõi danh sách SEPA — phiên {result.as_of}",
        "",
        "Ứng viên nghiên cứu, không phải khuyến nghị. Sự kiện mô tả điều giá và khối lượng đã làm.",
        "",
        f"**Thị trường:** {m.get('status')} (hôm trước: {result.market_before.get('status', '?')}) — "
        f"{m.get('index', '')} {m.get('close', '')}, MA50 {m.get('ma50', '')}, MA200 {m.get('ma200', '')}, "
        f"ngày phân phối {m.get('distribution_days', '?')}/{m.get('window', '?')}.",
        "",
        f"## Sự kiện ({len(result.events)})",
        "",
    ]
    if not result.events:
        lines.append("Không có sự kiện mới.")
    for e in sorted(result.events, key=lambda e: (e.event_type, e.ticker)):
        lines.append(f"- **{e.ticker} — {e.event_type}**: {e.what_changed} _{e.why_it_matters}_")
    lines += ["", "## Trạng thái", "",
              "| Mã | Nhóm | Giá | ±% | KL/TB | TT giá | RS lọc | RS vs index 63p | Nền | Pivot | Cách | Stop |",
              "|---|---|---|---|---|---|---|---|---|---|---|---|"]

    def f(v: Any, fmt: str) -> str:
        return "-" if v is None else format(v, fmt)

    for t in result.tickers:
        s = result.states.get(t)
        if s is None:
            lines.append(f"| {t} | {result.screen_group.get(t, 'WL')} | {result.missing.get(t, '-')} |||||||||| ")
            continue
        lines.append(
            f"| {t} | {result.screen_group.get(t, 'WL')} | {s.close:,.0f} | {f(s.change_pct, '+.1f')} | "
            f"{f(s.volume_ratio, '.2f')} | {len(s.price_criteria_passed)}/7 | {f(result.screen_rs.get(t), '.0f')} | "
            f"{f(s.rs_vs_index_63d, '+.1f')} | {s.base_kind} | {f(s.pivot, ',.0f')} | "
            f"{f(s.distance_to_pivot_pct, '+.1f')} | {f(s.stop, ',.0f')} |")
    lines += ["", "WL = chỉ nằm trong watchlist.yaml. RS lọc = RS Rating tại lần lọc gần nhất (chỉ đổi khi lọc lại)."]
    return "\n".join(lines) + "\n"
