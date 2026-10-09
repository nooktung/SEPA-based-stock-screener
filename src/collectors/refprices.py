"""Reference / ceiling / floor price of each session, from DNSE's security definition.

Price boards show a session's change against its reference price ("giá tham
chiếu"), not against the previous close. On HOSE and HNX the two are the same
except on ex-rights / ex-dividend days; on UPCoM the reference is the previous
session's average matched price, so they differ almost every day (PHP on
05/10/2026: +6.00% close to close, +4.65% on the boards).

DNSE serves only the *current* definition (`/price/{symbol}/secdef`, read-only).
It publishes the next session's values in the evening (`time` in the payload,
Vietnam time -- 20:46 on 05/10/2026), so a read between that and the next
evening holds that session's values. Each read is stored under the session it
applies to:

    published on a trading day before `next_session_after` -> that day's session
    otherwise                                               -> the next trading day

Trading days come from DNSE's calendar (src/market_calendar.is_trading_day),
weekdays where it has no answer.

A holiday after the publication is resolved when the value is used: the
reference of session T is the one labelled after the previous session and no
later than T.

A session whose definition was never read (DNSE had already moved on) is
filled, on UPCoM only, by the exchange's own rule: the reference is the
average matched price of the ticker's previous trading session, rounded to
the 100-dong tick (DNSE's `avgPrice` in `/price/{symbol}/trades`); ceiling /
floor are the band around it. Checked against DNSE's published values on every
run (13/13 UPCoM tickers on 2026-10-06; PHP 02/10 average 47,275 -> 47,300, the
board's reference for 05/10); when the check fails the fill is not used. On
HOSE / HNX the reference is the previous close except on ex-dates, which no
rule can tell from prices, so nothing is filled there. A session with neither
has no reference -- the dashboard then says its change is against the
previous close. Settings: `reference_prices` in config/thresholds.yaml.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from datetime import date, datetime, time as dtime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from src.database import connect
from src.market_calendar import is_trading_day, next_trading_day

DNSE_TIME_FORMATS = ("%Y-%m-%d %H:%M:%S.%f", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S.%f", "%Y-%m-%dT%H:%M:%S")


@dataclass
class RefReport:
    stored: dict[str, date] = field(default_factory=dict)          # ticker -> session the values apply to
    failed: dict[str, str] = field(default_factory=dict)           # ticker -> why nothing was stored

    def sessions(self) -> dict[date, int]:
        out: dict[date, int] = {}
        for d in self.stored.values():
            out[d] = out.get(d, 0) + 1
        return dict(sorted(out.items()))


def parse_published(text: Any) -> datetime | None:
    """DNSE's `time` ("2026-10-05 20:46:29.147", Vietnam time, no zone) -> naive datetime, or None."""
    if not text:
        return None
    raw = str(text).strip().replace("Z", "")
    for fmt in DNSE_TIME_FORMATS:
        try:
            return datetime.strptime(raw, fmt)
        except ValueError:
            continue
    return None


def _cutoff(cfg: Mapping[str, Any]) -> dtime:
    h, m = (int(x) for x in str(cfg["next_session_after"]).split(":"))
    return dtime(h, m)


def session_for(published: datetime, cfg: Mapping[str, Any]) -> date:
    """The session a definition published at `published` (Vietnam time) applies to."""
    day = published.date()
    if is_trading_day(day) and published.time() < _cutoff(cfg):
        return day
    return next_trading_day(day)


def valid(values: Mapping[str, Any]) -> str | None:
    """Why these limits cannot be a session's, or None when they can."""
    ref, ceil, floor = values.get("reference"), values.get("ceiling"), values.get("floor")
    if ref is None or ref <= 0:
        return "không có giá tham chiếu"
    if ceil is None or floor is None or not floor <= ref <= ceil or floor >= ceil:
        return f"trần / sàn không bao quanh tham chiếu ({floor} / {ref} / {ceil})"
    return None


def collect_reference_prices(tickers: Sequence[str], fetch: Callable[[str], Mapping[str, Any]],
                             cfg: Mapping[str, Any], source_id: str, path: Path | None = None,
                             now: datetime | None = None) -> RefReport:
    """Read and store the current definition of each ticker; one request per ticker."""
    report = RefReport()
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")
    rows = []
    for t in dict.fromkeys(x.upper() for x in tickers):
        try:
            values = fetch(t)
        except Exception as exc:  # noqa: BLE001 -- one ticker failing never stops the rest
            report.failed[t] = f"{type(exc).__name__}: {exc}"
            continue
        published = parse_published(values.get("published_at"))
        if published is None:
            report.failed[t] = "DNSE không ghi thời điểm công bố -- không biết thuộc phiên nào"
            continue
        why = valid(values)
        if why:
            report.failed[t] = why
            continue
        session = session_for(published, cfg)
        report.stored[t] = session
        rows.append((t, session.isoformat(), float(values["reference"]), float(values["ceiling"]),
                     float(values["floor"]), published.isoformat(sep=" "), source_id, observed))
    if rows:
        with connect(path) as conn:
            # The vendor may republish a session's definition (a correction): the latest read wins.
            conn.executemany(
                "INSERT INTO reference_prices (ticker, session_date, reference, ceiling, floor, published_at, "
                "source_id, observed_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) ON CONFLICT(ticker, session_date) DO UPDATE SET "
                "reference = excluded.reference, ceiling = excluded.ceiling, floor = excluded.floor, "
                "published_at = excluded.published_at, source_id = excluded.source_id, observed_at = excluded.observed_at, "
                "method = 'secdef'",
                rows)
    return report


# ---------------------------------------------------------------- UPCoM: the prior session's average
UPCOM_TICK = 100.0          # VND, every price (src/quant/screener.tick_size)


def upcom_limits(average_vnd: float, band_pct: float) -> dict[str, float]:
    """Reference (average rounded half-up to the tick), ceiling (band, rounded down) and floor (rounded up)."""
    ref = math.floor(average_vnd / UPCOM_TICK + 0.5) * UPCOM_TICK
    b = band_pct / 100.0
    return {"reference": ref, "ceiling": math.floor(ref * (1 + b) / UPCOM_TICK + 1e-9) * UPCOM_TICK,
            "floor": math.ceil(ref * (1 - b) / UPCOM_TICK - 1e-9) * UPCOM_TICK}


def _previous_bar(conn: Any, ticker: str, session: str) -> str | None:
    row = conn.execute("SELECT MAX(trade_date) FROM prices_daily WHERE ticker = ? AND trade_date < ?",
                       (ticker, session)).fetchone()
    return row[0] if row and row[0] else None


@dataclass
class AverageRuleCheck:
    checked: dict[str, tuple[float, float]] = field(default_factory=dict)   # ticker -> (from average, published)
    mismatched: set[str] = field(default_factory=set)

    @property
    def share(self) -> float | None:
        return (len(self.checked) - len(self.mismatched)) / len(self.checked) if self.checked else None


def check_average_rule(tickers: Sequence[str], fetch_avg: Callable[[str, date], float | None], band_pct: float,
                       path: Path | None = None) -> AverageRuleCheck:
    """For each UPCoM ticker, the newest published reference against the rule applied to the
    average of the ticker's trading session before it. One request per ticker."""
    out = AverageRuleCheck()
    with connect(path) as conn:
        adjust = _adjustments(conn)
        for t in tickers:
            row = conn.execute("SELECT session_date, reference FROM reference_prices WHERE ticker = ? AND method = 'secdef' "
                               "ORDER BY session_date DESC LIMIT 1", (t,)).fetchone()
            if not row:
                continue
            prev = _previous_bar(conn, t, row[0])
            if not prev:
                continue
            try:
                avg = fetch_avg(t, date.fromisoformat(prev))
            except Exception:  # noqa: BLE001 -- an unreadable tape is no evidence either way
                continue
            if avg is None:
                continue
            # an ex-date's published reference is on the new scale while the tape averaged is not
            m = scale_for(adjust, t, prev) / scale_for(adjust, t, row[0])
            derived = upcom_limits(avg * m, band_pct)["reference"]
            out.checked[t] = (derived, float(row[1]))
            if abs(derived - float(row[1])) > 0.5:
                out.mismatched.add(t)
    return out


def fill_from_averages(targets: Iterable[tuple[str, str]], fetch_avg: Callable[[str, date], float | None],
                       cfg: Mapping[str, Any], band_pct: float, source_id: str, path: Path | None = None,
                       now: datetime | None = None) -> RefReport:
    """Store an average-derived reference for each (UPCoM ticker, session) that has none.

    The session averaged is the ticker's previous trading session; a gap of more
    than `fill_max_gap_days` calendar days (a halt: the exchange then sets the
    reference differently) or a session without trades leaves the session empty.
    """
    report = RefReport()
    observed = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat(timespec="seconds")
    max_gap = int(cfg["fill_max_gap_days"])
    rows = []
    for t, session in targets:
        if lookup([(t, session)], path):
            continue                                   # published (or already filled): kept
        with connect(path) as conn:
            prev = _previous_bar(conn, t, session)
        if not prev or (date.fromisoformat(session) - date.fromisoformat(prev)).days > max_gap:
            report.failed[t] = f"phiên {session}: không có phiên giao dịch liền trước để lấy giá bình quân"
            continue
        try:
            avg = fetch_avg(t, date.fromisoformat(prev))
        except Exception as exc:  # noqa: BLE001
            report.failed[t] = f"phiên {session}: {type(exc).__name__}: {exc}"
            continue
        if avg is None:
            report.failed[t] = f"phiên {prev} không có lệnh khớp"
            continue
        with connect(path) as conn:
            adjust = _adjustments(conn)
        m = scale_for(adjust, t, prev) / scale_for(adjust, t, session)
        if m != 1.0:
            # `session` is the ex-date: the exchange set its reference from the average on the new scale
            lim = upcom_limits(avg * m, band_pct)
            rows.append((t, session, lim["reference"], lim["ceiling"], lim["floor"], prev, source_id, observed,
                         "avg_price_adj"))
        else:
            lim = upcom_limits(avg, band_pct)
            rows.append((t, session, lim["reference"], lim["ceiling"], lim["floor"], prev, source_id, observed, "avg_price"))
        report.stored[t] = date.fromisoformat(session)
    if rows:
        with connect(path) as conn:
            conn.executemany(
                "INSERT OR IGNORE INTO reference_prices (ticker, session_date, reference, ceiling, floor, published_at, "
                "source_id, observed_at, method) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)", rows)
    return report


def fill_targets(tickers: Sequence[str], sessions: int, latest_only: bool = False,
                 path: Path | None = None) -> list[tuple[str, str]]:
    """(UPCoM ticker, session) pairs among each ticker's last `sessions` bars (or its bar on the
    latest stored session) that have no reference yet."""
    out: list[tuple[str, str]] = []
    with connect(path) as conn:
        latest = conn.execute("SELECT MAX(trade_date) FROM prices_daily").fetchone()[0]
        exchanges = {r[0]: str(r[1]).upper() for r in conn.execute("SELECT ticker, exchange FROM tickers")}
        days_of: dict[str, list[str]] = {}
        for t in dict.fromkeys(tickers):
            if exchanges.get(t) != "UPCOM":
                continue
            if latest_only:
                days_of[t] = [r[0] for r in conn.execute(
                    "SELECT trade_date FROM prices_daily WHERE ticker = ? AND trade_date = ?", (t, latest))]
            else:
                days_of[t] = [r[0] for r in conn.execute(
                    "SELECT trade_date FROM prices_daily WHERE ticker = ? ORDER BY trade_date DESC LIMIT ?", (t, sessions))]
    have = lookup([(t, d) for t, days in days_of.items() for d in days], path)
    for t, days in days_of.items():
        out += [(t, d) for d in sorted(days) if (t, d) not in have]
    return out


def record_adjustment(ticker: str, factor: float, through: date, source_id: str, path: Path | None = None) -> None:
    """Note that the stored bars of `ticker` up to `through` were rescaled by `factor` (new / old)."""
    with connect(path) as conn:
        conn.execute("INSERT INTO price_adjustments (ticker, through_session, factor, source_id, observed_at) "
                     "VALUES (?, ?, ?, ?, ?) ON CONFLICT(ticker, through_session) DO UPDATE SET factor = excluded.factor, "
                     "observed_at = excluded.observed_at",
                     (ticker.upper(), through.isoformat(), float(factor), source_id,
                      datetime.now(timezone.utc).isoformat(timespec="seconds")))


def _adjustments(conn: Any) -> dict[str, list[tuple[str, float]]]:
    out: dict[str, list[tuple[str, float]]] = {}
    for t, through, f in conn.execute("SELECT ticker, through_session, factor FROM price_adjustments"):
        out.setdefault(t, []).append((through, float(f)))
    return out


def scale_for(adjustments: Mapping[str, list[tuple[str, float]]], ticker: str, session: str) -> float:
    """Factor that puts an exchange price of `session` (on that day's scale) onto the stored bars' scale."""
    m = 1.0
    for through, f in adjustments.get(ticker, []):
        if session <= through:
            m *= f
    return m


def plausible(close: float | None, reference: float | None, exchange: str | None) -> bool:
    """A session cannot close beyond its band from its own reference (+1 point of slack); a
    bigger gap means the two are on different price scales (an ex-date adjustment)."""
    from src.market_calendar import price_limit_pct  # noqa: PLC0415

    if not close or not reference or not exchange:
        return bool(close and reference)
    band = price_limit_pct(exchange)
    return band is None or abs(close / reference - 1) * 100.0 <= float(band) + 1.0


def lookup(pairs: Iterable[tuple[str, str]], path: Path | None = None) -> dict[tuple[str, str], dict[str, Any]]:
    """(ticker, session ISO date) -> {reference, ceiling, floor, session_date} for the pairs that have one.

    The value labelled for a session is used; a value labelled for a day with no
    session (published before a holiday) serves the first session after it.
    """
    wanted = {(t.upper(), d) for t, d in pairs if t and d}
    if not wanted:
        return {}
    earliest = min(d for _, d in wanted)
    since = (date.fromisoformat(earliest) - timedelta(days=30)).isoformat()
    with connect(path) as conn:
        adjust = _adjustments(conn)
        sessions = sorted(r[0] for r in conn.execute(
            "SELECT DISTINCT trade_date FROM index_daily WHERE trade_date >= ?", (since,)))
        stored: dict[str, list[dict[str, Any]]] = {}
        for r in conn.execute("SELECT ticker, session_date, reference, ceiling, floor, method, published_at "
                              "FROM reference_prices WHERE session_date > ? ORDER BY session_date", (since,)):
            stored.setdefault(r[0], []).append({"session_date": r[1], "reference": r[2], "ceiling": r[3], "floor": r[4],
                                                "method": r[5], "averaged": r[6] if r[5] != "secdef" else None})
    out: dict[tuple[str, str], dict[str, Any]] = {}
    for t, d in wanted:
        before = [s for s in sessions if s < d]
        after = before[-1] if before else (date.fromisoformat(d) - timedelta(days=10)).isoformat()
        hits = [r for r in stored.get(t, []) if after < r["session_date"] <= d]
        if hits:
            # a published value beats one derived from the average, then the latest label wins
            row = dict(max(hits, key=lambda r: (r.get("method") == "secdef", r["session_date"])))
            # A published definition is on the scale of its own session; a value from the tape's
            # average, on the scale of the session averaged (unless already put on the new one).
            basis = row["averaged"] if row.get("method") == "avg_price" else row["session_date"]
            m = 1.0 if row.get("method") == "avg_price_adj" else scale_for(adjust, t, basis)
            if m != 1.0:
                row.update(published_reference=row["reference"], reference=row["reference"] * m,
                           ceiling=row["ceiling"] * m, floor=row["floor"] * m, scaled=m)
            out[(t, d)] = row
    return out


def board_pct(close: float, ref: Mapping[str, Any], exchange: str | None) -> float:
    """% against the reference as the board printed it. For a session on the old scale the vendor's
    rescaled close is put back on that day's scale and onto the exchange's tick (DNSE rounds rescaled
    prices to 10 dong: TV1 06/10/2026 20,600 x 0.92786 = 19,114 stored as 19,110), then compared
    with the reference as published."""
    m = ref.get("scaled")
    if m and ref.get("published_reference") and exchange:
        from src.quant.screener import tick_size  # noqa: PLC0415

        raw = close / m
        tick = tick_size(raw, exchange)
        return round((math.floor(raw / tick + 0.5) * tick / ref["published_reference"] - 1) * 100, 2)
    return round((close / ref["reference"] - 1) * 100, 2)


def change(close: float | None, prev_close: float | None, ref: Mapping[str, Any] | None,
           exchange: str | None = None) -> dict[str, Any]:
    """A session's % change as the boards show it (against the reference), or against the previous
    close when no reference is stored (or it is on another price scale) -- with the basis named."""
    if ref and close is not None and plausible(close, ref["reference"], exchange):
        return {"pct": board_pct(close, ref, exchange), "basis": "ref", "reference": ref["reference"],
                "ceiling": ref["ceiling"], "floor": ref["floor"], "prev_close": prev_close,
                "method": ref.get("method", "secdef"), "averaged": ref.get("averaged")}
    pct = round((close / prev_close - 1) * 100, 2) if close is not None and prev_close else None
    return {"pct": pct, "basis": "close", "reference": None, "prev_close": prev_close}
