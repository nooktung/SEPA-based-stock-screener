"""Backtest of the SEPA screen's own rules on Vietnamese data.

Question answered: when a stock met a rule on a given day, using only data up
to that day, what did it do over the next 5 / 10 / 20 / 60 sessions -- and was
that better than an ordinary liquid stock on the same days?

Signals (all dated when knowable; entry is the next session's open, costs
deducted by the engine):

    baseline_liquid        every liquid stock, sampled every `step` sessions (the control)
    tt_hold                a stock passing 8/8 Trend Template, sampled every `step` sessions
    tt_entry               the first day a stock passes 8/8 after at least `reentry_gap` days without
                           -- status = RS tier that day (STRONG_LEADER / LEADER / QUALIFIED)
    tt_entry_tt6_25        the same, with TT6 at 25% instead of 30% (the disputed threshold D1)
    breakout_<BASE>        close crossing the previous day's pivot, by base label, status =
                           VALID / WEAK (the monitor's rules: volume, close position, not extended)
    breakout_valid_tt      valid breakouts of stocks passing 8/8 on the day, status = market regime

Two exits are measured for every signal (2026-10-11):

    fixed      sell at the close N sessions after the buy, whatever happened on the way
    stop       the same, but out early after the first CLOSE at or below the stop -- a
               breakout's base stop (risk <= 8% from the pivot), else the buy price - 8%
               (Minervini's maximum loss). Vietnam's rules apply: shares bought on B can be
               sold from the afternoon of B+2 (only that session's close is reachable then);
               a stop seen at a close is sold at the next sellable open; a session locked at
               the floor cannot be sold (src/backtest/swing.py `Bars`).

Limits stated up front: the universe is today's listings (no delisted names:
survivorship bias flatters every number); DNSE prices omit cash dividends; the
market regime here is the simplified one (index vs MAs and distribution days,
no follow-through day).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any, Callable, Mapping, Sequence

import numpy as np
import pandas as pd

from src.backtest.engine import BacktestConfig, Outcome, Signal, evaluate_signal, split_sample, summarise
from src.backtest.swing import Bars, _next_sellable
from src.database import connect
from src.quant import indicators as ind
from src.quant.screener import read_base, screen_settings
from src.quant.setup_status import rs_tier

log = logging.getLogger(__name__)

DEFAULT_HORIZONS = (5, 10, 20, 60)


# ---------------------------------------------------------------------------
# Panels
# ---------------------------------------------------------------------------

@dataclass
class Panel:
    """Aligned date x ticker matrices built once for the whole universe."""

    frames: dict[str, pd.DataFrame]
    exchanges: dict[str, str]
    close: pd.DataFrame
    liquid: pd.DataFrame          # bool
    rs: pd.DataFrame              # RS rating 1..99 among the liquid names that day
    tt: dict[str, pd.DataFrame] = field(default_factory=dict)   # criteria "TT1".."TT7" -> bool frames


def load_frames(path: Path | None = None, limit: int | None = None,
                tickers: Sequence[str] | None = None) -> tuple[dict[str, pd.DataFrame], dict[str, str]]:
    with connect(path) as conn:
        exchanges = {r[0]: r[1] for r in conn.execute("SELECT ticker, exchange FROM tickers WHERE active = 1")}
        names = list(tickers) if tickers else sorted(exchanges)
        frames: dict[str, pd.DataFrame] = {}
        for t in names:
            rows = conn.execute(
                "SELECT trade_date, open, high, low, close, volume FROM prices_daily WHERE ticker = ? "
                "ORDER BY trade_date", (t,)).fetchall()
            if len(rows) < 300:
                continue
            df = pd.DataFrame(rows, columns=["trade_date", "open", "high", "low", "close", "volume"])
            frames[t] = df.set_index(pd.to_datetime(df["trade_date"])).drop(columns="trade_date").astype("float64")
            if limit and len(frames) >= limit:
                break
    return frames, {t: exchanges[t] for t in frames}


def build_panel(frames: Mapping[str, pd.DataFrame], exchanges: Mapping[str, str],
                settings: Mapping[str, Any]) -> Panel:
    close = pd.DataFrame({t: f["close"] for t, f in frames.items()}).sort_index()
    volume = pd.DataFrame({t: f["volume"] for t, f in frames.items()}).reindex(close.index)
    high = pd.DataFrame({t: f["high"] for t, f in frames.items()}).reindex(close.index)
    low = pd.DataFrame({t: f["low"] for t, f in frames.items()}).reindex(close.index)

    liq = settings["liquidity"]
    value = (close * volume).rolling(int(liq["window"]), min_periods=int(liq["window"])).mean()
    floors = pd.Series({t: float(liq["min_value_vnd"].get(exchanges[t].upper(), max(liq["min_value_vnd"].values())))
                        for t in close.columns})
    active = (volume > 0).astype(float).rolling(int(liq["active_window"]), min_periods=1).mean()
    history = close.notna().cumsum()
    liquid = (value.ge(floors, axis=1)) & (active >= float(liq["min_active_session_ratio"])) \
        & (history >= int(settings["min_history_sessions"]))

    # RS raw score (variant A) and its percentile among the liquid names each day.
    p3, p6, p9, p12 = (int(x) for x in settings["rs"]["periods"])
    ret = lambda n: close / close.shift(n) - 1.0  # noqa: E731
    raw = 0.4 * ret(p3) + 0.2 * ret(p6) + 0.2 * ret(p9) + 0.2 * ret(p12)
    raw = raw.where(liquid)
    rs = (raw.rank(axis=1, pct=True) * 100.0).round().clip(1, 99)

    sma = {n: close.rolling(n, min_periods=n).mean() for n in (50, 150, 200)}
    tt_cfg = settings["trend_template"]
    n52 = int(settings["lookback_52w_sessions"])
    low52 = low.rolling(n52, min_periods=n52).min()
    high52 = high.rolling(n52, min_periods=n52).max()
    tt = {
        "TT1": (close > sma[150]) & (close > sma[200]),
        "TT2": sma[150] > sma[200],
        "TT3": sma[200] > sma[200].shift(int(tt_cfg["ma200_rising_sessions"])),
        "TT4": (sma[50] > sma[150]) & (sma[50] > sma[200]),
        "TT5": close > sma[50],
        "TT6": close >= low52 * (1 + float(tt_cfg["above_52w_low_pct"]) / 100.0),
        "TT6_25": close >= low52 * 1.25,
        "TT7": close >= high52 * (1 - float(tt_cfg["within_52w_high_pct"]) / 100.0),
    }
    return Panel(dict(frames), dict(exchanges), close, liquid, rs, tt)


def regime_by_date(index: pd.DataFrame, settings: Mapping[str, Any]) -> pd.Series:
    """The screener's simplified market status, computed for every date."""
    m = settings["market"]
    c = index["close"].astype("float64")
    ma50, ma200 = c.rolling(50).mean(), c.rolling(200).mean()
    # Distribution days still counted on each date (25 sessions, or until a 5% rally): the screener's own rule.
    from src.quant.screener import active_distribution_count  # noqa: PLC0415

    pressure = active_distribution_count(index, settings) >= int(m["pressure_distribution_days"])
    status = pd.Series("MIXED", index=index.index)
    status[(c > ma50) & (ma50 > ma200) & ~pressure] = "UPTREND"
    status[(pressure | (c < ma50)) & (c >= ma200)] = "UNDER_PRESSURE"
    status[c < ma200] = "CORRECTION"
    status[ma200.isna()] = None
    return status


# ---------------------------------------------------------------------------
# Signals
# ---------------------------------------------------------------------------

def generate_sepa_signals(panel: Panel, settings: Mapping[str, Any], monitor: Mapping[str, Any],
                          regimes: pd.Series | None = None, step: int = 5, reentry_gap: int = 20,
                          progress: Callable[[int, int], None] | None = None) -> list[Signal]:
    signals: list[Signal] = []
    min_rs = float(settings["trend_template"]["min_rs_rating"])
    rs_ok = panel.rs >= min_rs
    passing = panel.liquid & rs_ok
    passing_25 = passing.copy()
    for k in ("TT1", "TT2", "TT3", "TT4", "TT5", "TT6", "TT7"):
        passing &= panel.tt[k]
        passing_25 &= panel.tt["TT6_25" if k == "TT6" else k]

    def regime(stamp: pd.Timestamp) -> str | None:
        if regimes is None or stamp not in regimes.index:
            return None
        value = regimes.loc[stamp]
        return None if value is None or (isinstance(value, float) and np.isnan(value)) else str(value)

    dates = panel.close.index
    sample_rows = np.arange(len(dates)) % step == 0
    tickers = list(panel.close.columns)
    for n, t in enumerate(tickers):
        liquid_t = panel.liquid[t].to_numpy()
        pass_t = passing[t].to_numpy()
        pass25_t = passing_25[t].to_numpy()
        rs_t = panel.rs[t].to_numpy()
        for i in np.flatnonzero(liquid_t & sample_rows):
            signals.append(Signal(t, "baseline_liquid", "SAMPLED", dates[i].date(), regime(dates[i])))
        for i in np.flatnonzero(pass_t & sample_rows):
            signals.append(Signal(t, "tt_hold", "SAMPLED", dates[i].date(), regime(dates[i])))
        for flags, name in ((pass_t, "tt_entry"), (pass25_t, "tt_entry_tt6_25")):
            last_pass = -10**9
            for i in np.flatnonzero(flags):
                if i - last_pass > reentry_gap:
                    tier = rs_tier(float(rs_t[i]), settings) or "BELOW_70"
                    signals.append(Signal(t, name, tier, dates[i].date(), regime(dates[i]),
                                          {"rs": float(rs_t[i])}))
                last_pass = i
        signals.extend(_breakouts(panel, t, pass_t, settings, monitor, regime))
        if progress:
            progress(n + 1, len(tickers))
    return signals


def _breakouts(panel: Panel, ticker: str, pass_t: np.ndarray, settings: Mapping[str, Any],
               monitor: Mapping[str, Any], regime: Callable[[pd.Timestamp], str | None]) -> list[Signal]:
    """Close crossing yesterday's pivot. The base is read on data up to yesterday only."""
    frame = panel.frames[ticker]
    exchange = panel.exchanges[ticker]
    pos = {d: i for i, d in enumerate(panel.close.index)}
    close = frame["close"].to_numpy()
    high = frame["high"].to_numpy()
    vol = frame["volume"].to_numpy()
    liquid = panel.liquid[ticker].reindex(frame.index).fillna(False).to_numpy()
    # Cheap prefilter: a pivot is at or below the recent high, so a crossing
    # needs a close at a 15-session high. Only those days pay for read_base.
    prior_high = pd.Series(high).shift(1).rolling(15, min_periods=15).max().to_numpy()
    candidates = np.flatnonzero(liquid & (close >= prior_high * 0.97))
    zone = float(settings["base"]["buy_zone_max_pct"])
    out: list[Signal] = []
    for i in candidates:
        if i < 260:
            continue
        before = read_base(frame.iloc[:i], exchange, settings)
        if not before.pivot or before.kind not in {"VCP", "FLAT_BASE", "CUP_HANDLE", "CUP", "BASE"}:
            continue
        if not (close[i] > before.pivot >= close[i - 1]):
            continue
        avg = vol[max(0, i - 50):i].mean()
        ratio = vol[i] / avg if avg > 0 else 0.0
        rng = high[i] - frame["low"].iloc[i]
        close_pos = (close[i] - frame["low"].iloc[i]) / rng if rng > 0 else 1.0
        valid = (ratio >= float(monitor["breakout_volume_ratio"])
                 and close_pos >= float(monitor["breakout_min_close_position"])
                 and close[i] <= before.pivot * (1 + zone / 100.0))
        stamp = frame.index[i]
        status = "VALID" if valid else "WEAK"
        meta = {"volume_ratio": round(float(ratio), 2), "close_position": round(float(close_pos), 2),
                "stop": before.stop}
        out.append(Signal(ticker, f"breakout_{before.kind}", status, stamp.date(), regime(stamp), meta))
        j = pos.get(stamp)
        if valid and j is not None and pass_t[j]:
            out.append(Signal(ticker, "breakout_valid_tt", regime(stamp) or "UNKNOWN", stamp.date(), regime(stamp), meta))
    return out


# ---------------------------------------------------------------------------
# Exit at the stop
# ---------------------------------------------------------------------------

def stop_exits(bars: Bars, entry: int, stop: float, horizons: Sequence[int],
               first_sell: int) -> dict[int, tuple[float, bool]]:
    """Gross % return per horizon, and whether the stop ended the trade.

    Held up to `horizon` sessions after the buy on `entry` (bought at its open). The first
    close at or below `stop` ends it: sold at the next sellable session's open, or, when
    that is the first session the shares can be sold (`first_sell` after the buy, the
    afternoon of T+2), at its close. Not stopped: sold at the close `horizon` sessions on,
    moved past floor-locked sessions like the fixed exit.
    """
    price = bars.open[entry]
    n = len(bars.close)
    last = min(n - 1, entry + max(horizons))
    below = np.flatnonzero(bars.close[entry:last + 1] <= stop)
    hit = entry + int(below[0]) if len(below) else None
    out: dict[int, tuple[float, bool]] = {}
    for h in horizons:
        if hit is not None and hit <= entry + h:
            first = entry + first_sell
            d = _next_sellable(bars, max(hit + 1, first))
            if d is None:
                continue
            fill = bars.close[d] if d == first else bars.open[d]
            out[h] = ((fill / price - 1.0) * 100.0, True)
        else:
            d = _next_sellable(bars, entry + h)
            if d is None:
                continue
            out[h] = ((bars.close[d] / price - 1.0) * 100.0, False)
    return out


def evaluate_with_stop(signal: Signal, frame: pd.DataFrame, bars: Bars, config: BacktestConfig,
                       max_loss_pct: float, first_sell: int) -> tuple[Outcome, dict[int, bool]]:
    """The signal's outcome with the stop exit, net of the same costs as the fixed exit."""
    outcome = Outcome(signal=signal)
    stamp = pd.Timestamp(signal.signal_date)
    if stamp not in frame.index:
        return outcome, {}
    entry = frame.index.get_loc(stamp) + 1
    if entry >= len(frame) or bars.open[entry] <= 0:
        return outcome, {}
    price = float(bars.open[entry])
    stop = signal.metadata.get("stop") or price * (1 - max_loss_pct / 100.0)
    outcome.entry_date, outcome.entry_price = frame.index[entry].date(), price
    stopped: dict[int, bool] = {}
    for h, (gross, was_stopped) in stop_exits(bars, entry, float(stop), config.horizons, first_sell).items():
        outcome.returns[h] = gross - config.round_trip_cost_pct
        stopped[h] = was_stopped
    return outcome, stopped


# ---------------------------------------------------------------------------
# Run and report
# ---------------------------------------------------------------------------

@dataclass
class SepaBacktest:
    stats: dict[str, Any]
    in_sample: dict[str, Any]
    out_sample: dict[str, Any]
    config: BacktestConfig
    tickers: int
    first: date | None
    last: date | None
    # the same signals with the stop exit; share of trades the stop ended, per key and horizon
    stop_stats: dict[str, Any] = field(default_factory=dict)
    stop_out_sample: dict[str, Any] = field(default_factory=dict)
    stopped_share: dict[str, dict[int, float]] = field(default_factory=dict)


def run_sepa_backtest(path: Path | None = None, limit: int | None = None, step: int = 5,
                      horizons: Sequence[int] = DEFAULT_HORIZONS,
                      progress: Callable[[int, int], None] | None = None) -> SepaBacktest:
    from src.config import load_config

    settings = screen_settings()
    monitor = dict(load_config().thresholds.raw["monitor"])
    frames, exchanges = load_frames(path, limit)
    panel = build_panel(frames, exchanges, settings)
    with connect(path) as conn:
        rows = conn.execute("SELECT trade_date, close, volume, high FROM index_daily WHERE symbol = ? ORDER BY trade_date",
                            (settings["market"]["index"],)).fetchall()
    index = pd.DataFrame(rows, columns=["trade_date", "close", "volume", "high"])
    index = index.set_index(pd.to_datetime(index["trade_date"])).drop(columns="trade_date").astype("float64")
    regimes = regime_by_date(index, settings) if len(index) else None

    base_cfg = BacktestConfig.from_thresholds()
    config = BacktestConfig(horizons=tuple(horizons), entry=base_cfg.entry, exit=base_cfg.exit,
                            brokerage_fee_pct=base_cfg.brokerage_fee_pct, sell_tax_pct=base_cfg.sell_tax_pct,
                            slippage_pct=base_cfg.slippage_pct, min_sample_size=base_cfg.min_sample_size,
                            robust_sample_size=base_cfg.robust_sample_size,
                            out_of_sample_fraction=base_cfg.out_of_sample_fraction)
    signals = generate_sepa_signals(panel, settings, monitor, regimes, step=step, progress=progress)
    outcomes: list[Outcome] = [evaluate_signal(s, frames[s.ticker], config) for s in signals]
    ins, outs = split_sample(outcomes, config)
    dates = [s.signal_date for s in signals]

    from src.market_calendar import price_limit_pct  # noqa: PLC0415

    swing = load_config().thresholds.raw["swing"]
    tol, first_sell = float(swing["ceiling_tolerance_pct"]), int(swing["first_sell_session"])
    bars = {t: Bars.of(f, float(price_limit_pct(exchanges[t]) or 7.0), tol) for t, f in frames.items()}
    max_loss = float(settings["base"]["max_stop_pct"])
    with_stop: list[Outcome] = []
    counts: dict[str, dict[int, list[int]]] = {}
    for sig in signals:
        o, stopped = evaluate_with_stop(sig, frames[sig.ticker], bars[sig.ticker], config, max_loss, first_sell)
        with_stop.append(o)
        for h, flag in stopped.items():
            c = counts.setdefault(sig.key, {}).setdefault(h, [0, 0])
            c[0] += int(flag)
            c[1] += 1
    _, stop_outs = split_sample(with_stop, config)
    return SepaBacktest(summarise(outcomes, config), summarise(ins, config), summarise(outs, config),
                        config, len(frames), min(dates) if dates else None, max(dates) if dates else None,
                        summarise(with_stop, config), summarise(stop_outs, config),
                        {k: {h: c[0] / c[1] * 100.0 for h, c in by_h.items() if c[1]} for k, by_h in counts.items()})


def render_report(bt: SepaBacktest) -> str:
    cfg = bt.config
    base = bt.stats.get("baseline_liquid:SAMPLED")

    def cell(stats, h: int) -> tuple[str, float | None]:
        hs = stats.horizons.get(h) if stats else None
        if not hs or not hs.sample_size:
            return "–", None
        sig = "*" if hs.distinguishable_from_noise else ""
        return f"{hs.mean_return:+.2f}% ({hs.win_rate:.0f}%){sig}", hs.mean_return

    lines = [
        "# Backtest các quy tắc SEPA trên dữ liệu Việt Nam", "",
        f"{bt.tickers} mã, tín hiệu từ {bt.first} tới {bt.last}. Vào lệnh giá mở cửa phiên sau tín hiệu, "
        f"bán giá đóng cửa sau N phiên, đã trừ {cfg.round_trip_cost_pct:.2f}% phí khứ hồi.", "",
        "**Giới hạn:** universe là các mã đang niêm yết (không có mã đã hủy — thiên lệch sống sót làm mọi con số "
        "đẹp hơn thực tế); giá không cộng cổ tức tiền mặt; trạng thái thị trường là bản rút gọn (không có FTD). "
        "Kết quả quá khứ không bảo đảm tương lai.", "",
        "Mỗi ô: lợi nhuận trung bình sau phí (tỷ lệ thắng). `*` = khác 0 có ý nghĩa thống kê (|t| ≥ 2). "
        "**Hơn nền** = chênh lệch so với mã thanh khoản bình thường ở cùng horizon.", "",
        "| Tín hiệu | Trạng thái | N | " + " | ".join(f"T+{h}" for h in cfg.horizons)
        + f" | Hơn nền T+{cfg.horizons[-2]} | Hơn nền T+{cfg.horizons[-1]} | Ngoài mẫu T+{cfg.horizons[-2]} |",
        "|---|---|---|" + "---|" * (len(cfg.horizons) + 3),
    ]
    for key, st in sorted(bt.stats.items(), key=lambda kv: (kv[1].pattern != "baseline_liquid", kv[0])):
        cells, vs = [], []
        for h in cfg.horizons:
            text, mean = cell(st, h)
            cells.append(text)
        for h in cfg.horizons[-2:]:
            _, mean = cell(st, h)
            _, bmean = cell(base, h)
            vs.append("–" if mean is None or bmean is None else f"{mean - bmean:+.2f}")
        _, oos = cell(bt.out_sample.get(key), cfg.horizons[-2])
        lines.append(f"| {st.pattern} | {st.status} | {st.sample_size} | " + " | ".join(cells)
                     + f" | {vs[0]} | {vs[1]} | {'–' if oos is None else f'{oos:+.2f}%'} |")
    lines += ["", f"Mẫu tối thiểu: {cfg.min_sample_size} tín hiệu; dưới mức này con số chỉ mô tả mẫu, "
              "không nói gì về quy tắc. Ngoài mẫu = {:.0%} tín hiệu mới nhất.".format(cfg.out_of_sample_fraction)]
    if bt.stop_stats:
        lines += _stop_table(bt)
    return "\n".join(lines) + "\n"


def _stop_table(bt: SepaBacktest) -> list[str]:
    cfg = bt.config
    h1, h2 = cfg.horizons[-2], cfg.horizons[-1]
    base = bt.stop_stats.get("baseline_liquid:SAMPLED")

    def mean(stats, h):
        hs = stats.horizons.get(h) if stats else None
        return (hs.mean_return, hs.win_rate, hs.distinguishable_from_noise) if hs and hs.sample_size else (None, None, False)

    def cell(stats, h):
        m, w, sig = mean(stats, h)
        return "–" if m is None else f"{m:+.2f}% ({w:.0f}%){'*' if sig else ''}"

    def vs(stats, h):
        m, b = mean(stats, h)[0], mean(base, h)[0]
        return "–" if m is None or b is None else f"{m - b:+.2f}"

    lines = ["", "## Có stop (bán sau phiên đóng cửa chạm stop)", "",
             "Như bảng trên nhưng thoát sớm sau phiên **đóng cửa** ở hoặc dưới stop: breakout dùng stop của nền "
             "(rủi ro từ pivot ≤ 8%), các tín hiệu khác dùng giá mua − 8%. Quy tắc Việt Nam: mua phiên B thì sớm nhất "
             "chiều B+2 mới bán được (khi đó chỉ khớp được giá đóng cửa); thấy chạm stop ở giá đóng cửa thì bán giá mở "
             "cửa phiên kế tiếp; phiên khóa sàn không bán được. **Hơn nền** so với mã thanh khoản bình thường cũng "
             "có stop 8%.", "",
             f"| Tín hiệu | Trạng thái | N | T+{h1} | T+{h2} | Dừng bởi stop (T+{h2}) | Hơn nền T+{h1} | Hơn nền T+{h2} "
             f"| Ngoài mẫu T+{h1} |",
             "|---|---|---|---|---|---|---|---|---|"]
    for key, st in sorted(bt.stop_stats.items(), key=lambda kv: (kv[1].pattern != "baseline_liquid", kv[0])):
        share = bt.stopped_share.get(key, {}).get(h2)
        oos = mean(bt.stop_out_sample.get(key), h1)[0]
        lines.append(f"| {st.pattern} | {st.status} | {st.sample_size} | {cell(st, h1)} | {cell(st, h2)} | "
                     f"{'–' if share is None else f'{share:.0f}%'} | {vs(st, h1)} | {vs(st, h2)} | "
                     f"{'–' if oos is None else f'{oos:+.2f}%'} |")
    return lines
