"""Read-only regression audit of every exported ticker's daily/weekly/monthly overlays."""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def audit(site):
    errors, counts = [], {"tickers": 0, "charts": 0, "lines": 0, "zones": 0}
    def check(ok, message):
        if not ok:
            errors.append(message)
    meta = json.loads((site / "site.json").read_text(encoding="utf-8"))
    for ticker in meta["detail"]:
        counts["tickers"] += 1
        daily = json.loads((site / "data/ticker" / f"ticker-{ticker}.json").read_text(encoding="utf-8"))
        base = daily.get("base") or {}
        if base.get("pivot") and base.get("distance_to_pivot_pct") is not None:
            actual = (daily["bars"][-1]["close"] / base["pivot"] - 1) * 100
            check(abs(actual - base["distance_to_pivot_pct"]) <= .011, f"{ticker}: pivot distance")
        lv = daily.get("levels") or {}
        for z in lv.get("zones", []) + ([lv["at_zone"]] if lv.get("at_zone") else []):
            counts["zones"] += 1
            touched = [b for b in daily["bars"] if b["low"] <= z["zone_high"] and b["high"] >= z["zone_low"]]
            check(len(touched) == z["touch_count"], f"{ticker}: zone touch count")
            check((touched[-1]["trade_date"] if touched else None) == z["last_tested"], f"{ticker}: last tested")
        for tf in ("D", "W", "M"):
            d = daily if tf == "D" else json.loads((site / "data/bars" / f"tf-{tf}_ticker-{ticker}.json").read_text(encoding="utf-8"))
            counts["charts"] += 1
            lv = d.get("levels") or {}
            dates = [b["trade_date"] for b in d["bars"]] + lv.get("projection_dates", [])
            check(dates == sorted(set(dates)), f"{ticker}/{tf}: time grid")
            pos = {day: i for i, day in enumerate(dates)}
            for line in lv.get("trendlines", {}).values():
                counts["lines"] += 1
                a, b = line["start"], line["end"]
                if a[0] not in pos or b[0] not in pos:
                    errors.append(f"{ticker}/{tf}: missing endpoint")
                    continue
                value = lambda day: a[1] + (b[1] - a[1]) * (pos[day] - pos[a[0]]) / (pos[b[0]] - pos[a[0]])
                for day, price in line["anchors"]:
                    check(abs(value(day) - price) <= .03, f"{ticker}/{tf}: line misses anchor")
                if not line["broken_on"]:
                    check(abs(value(d["bars"][-1]["trade_date"]) - line["value_today"]) <= .03, f"{ticker}/{tf}: line today")
            ch = lv.get("channel")
            if ch:
                check(abs((ch["upper"][0][1] - ch["lower"][0][1]) - (ch["upper"][-1][1] - ch["lower"][-1][1])) <= .03,
                      f"{ticker}/{tf}: channel parallel")
                for u, m, l in zip(ch["upper"], ch["middle"], ch["lower"]):
                    check(abs(m[1] - (u[1] + l[1]) / 2) <= .011, f"{ticker}/{tf}: channel midpoint")
    return {"session": meta["session"], **counts, "errors": errors}


if __name__ == "__main__":
    result = audit(ROOT / "data/site")
    out = ROOT / "data/audit/published_overlays.json"
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(bool(result["errors"]))
