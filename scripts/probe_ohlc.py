"""Probe the live shape of GET /price/ohlc.

Run once on a machine with network access to openapi.dnse.com.vn:
    python scripts/probe_ohlc.py

Reads credentials from the environment. Prints nothing sensitive.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import date, datetime, time, timezone


def epoch(d: date, end: bool = False) -> int:
    t = time(23, 59, 59) if end else time(0, 0, 0)
    return int(datetime.combine(d, t, tzinfo=timezone.utc).timestamp())


def main() -> int:
    key, secret = os.environ.get("DNSE_API_KEY"), os.environ.get("DNSE_API_SECRET")
    if not key or not secret:
        print("Set DNSE_API_KEY and DNSE_API_SECRET first.", file=sys.stderr)
        return 1

    from dnse import DNSEClient

    client = DNSEClient(
        api_key=key, api_secret=secret,
        base_url="https://openapi.dnse.com.vn", api_version="2026-05-07",
    )

    # Windows spanning both August 2026 ex-dates.
    cases = [
        ("SSI", date(2026, 8, 10), date(2026, 8, 24), "5:1 bonus, ex 17/08, ~-16.7%"),
        ("VIX", date(2026, 8, 14), date(2026, 8, 27), "20:1 dividend, ex 21/08, ~-4.8%"),
    ]

    for symbol, start, end, note in cases:
        print(f"\n=== {symbol}  ({note})")
        status, body = client.get_ohlc(
            "1D", query={"symbol": symbol, "from": epoch(start), "to": epoch(end, True)}
        )
        print("status:", status)
        print("type  :", type(body).__name__)
        if isinstance(body, dict):
            print("keys  :", list(body.keys()))
        print(json.dumps(body, indent=2, ensure_ascii=False)[:1500])

    print("\nCheck three things: payload shape, price magnitude "
          "(21.4 vs 21400), and whether the ex-date step is present.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
