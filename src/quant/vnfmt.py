"""Numbers and dates written the Vietnamese way, for every text the code produces.

The dashboard formats its own numbers; these are for texts built in Python (event
texts, base checklists, market notes), which used English formats before 2026-10-11
("39,020", "1.62", "12.7%", "2026-10-09" -- tester report, B14).
"""

from __future__ import annotations

from typing import Any


def num(value: float, decimals: int = 0) -> str:
    """39.020 and 1,62: dots group thousands, a comma marks decimals."""
    return f"{value:,.{decimals}f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def pct(value: float, decimals: int = 1) -> str:
    return f"{num(value, decimals)}%"


def dmy(day: Any) -> str:
    """'2026-10-09' (or a date / timestamp) -> '09/10/2026'."""
    text = str(day)[:10]
    return f"{text[8:10]}/{text[5:7]}/{text[:4]}"
