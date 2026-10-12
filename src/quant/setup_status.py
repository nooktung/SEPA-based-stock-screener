"""Three questions and one status per focus-list stock.

A stock can be a fine leader and still have no entry. Keeping the questions
apart is what stops a watch list from reading as a buy list:

    Trend  -- is it in Stage 2?           (Trend Template: A = 8/8, B = near)
    Setup  -- is it building a base?      (VCP / flat base / cup / base)
    Entry  -- is price at the pivot now?  (below / approaching / in the pivot zone / extended)

The status combines them with what the stock did recently:

    AT_PIVOT     trend and setup in place, price from the pivot to +5% above it
    APPROACHING  trend and setup in place, price up to 5% under the pivot (no buy point yet)
    WATCH        something is still missing (usually the entry)
    BREAKOUT     a valid breakout in the last few sessions
    INVALIDATED  a failed breakout, a breached stop or a lost Trend Template since

and every label carries the market regime, because Minervini does not open
new positions in a correction however good the stock looks. None of these is a
trade instruction.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping

from src.quant.vnfmt import dmy as _dmy

AT_PIVOT, WATCH, BREAKOUT_STATUS, INVALIDATED = "AT_PIVOT", "WATCH", "BREAKOUT", "INVALIDATED"
APPROACHING = "APPROACHING"
STATUS_TEXT = {
    AT_PIVOT: "Ở vùng pivot",
    APPROACHING: "Sắp tới pivot",
    WATCH: "Đang theo dõi",
    BREAKOUT_STATUS: "Vừa breakout",
    INVALIDATED: "Mất hiệu lực",
}
FORMING_BASES = {"VCP", "FLAT_BASE", "CUP_HANDLE", "CUP", "BASE"}
INVALIDATING_EVENTS = {"BREAKOUT_FAILED", "STOP_BREACHED", "TREND_TEMPLATE_BROKEN"}
VALID_BREAKOUT_EVENTS = {"BREAKOUT"}
# An event that cancels an earlier one in the window: a regained Trend Template undoes its loss
# (tester N05: KSV read "lost the template 06/10" after regaining 8/8 on 07/10).
CANCELS = {"TREND_TEMPLATE_RESTORED": "TREND_TEMPLATE_BROKEN"}


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
    """'BELOW' (more than approaching_pct under the pivot), 'APPROACHING' (up to the pivot),
    'IN_ZONE' (the pivot to buy_zone_max_pct above it: SEPA's buy zone) or 'EXTENDED'.
    Under the pivot is not a buy point yet (tester report 3, R3-01)."""
    if distance_to_pivot_pct is None:
        return None
    if distance_to_pivot_pct > float(settings["base"]["buy_zone_max_pct"]):
        return "EXTENDED"
    if distance_to_pivot_pct < float(settings["entry"]["approaching_pct"]):
        return "BELOW"
    return "APPROACHING" if distance_to_pivot_pct < 0 else "IN_ZONE"


ENTRY_TEXT = {"BELOW": "Chưa tới pivot", "APPROACHING": "Sắp tới pivot — giá dưới pivot tối đa 5%, chưa có điểm mua",
              "IN_ZONE": "Ở vùng pivot — từ pivot tới +5%", "EXTENDED": "Đã chạy xa",
              "STOP_BREACHED": "Đã thủng stop — chờ nền mới",
              "NO_BASE": "Không áp dụng — chưa có nền đạt chuẩn, pivot chỉ để tham khảo", None: "Không xác định"}


def market_note(market: Mapping[str, Any] | None) -> tuple[str, str]:
    """(level, text) where level is 'ok' | 'caution' | 'stop' for the risk switch."""
    market = market or {}
    status = market.get("status")
    ftd = market.get("ftd") or {}
    if status == "CORRECTION":
        if ftd.get("date") and not ftd.get("failed") and not ftd.get("pressured"):
            return "caution", (f"Thị trường điều chỉnh nhưng đã có ngày follow-through {_dmy(ftd['date'])} — "
                               "xu hướng tăng mới đang được xác nhận, thận trọng")
        # The FTD that ended most recently (congthuc.md §11): undercut, or worn out by distribution.
        last = ftd.get("last_ftd") or {}
        if last.get("outcome") == "invalidated" or (ftd.get("date") and ftd.get("pressured")):
            day = last.get("date") or ftd.get("date")
            n = last.get("distribution_since", ftd.get("distribution_since"))
            return "stop", (f"Thị trường điều chỉnh — FTD {_dmy(day)} đã bị {n} ngày phân phối làm mất hiệu lực; "
                            "SEPA không mở vị thế mới")
        if last.get("outcome") == "failed":
            return "stop", (f"Thị trường điều chỉnh — FTD {_dmy(last['date'])} đã thất bại (thủng đáy nhịp hồi phục "
                            f"ngày {_dmy(last['on'])}); SEPA không mở vị thế mới")
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
    stop_breached: bool = False,
) -> dict[str, Any]:
    """Answer the three questions and pick a status.

    `recent_events` are (session date ISO, event type) within the status
    window. The most recent decisive event wins: a breakout followed by a
    failure is INVALIDATED, a failure followed by a fresh breakout is BREAKOUT.
    A regained Trend Template cancels its earlier loss. "Vừa breakout" is never
    shown beside a pivot above the price, and a close at or below the stop is
    never "at the pivot" (tester N05: VGC).
    """
    trend = {"A": "PASS", "B": "NEAR"}.get(group or "", "NO")
    setup = "PASS" if base_kind in FORMING_BASES else "NO"
    if stop_breached:
        entry = "STOP_BREACHED"
    elif setup != "PASS" and base_kind not in ("NEAR_HIGH", "EXTENDED"):
        entry = "NO_BASE"           # a too-deep or broken base's pivot is a reference only (PVT)
    else:
        entry = entry_zone(distance_to_pivot_pct, settings)

    decisive: list[tuple[str, str]] = []
    for d, e in sorted(recent_events, key=lambda x: x[0]):
        if e in CANCELS:
            decisive = [x for x in decisive if x[1] != CANCELS[e]]
        elif e in INVALIDATING_EVENTS or e in VALID_BREAKOUT_EVENTS:
            decisive.append((d, e))
    if decisive and decisive[-1][1] in INVALIDATING_EVENTS:
        status = INVALIDATED
    elif (decisive and decisive[-1][1] in VALID_BREAKOUT_EVENTS
          and (distance_to_pivot_pct is None or distance_to_pivot_pct >= 0)):
        status = BREAKOUT_STATUS
    elif trend in ("PASS", "NEAR") and setup == "PASS" and entry == "IN_ZONE":
        status = AT_PIVOT
    elif trend in ("PASS", "NEAR") and setup == "PASS" and entry == "APPROACHING":
        status = APPROACHING
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
