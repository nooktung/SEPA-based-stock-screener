"""Trendlines, a parallel channel and the nearest support / resistance, for the chart.

What a trader draws by hand, made deterministic [TRIỂN KHAI]:

Trendline
    A straight line through two confirmed swing lows (a support line) or two
    confirmed swing highs (a resistance line), with no close beyond it between
    the two anchors (a `tolerance_pct` poke is allowed). The line whose second
    anchor is the most recent is taken; among those, the one touched by the
    most swings of its kind, then the longest. After the second anchor the
    line runs until the first close beyond it (the break) or, unbroken, to the
    last session plus `project_sessions` as a ray.

Parallel channel
    Built on the more recent of the two trendlines: the opposite rail is the
    parallel line through the farthest high (for a support line) or low (for a
    resistance line) over the trendline's span, so that rail holds every bar.
    On the trendline's own side the trendline's rule applies: no close more
    than `tolerance_pct` through it before the break, while wicks may poke
    through (checked by scripts/audit_chart.py, 2026-10-04). A dashed middle
    line splits it.

Nearest zones
    The nearest support and nearest resistance zone, at least `zones.min_distance_pct`
    from the price, among every zone src/quant/levels.py builds -- not only its few
    strongest, which left DRI's "nearest" support 24% below while its recent swing lows
    sat 7% below (tester M05). A zone the price is inside is reported as such. Prices are
    put on the exchange's tick grid (M06: 17,433 is not a price anyone can trade at).

Weekly and monthly charts (2026-10-06) run the same rules on weekly / monthly
candles (src/quant/timeframes.py) with their own counts, in bars of that
timeframe: `chart_levels.trendline_by_tf`. The ray then extends by weeks or
months. Zones stay those of the daily chart (price levels, any timeframe).

All of it describes where price has turned; none of it forecasts where it
will. Settings: `chart_levels` in config/thresholds.yaml.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from src.market_calendar import is_trading_day, next_trading_day
from src.quant.levels import (SIDE_RESISTANCE, SIDE_SUPPORT, Zone, ZoneConfig, collect_levels, merge_levels,
                              score_zones)
from src.quant.patterns import PatternConfig, find_pivots
from src.quant.screener import round_to_tick, tick_size
from src.quant.store import price_series


@dataclass
class Trendline:
    kind: str                      # SUPPORT (through lows) | RESISTANCE (through highs)
    anchors: list[tuple[str, float]]
    start: tuple[str, float]       # first anchor
    end: tuple[str, float]         # where the drawn line stops (break, or projection)
    slope_pct_month: float         # % per 21 sessions, relative to the line at the last anchor
    touches: int                   # swings of its kind within tolerance of the line
    broken_on: str | None          # first close beyond the line after the second anchor
    value_today: float | None      # the line at the last session (None when broken before it)
    # Last close against the line, % (None once broken): a close beyond it by less than
    # tolerance_pct has not broken it yet, and the chart says so (tester M07: HDB 22,400 > 22,228).
    close_vs_line_pct: float | None = None
    tolerance_pct: float = 0.0


@dataclass
class Channel:
    base: str                      # which trendline it is built on: SUPPORT | RESISTANCE
    upper: list[tuple[str, float]]
    middle: list[tuple[str, float]]
    lower: list[tuple[str, float]]
    width_pct: float               # band width at its start, % of the middle line
    position_pct: float | None     # last close in the band: 0 = lower rail, 100 = upper (None once broken)
    broken_on: str | None


def future_sessions(last: pd.Timestamp, n: int, tf: str = "D") -> list[str]:
    """The next `n` bar dates after `last`: sessions (D), Mondays (W) or each month's first
    session (M). Sessions follow DNSE's published calendar where it is stored
    (src/market_calendar.is_trading_day) and weekdays beyond it, so dates past the
    announced holidays are approximate."""
    if tf == "W":
        monday = last - pd.Timedelta(days=last.weekday())
        return [(monday + pd.Timedelta(weeks=k)).date().isoformat() for k in range(1, n + 1)]
    if tf == "M":
        out = []
        for k in range(1, n + 1):
            day = (last.to_period("M") + k).start_time.date()
            if not is_trading_day(day):
                day = next_trading_day(day)
            out.append(day.isoformat())
        return out
    out, day = [], last.date()
    for _ in range(n):
        day = next_trading_day(day)
        out.append(day.isoformat())
    return out


def _find_line(f: pd.DataFrame, kind: str, cfg: Mapping[str, Any]) -> tuple[int, int, int] | None:
    """(first anchor, second anchor, touches) as row positions in `f`, or None."""
    pivot_kind = "LOW" if kind == SIDE_SUPPORT else "HIGH"
    pos = {d: k for k, d in enumerate(f.index.date)}
    pts = [(pos[p.trade_date], p.price) for p in find_pivots(f, int(cfg["pivot_strength"]))
           if p.kind == pivot_kind and p.confirmed_on <= f.index[-1].date()]
    close = f["close"].to_numpy(dtype="float64")
    tol = float(cfg["tolerance_pct"]) / 100.0
    min_span = int(cfg["min_span_sessions"])
    for jj in range(len(pts) - 1, 0, -1):
        j, pj = pts[jj]
        best: tuple[int, int, int] | None = None
        for ii in range(jj):
            i, pi = pts[ii]
            if j - i < min_span:
                continue
            slope = (pj - pi) / (j - i)
            line = pi + slope * (np.arange(i, j + 1) - i)
            seg = close[i:j + 1]
            beyond = seg < line * (1 - tol) if kind == SIDE_SUPPORT else seg > line * (1 + tol)
            if beyond.any():
                continue
            touches = sum(1 for k, p in pts if i <= k <= j and abs(p - (pi + slope * (k - i))) <= abs(p) * tol)
            key = (touches, j - i)
            if best is None or key > (best[2], best[1] - best[0]):
                best = (i, j, touches)
        if best:
            return best
    return None


def trendline(frame: pd.DataFrame, kind: str, cfg: Mapping[str, Any],
              tf: str = "D") -> tuple[Trendline, dict[str, Any]] | None:
    f = frame.iloc[-int(cfg["lookback_sessions"]):]
    found = _find_line(f, kind, cfg)
    if found is None:
        return None
    i, j, touches = found
    lo, hi = f["low"].to_numpy(dtype="float64"), f["high"].to_numpy(dtype="float64")
    anchor = lo if kind == SIDE_SUPPORT else hi
    pi, pj = float(anchor[i]), float(anchor[j])
    slope = (pj - pi) / (j - i)
    at = lambda x: pi + slope * (x - i)  # noqa: E731
    close = f["close"].to_numpy(dtype="float64")
    tol = float(cfg["tolerance_pct"]) / 100.0
    n = len(f)
    dates = [d.isoformat() for d in f.index.date]
    broken = None
    for k in range(j + 1, n):
        if (close[k] < at(k) * (1 - tol)) if kind == SIDE_SUPPORT else (close[k] > at(k) * (1 + tol)):
            broken = k
            break
    project = int(cfg["project_sessions"])
    if broken is not None:
        end_x, end_date = broken, dates[broken]
    else:
        end_x, end_date = n - 1 + project, future_sessions(f.index[-1], project, tf)[-1]
    line = Trendline(
        kind=kind, anchors=[(dates[i], round(pi, 2)), (dates[j], round(pj, 2))],
        start=(dates[i], round(pi, 2)), end=(end_date, round(at(end_x), 2)),
        slope_pct_month=round(slope * float(cfg.get("bars_per_month", 21)) / pj * 100.0, 2) if pj else 0.0,
        touches=touches,
        broken_on=dates[broken] if broken is not None else None,
        value_today=None if broken is not None else round(at(n - 1), 2),
        close_vs_line_pct=None if broken is not None or not at(n - 1) else round((close[-1] / at(n - 1) - 1) * 100.0, 2),
        tolerance_pct=float(cfg["tolerance_pct"]),
    )
    geometry = {"i": i, "end_x": end_x, "slope": slope, "at": at, "last_bar": min(end_x, n - 1),
                "dates": dates, "end_date": end_date, "low": lo, "high": hi, "close": close}
    return line, geometry


def parallel_channel(line: Trendline, g: Mapping[str, Any]) -> Channel:
    i, last = g["i"], g["last_bar"]
    xs = np.arange(i, last + 1)
    base = np.array([g["at"](x) for x in xs])
    if line.kind == SIDE_SUPPORT:
        offset = float(np.max(g["high"][i:last + 1] - base))
    else:
        offset = float(np.min(g["low"][i:last + 1] - base))
    points = [(g["dates"][i], i), (g["end_date"], g["end_x"])]

    def rail(off: float) -> list[tuple[str, float]]:
        return [(d, round(float(g["at"](x) + off), 2)) for d, x in points]

    main, other = rail(0.0), rail(offset)
    upper, lower = (other, main) if line.kind == SIDE_SUPPORT else (main, other)
    middle = [(d, round((u + l_) / 2, 2)) for (d, u), (_, l_) in zip(upper, lower)]
    mid0 = middle[0][1]
    width = abs(offset)
    position = None
    if line.broken_on is None and width > 0:
        today = len(g["close"]) - 1
        low_today = g["at"](today) + (0.0 if line.kind == SIDE_SUPPORT else offset)
        position = round((g["close"][today] - low_today) / width * 100.0, 1)
    return Channel(base=line.kind, upper=upper, middle=middle, lower=lower,
                   width_pct=round(width / mid0 * 100.0, 1) if mid0 else 0.0,
                   position_pct=position, broken_on=line.broken_on)


def trend_overlays(frame: pd.DataFrame, cfg: Mapping[str, Any], tf: str = "D",
                   exchange: str | None = None, base: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """Both trendlines and the channel of the more recent one, on bars of timeframe `tf`.
    On daily bars an opening spike is measured at its close, as the base reader does
    (`base.spike_bar`): ABB's resistance line was anchored on its 09/07 ceiling print (R3-09)."""
    if tf == "D" and exchange and base and base.get("spike_bar") and len(frame) > 1:
        from src.quant.screener import _effective_highs  # noqa: PLC0415

        frame = frame.assign(high=_effective_highs(frame, exchange, base)[0])
    lines, channel = {}, None
    found = {k: trendline(frame, k, cfg, tf) for k in (SIDE_SUPPORT, SIDE_RESISTANCE)}
    for k, res in found.items():
        if res:
            lines[k] = asdict(res[0])
    recent = [res for res in found.values() if res]
    if recent:
        # the channel follows the line whose second anchor is the more recent
        line, geometry = max(recent, key=lambda r: (r[0].anchors[1][0], r[0].touches))
        channel = asdict(parallel_channel(line, geometry))
    return {"trendlines": lines, "channel": channel,
            "projection_dates": future_sessions(frame.index[-1], int(cfg["project_sessions"]), tf)
            if recent and any(r[0].broken_on is None for r in recent) else []}


def _all_zones(frame: pd.DataFrame) -> tuple[list[Zone], float]:
    """Every zone levels.py builds (build_zones keeps only the strongest few), and the price."""
    config, patterns = ZoneConfig.from_thresholds(), PatternConfig.from_thresholds()
    as_of = frame.index[-1].date()
    zones = score_zones(merge_levels(collect_levels(frame, as_of, config, patterns), config), frame, as_of, config)
    price = float(price_series(frame).iloc[-1])
    for z in zones:
        z.distance_from_price = (z.center - price) / price * 100.0
        z.side = SIDE_RESISTANCE if z.center > price else SIDE_SUPPORT
    return zones, price


def _zone_dict(z: Zone, price: float, exchange: str | None, frame: pd.DataFrame | None = None) -> dict[str, Any]:
    """A zone on the exchange's tick grid: centre to the nearest tick, the band widened to whole ticks."""
    d = z.as_dict()
    if exchange:
        tick = tick_size(z.center, exchange)
        d["center"] = round_to_tick(z.center, exchange, up=(z.center / tick) % 1 >= 0.5)
        d["zone_low"] = round_to_tick(z.zone_low, exchange, up=False)
        d["zone_high"] = round_to_tick(z.zone_high, exchange, up=True)
        d["distance_from_price"] = round((d["center"] - price) / price * 100.0, 2)
    if frame is not None:
        # The chart says "range crossed this band", not the scoring rule's wider tolerance.
        touched = (frame["low"] <= d["zone_high"]) & (frame["high"] >= d["zone_low"])
        d["touch_count"] = int(touched.sum())
        d["last_tested"] = frame.index[touched][-1].date().isoformat() if touched.any() else None
    return d


def nearest_zones(frame: pd.DataFrame, min_distance_pct: float,
                  exchange: str | None = None) -> tuple[list[dict[str, Any]], dict[str, Any] | None]:
    """(nearest resistance and support at least `min_distance_pct` away, the zone the price is in)."""
    zones, price = _all_zones(frame)
    # Select using the same tick-rounded boundaries the viewer sees.
    for z in zones:
        shown = _zone_dict(z, price, exchange)
        z.center, z.zone_low, z.zone_high = shown["center"], shown["zone_low"], shown["zone_high"]
        z.distance_from_price = (z.center - price) / price * 100.0
        z.side = SIDE_RESISTANCE if z.center > price else SIDE_SUPPORT
    inside = min((z for z in zones if z.contains(price)), key=lambda z: abs(z.distance_from_price), default=None)
    out = []
    for side in (SIDE_RESISTANCE, SIDE_SUPPORT):
        far = [z for z in zones if z.side == side and abs(z.distance_from_price) >= min_distance_pct and z is not inside]
        if far:
            out.append(_zone_dict(min(far, key=lambda z: abs(z.distance_from_price)), price, exchange, frame))
    return out, (_zone_dict(inside, price, exchange, frame) if inside else None)


def chart_levels(frame: pd.DataFrame, thresholds: Mapping[str, Any], exchange: str | None = None) -> dict[str, Any]:
    """Nearest zones, both trendlines and the channel of the more recent one, for the dashboard.
    `exchange` puts the zones on that exchange's tick grid (None for an index: points)."""
    cfg = thresholds["chart_levels"]["trendline"]
    zcfg = thresholds["chart_levels"].get("zones") or {}
    zones, at_zone = nearest_zones(frame, float(zcfg.get("min_distance_pct", 0.0)), exchange)
    return {
        "zones": zones,
        "at_zone": at_zone,
        **trend_overlays(frame, cfg, "D", exchange, (thresholds["sepa_screen"] or {}).get("base")),
        "note": "Trendline, kênh và vùng hỗ trợ / kháng cự mô tả nơi giá từng đảo chiều, không phải dự báo. "
                "Phần tia kéo dài về tương lai là ước tính: bỏ các ngày nghỉ theo lịch giao dịch DNSE đã công bố, "
                "xa hơn lịch đó chỉ bỏ thứ 7, chủ nhật.",
    }
