"""Three questions and one status per focus-list stock.

A stock can be a fine leader and still have no entry. Keeping the questions
apart is what stops a watch list from reading as a buy list:

    Trend  -- is it in Stage 2?           (Trend Template: A = 8/8, B = near)
    Setup  -- is it building a base?      (VCP / flat base / cup / base)
    Entry  -- is price at the pivot now?  (below / in the buy zone / extended)

The status combines them with what the stock did recently:

    AT_PIVOT     trend and setup in place, price in the pivot zone
    WATCH        something is still missing (usually the entry)
    BREAKOUT     a valid breakout in the last few sessions
    INVALIDATED  a failed breakout, a breached stop or a lost Trend Template since

and every label carries the market regime, because Minervini does not open
new positions in a correction however good the stock looks. None of these is a
trade instruction.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

AT_PIVOT, WATCH, BREAKOUT_STATUS, INVALIDATED = "AT_PIVOT", "WATCH", "BREAKOUT", "INVALIDATED"
STATUS_TEXT = {
    AT_PIVOT: "Ở vùng pivot",
    WATCH: "Đang theo dõi",
    BREAKOUT_STATUS: "Vừa breakout",
    INVALIDATED: "Mất hiệu lực",
}
FORMING_BASES = {"VCP", "FLAT_BASE", "CUP_HANDLE", "CUP", "BASE"}
INVALIDATING_EVENTS = {"BREAKOUT_FAILED", "STOP_BREACHED", "TREND_TEMPLATE_BROKEN"}
VALID_BREAKOUT_EVENTS = {"BREAKOUT"}


def rs_tier(rs: float | None, settings: Mapping[str, Any]) -> str | None:
    """'STRONG_LEADER' (90+), 'LEADER' (80-89), 'QUALIFIED' (70-79) or None."""
    if rs is None:
        return None
    t = settings["rs_tiers"]
    if rs >= float(t["strong_leader"]):
        return "STRONG_LEADER"
    if rs >= float(t["leader"]):
        return "LEADER"
    if rs >= float(t["qualified"]):
        return "QUALIFIED"
    return None


RS_TIER_TEXT = {"STRONG_LEADER": "Dẫn dắt mạnh", "LEADER": "Dẫn dắt", "QUALIFIED": "Đạt"}
RS_TIER_ORDER = {"STRONG_LEADER": 0, "LEADER": 1, "QUALIFIED": 2, None: 3}


def entry_zone(distance_to_pivot_pct: float | None, settings: Mapping[str, Any]) -> str | None:
    """'BELOW' the pivot zone, 'IN_ZONE' (pivot -x% .. buy zone), or 'EXTENDED'."""
    if distance_to_pivot_pct is None:
        return None
    if distance_to_pivot_pct > float(settings["base"]["buy_zone_max_pct"]):
        return "EXTENDED"
    if distance_to_pivot_pct < float(settings["entry"]["approaching_pct"]):
        return "BELOW"
    return "IN_ZONE"


ENTRY_TEXT = {"BELOW": "Chưa tới pivot", "IN_ZONE": "Ở vùng pivot", "EXTENDED": "Đã chạy xa", None: "Không xác định"}


def market_note(market: Mapping[str, Any] | None) -> tuple[str, str]:
    """(level, text) where level is 'ok' | 'caution' | 'stop' for the risk switch."""
    market = market or {}
    status = market.get("status")
    ftd = market.get("ftd") or {}
    if status == "CORRECTION":
        if ftd.get("date") and not ftd.get("failed") and not ftd.get("pressured"):
            return "caution", (f"Thị trường điều chỉnh nhưng đã có ngày follow-through {ftd['date']} — "
                               "xu hướng tăng mới đang được xác nhận, thận trọng")
        # The FTD that ended most recently (congthuc.md §11): undercut, or worn out by distribution.
        last = ftd.get("last_ftd") or {}
        if last.get("outcome") == "invalidated" or (ftd.get("date") and ftd.get("pressured")):
            day = last.get("date") or ftd.get("date")
            n = last.get("distribution_since", ftd.get("distribution_since"))
            return "stop", (f"Thị trường điều chỉnh — FTD {day} đã bị {n} ngày phân phối làm mất hiệu lực; "
                            "SEPA không mở vị thế mới")
        if last.get("outcome") == "failed":
            return "stop", (f"Thị trường điều chỉnh — FTD {last['date']} đã thất bại (thủng đáy nhịp hồi phục "
                            f"ngày {last['on']}); SEPA không mở vị thế mới")
        return "stop", "Thị trường điều chỉnh — SEPA không mở vị thế mới"
    if status == "UNDER_PRESSURE":
        return "caution", "Thị trường tăng nhưng chịu áp lực — giảm số lượng và quy mô"
    if status == "UPTREND":
        return "ok", "Thị trường xu hướng tăng"
    if status == "MIXED":
        return "caution", "Thị trường trung tính — chủ yếu theo dõi"
    return "caution", "Chưa rõ trạng thái thị trường"


def assess(
    group: str | None,
    base_kind: str | None,
    distance_to_pivot_pct: float | None,
    settings: Mapping[str, Any],
    market: Mapping[str, Any] | None = None,
    recent_events: Iterable[tuple[str, str]] = (),
) -> dict[str, Any]:
    """Answer the three questions and pick a status.

    `recent_events` are (session date ISO, event type) within the status
    window. The most recent decisive event wins: a breakout followed by a
    failure is INVALIDATED, a failure followed by a fresh breakout is BREAKOUT.
    """
    trend = {"A": "PASS", "B": "NEAR"}.get(group or "", "NO")
    setup = "PASS" if base_kind in FORMING_BASES else "NO"
    entry = entry_zone(distance_to_pivot_pct, settings)

    decisive = sorted(
        ((d, e) for d, e in recent_events if e in INVALIDATING_EVENTS or e in VALID_BREAKOUT_EVENTS),
        key=lambda x: x[0],
    )
    if decisive and decisive[-1][1] in INVALIDATING_EVENTS:
        status = INVALIDATED
    elif decisive and decisive[-1][1] in VALID_BREAKOUT_EVENTS:
        status = BREAKOUT_STATUS
    elif trend in ("PASS", "NEAR") and setup == "PASS" and entry == "IN_ZONE":
        status = AT_PIVOT
    else:
        status = WATCH

    level, note = market_note(market)
    return {
        "trend": trend,
        "setup": setup,
        "entry": entry,
        "entry_text": ENTRY_TEXT[entry],
        "status": status,
        "status_text": STATUS_TEXT[status],
        "market_level": level,
        "market_note": note,
        "decided_by": decisive[-1] if decisive else None,
    }
