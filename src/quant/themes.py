"""Theme strength ("sóng ngành"): how the user's curated stock groups are doing.

A theme is a ticker list in config/themes.yaml (oil & gas, rubber, ports...).
For each theme, over its members that clear the liquidity floor:

    rs_percentile   the mean raw RS score of those members, ranked as a
                    percentile against every industry (at the configured level)
                    and every theme -- the same yardstick as industry RS
    return_1m / 3m  median return over 21 / 63 sessions
    above_ma50_pct  share of members closing above their MA50
    leaders         members in groups A/B of this screen

A theme is marked "hot" when its RS percentile reaches
`sepa_screen.themes.hot_percentile`. Hot themes change nothing about A/B:
their uptrending members are only placed first in the extended watch list.
Deterministic; describes, does not forecast. [TRIỂN KHAI]
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping

import pandas as pd
import yaml

from src.config import CONFIG_DIR
from src.quant import indicators as ind


def load_themes(path: Path | None = None) -> dict[str, list[str]]:
    p = path or CONFIG_DIR / "themes.yaml"
    if not p.exists():
        return {}
    raw = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    return {str(name): [str(t).upper() for t in (tickers or [])] for name, tickers in (raw.get("themes") or {}).items()}


@dataclass
class ThemeStat:
    name: str
    tickers: list[str]                                   # every configured member
    members: list[str] = field(default_factory=list)     # members that cleared the liquidity floor
    mean_rs_raw: float | None = None
    rs_percentile: float | None = None
    return_1m: float | None = None
    return_3m: float | None = None
    above_ma50_pct: float | None = None
    leaders: list[str] = field(default_factory=list)
    hot: bool = False

    def as_dict(self) -> dict[str, Any]:
        r = lambda v: None if v is None else round(v, 1)  # noqa: E731
        return {"name": self.name, "count": len(self.members), "configured": len(self.tickers),
                "members": list(self.members), "rs_percentile": self.rs_percentile,
                "return_1m": r(self.return_1m), "return_3m": r(self.return_3m),
                "above_ma50_pct": r(self.above_ma50_pct), "leaders": list(self.leaders), "hot": self.hot}


def _ret(close: pd.Series, n: int) -> float | None:
    return float((close.iloc[-1] / close.iloc[-1 - n] - 1) * 100.0) if len(close) > n else None


def theme_strength(themes: Mapping[str, list[str]], frames: Mapping[str, pd.DataFrame],
                   raw_rs: Mapping[str, float], groups: Mapping[str, str | None],
                   industry_means: Mapping[str, float], settings: Mapping[str, Any]) -> dict[str, ThemeStat]:
    """Stats per theme. `industry_means` are the industries' mean raw RS, the ranking pool."""
    cfg = settings["themes"]
    out: dict[str, ThemeStat] = {}
    pool: dict[str, float] = {f"industry:{k}": v for k, v in industry_means.items() if v is not None}
    for name, tickers in themes.items():
        st = ThemeStat(name, list(tickers))
        st.members = [t for t in tickers if t in frames]
        scores = [raw_rs[t] for t in st.members if t in raw_rs]
        closes = [frames[t]["close"].astype("float64") for t in st.members]
        r1 = [v for c in closes if (v := _ret(c, 21)) is not None]
        r3 = [v for c in closes if (v := _ret(c, 63)) is not None]
        above = [bool(c.iloc[-1] > v) for c in closes if len(c) >= 50 and (v := ind.sma(c, 50).iloc[-1]) == v]
        st.return_1m = float(pd.Series(r1).median()) if r1 else None
        st.return_3m = float(pd.Series(r3).median()) if r3 else None
        st.above_ma50_pct = 100.0 * sum(above) / len(above) if above else None
        st.leaders = [t for t in st.members if groups.get(t) in ("A", "B")]
        if len(scores) >= int(cfg["min_members"]):
            st.mean_rs_raw = sum(scores) / len(scores)
            pool[f"theme:{name}"] = st.mean_rs_raw
        out[name] = st
    if pool:
        pct = pd.Series(pool, dtype="float64").rank(pct=True, method="average") * 100.0
        for key, v in pct.items():
            if key.startswith("theme:"):
                st = out[key[len("theme:"):]]
                st.rs_percentile = float(min(99.0, max(1.0, round(v))))
                st.hot = st.rs_percentile >= float(cfg["hot_percentile"])
    return out


def themes_of(ticker: str, stats: Mapping[str, ThemeStat]) -> list[dict[str, Any]]:
    """The themes a ticker belongs to, hottest first, for the row badge."""
    mine = [s for s in stats.values() if ticker in s.tickers]
    mine.sort(key=lambda s: -(s.rs_percentile or 0))
    return [{"name": s.name, "rs_percentile": s.rs_percentile, "hot": s.hot} for s in mine]
