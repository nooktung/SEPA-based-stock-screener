"""Configuration loading and validation.

Config is validated at load time rather than at use time. A weight table that
does not sum to 100, or a recommendation band with a gap in it, is a silent
scoring bug that would otherwise surface as a plausible-looking but wrong
recommendation weeks later.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_ROOT / "config"


class ConfigError(ValueError):
    """Raised when configuration is internally inconsistent."""


def _load_yaml(name: str) -> dict[str, Any]:
    path = CONFIG_DIR / name
    if not path.exists():
        raise ConfigError(f"missing config file: {path}")
    with path.open("r", encoding="utf-8") as fh:
        data = yaml.safe_load(fh)
    if not isinstance(data, dict):
        raise ConfigError(f"{name} must parse to a mapping, got {type(data).__name__}")
    return data


# ---------------------------------------------------------------------------
# Watchlist
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class WatchlistEntry:
    ticker: str
    exchange: str
    name: str
    sector_profile: str
    active: bool


@dataclass(frozen=True)
class Watchlist:
    entries: tuple[WatchlistEntry, ...]
    benchmarks: tuple[str, ...]
    peer_groups: dict[str, tuple[str, ...]]
    macro_watch: dict[str, tuple[str, ...]]

    @property
    def active_tickers(self) -> tuple[str, ...]:
        return tuple(e.ticker for e in self.entries if e.active)

    def peers_of(self, ticker: str) -> tuple[str, ...]:
        peers: set[str] = set()
        for group in self.peer_groups.values():
            if ticker in group:
                peers.update(t for t in group if t != ticker)
        return tuple(sorted(peers))

    def profile_of(self, ticker: str) -> str:
        for e in self.entries:
            if e.ticker == ticker:
                return e.sector_profile
        raise KeyError(f"{ticker} not in watchlist")


def load_watchlist() -> Watchlist:
    raw = _load_yaml("watchlist.yaml")
    default_profile = raw.get("default_sector_profile", "generic")

    entries: list[WatchlistEntry] = []
    seen: set[str] = set()
    for group_name, items in raw.items():
        if group_name in {"version", "benchmarks", "peer_groups", "macro_watch", "default_sector_profile"}:
            continue
        if not isinstance(items, list):
            continue
        for item in items:
            ticker = str(item["ticker"]).upper()
            if ticker in seen:
                raise ConfigError(f"duplicate ticker in watchlist: {ticker}")
            seen.add(ticker)
            entries.append(
                WatchlistEntry(
                    ticker=ticker,
                    exchange=item.get("exchange", "HOSE"),
                    name=item.get("name", ticker),
                    sector_profile=item.get("sector_profile", default_profile),
                    active=bool(item.get("active", True)),
                )
            )

    if not entries:
        raise ConfigError("watchlist contains no tickers")

    benchmarks = tuple(b["symbol"] for b in raw.get("benchmarks", []))
    if not benchmarks:
        raise ConfigError("at least one benchmark is required for relative strength")

    peer_groups = {k: tuple(v) for k, v in (raw.get("peer_groups") or {}).items()}
    for gname, members in peer_groups.items():
        unknown = set(members) - seen
        if unknown:
            raise ConfigError(f"peer_group '{gname}' references tickers not in watchlist: {sorted(unknown)}")

    macro_watch = {k: tuple(v) for k, v in (raw.get("macro_watch") or {}).items()}

    return Watchlist(tuple(entries), benchmarks, peer_groups, macro_watch)


# ---------------------------------------------------------------------------
# Thresholds
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Thresholds:
    raw: dict[str, Any]

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self.raw.get(key, default)

    @property
    def scoring_weights(self) -> dict[str, int]:
        return dict(self.raw["scoring_weights"])

    @property
    def bands(self) -> list[dict[str, Any]]:
        return list(self.raw["recommendation_bands"])

    def label_for_score(self, score: float) -> str:
        for band in self.bands:
            if band["min"] <= score <= band["max"]:
                return band["label"]
        raise ConfigError(f"score {score} falls outside all recommendation bands")


def load_thresholds() -> Thresholds:
    raw = _load_yaml("thresholds.yaml")

    weights = raw.get("scoring_weights")
    if not weights:
        raise ConfigError("scoring_weights is required")
    total = sum(weights.values())
    if total != 100:
        raise ConfigError(f"scoring_weights must sum to 100, got {total}: {weights}")

    bands = raw.get("recommendation_bands") or []
    if not bands:
        raise ConfigError("recommendation_bands is required")
    ordered = sorted(bands, key=lambda b: b["min"])
    if ordered[0]["min"] != 0 or ordered[-1]["max"] != 100:
        raise ConfigError("recommendation_bands must cover 0..100 inclusive")
    for lower, upper in zip(ordered, ordered[1:]):
        if upper["min"] != lower["max"] + 1:
            raise ConfigError(
                f"gap or overlap between bands {lower['label']} (max {lower['max']}) "
                f"and {upper['label']} (min {upper['min']})"
            )

    conf_weights = (raw.get("confidence") or {}).get("weights", {})
    if conf_weights and sum(conf_weights.values()) != 100:
        raise ConfigError(f"confidence.weights must sum to 100, got {sum(conf_weights.values())}")

    return Thresholds(raw)


# ---------------------------------------------------------------------------
# Sources
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceSpec:
    id: str
    kind: str
    provider_class: str
    source_level: int
    enabled: bool
    priority: int
    config: dict[str, Any]

    @property
    def auth_env(self) -> str | None:
        return self.config.get("auth_env")

    @property
    def has_credentials(self) -> bool:
        env = self.auth_env
        return True if env is None else bool(os.environ.get(env))


@dataclass(frozen=True)
class SourceRegistry:
    specs: tuple[SourceSpec, ...]
    failure_policy: dict[str, Any]

    def for_kind(self, kind: str, enabled_only: bool = True) -> tuple[SourceSpec, ...]:
        items = [s for s in self.specs if s.kind == kind and (s.enabled or not enabled_only)]
        return tuple(sorted(items, key=lambda s: s.priority))

    def authoritative_news_sources(self) -> tuple[SourceSpec, ...]:
        return tuple(s for s in self.for_kind("news") if s.config.get("event_authority"))


VALID_KINDS = {"market_data", "news", "financial", "macro", "flow", "crosscheck"}
# `crosscheck` sources (SSI FastConnect Data) only verify the primary sources;
# the pipeline runs without them, and nothing picks them as a fallback.
REQUIRED_KINDS = VALID_KINDS - {"crosscheck"}


def load_sources() -> SourceRegistry:
    raw = _load_yaml("sources.yaml")
    specs: list[SourceSpec] = []
    seen: set[tuple[str, str]] = set()

    for kind, items in raw.items():
        if kind in {"version", "failure_policy"}:
            continue
        if kind not in VALID_KINDS:
            raise ConfigError(f"unknown source kind '{kind}'; expected one of {sorted(VALID_KINDS)}")
        for item in items or []:
            level = int(item["source_level"])
            if not 1 <= level <= 5:
                raise ConfigError(f"source '{item['id']}' has source_level {level}, must be 1..5")
            key = (kind, item["id"])
            if key in seen:
                raise ConfigError(f"duplicate source id '{item['id']}' in kind '{kind}'")
            seen.add(key)
            specs.append(
                SourceSpec(
                    id=item["id"],
                    kind=kind,
                    provider_class=item["provider_class"],
                    source_level=level,
                    enabled=bool(item.get("enabled", False)),
                    priority=int(item.get("priority", 50)),
                    config=item,
                )
            )

    registry = SourceRegistry(tuple(specs), raw.get("failure_policy") or {})

    for kind in REQUIRED_KINDS:
        if not registry.for_kind(kind):
            raise ConfigError(f"no enabled source for kind '{kind}' -- the pipeline would have no input")

    return registry


# ---------------------------------------------------------------------------
# Universe
# ---------------------------------------------------------------------------

VALID_EXCHANGES = {"HOSE", "HNX", "UPCOM"}


@dataclass(frozen=True)
class UniverseConfig:
    markets: dict[str, str]
    security_groups: tuple[str, ...]
    symbol_length: int
    allow_digits: bool


def load_universe() -> UniverseConfig:
    raw = _load_yaml("universe.yaml")

    markets = {str(k): str(v).upper() for k, v in (raw.get("markets") or {}).items()}
    if not markets:
        raise ConfigError("universe.markets is required")
    unknown = set(markets.values()) - VALID_EXCHANGES
    if unknown:
        raise ConfigError(f"universe.markets maps to unknown exchanges {sorted(unknown)}")

    groups = tuple(str(g) for g in raw.get("security_groups") or ())
    if not groups:
        raise ConfigError("universe.security_groups is required")

    length = int(raw.get("symbol_length", 0))
    if length < 1:
        raise ConfigError(f"universe.symbol_length must be positive, got {length}")

    return UniverseConfig(markets, groups, length, bool(raw.get("allow_digits", True)))


# ---------------------------------------------------------------------------
# Aggregate
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AppConfig:
    watchlist: Watchlist
    thresholds: Thresholds
    sources: SourceRegistry


@lru_cache(maxsize=1)
def load_config() -> AppConfig:
    return AppConfig(
        watchlist=load_watchlist(),
        thresholds=load_thresholds(),
        sources=load_sources(),
    )
