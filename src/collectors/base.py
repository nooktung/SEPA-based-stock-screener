"""Provider interfaces.

Every data source in the system implements one of the ABCs below. The engine
depends only on these interfaces -- never on a concrete vendor. Switching from
one market-data vendor to another is a change in config/sources.yaml plus one
new subclass, with zero changes to quant/, analysts/ or decision/.

Two invariants every provider must honour:

1. Provenance. Each returned record carries source_id, source_url and
   observed_at. A record without provenance is rejected downstream.
2. No fabrication. On failure a provider raises ProviderError or returns an
   empty result. It never interpolates, estimates or invents values. Missing
   data becomes missing data, which becomes reduced confidence, which can
   become an INSUFFICIENT_DATA recommendation.
"""

from __future__ import annotations

import abc
from dataclasses import dataclass, field
from datetime import date, datetime
from enum import Enum
from typing import Any, Iterable, Sequence


# ---------------------------------------------------------------------------
# Shared value types
# ---------------------------------------------------------------------------

class AssertionType(str, Enum):
    """How much epistemic weight a record carries.

    Kept as a first-class type so that no layer can quietly promote a rumour
    into a fact. Only sources with event_authority may emit FACT for a
    corporate event.
    """

    FACT = "FACT"
    INFERENCE = "INFERENCE"
    ESTIMATE = "ESTIMATE"
    RUMOUR = "RUMOUR"


class SourceLevel(int, Enum):
    EXCHANGE_OR_ISSUER = 1
    DATA_VENDOR = 2
    MAJOR_FINANCIAL_NEWS = 3
    SECONDARY_MEDIA = 4
    SOCIAL_OR_FORUM = 5


@dataclass(frozen=True)
class Provenance:
    source_id: str
    source_level: SourceLevel
    observed_at: datetime
    source_url: str | None = None

    def __post_init__(self) -> None:
        if not self.source_id:
            raise ValueError("provenance requires a source_id")


@dataclass(frozen=True)
class OHLCV:
    symbol: str
    trade_date: date
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None
    value_vnd: float | None
    provenance: Provenance
    adj_close: float | None = None
    adj_factor: float = 1.0


@dataclass(frozen=True)
class NewsArticle:
    article_id: str
    title: str
    url: str
    published_at: datetime | None
    provenance: Provenance
    body: str | None = None
    tickers_hint: Sequence[str] = field(default_factory=tuple)
    language: str = "vi"
    # Set by the source registry, not by the article itself. A level-5 source
    # is rumour-only no matter how confidently the article is written.
    rumour_only: bool = False


@dataclass(frozen=True)
class FinancialMetric:
    ticker: str
    period_end: date
    period_type: str  # Q | H | FY
    metric_name: str
    value: float | None
    unit: str
    provenance: Provenance
    assertion_type: AssertionType = AssertionType.FACT
    is_consolidated: bool = True
    is_audited: bool = False


@dataclass(frozen=True)
class MacroObservation:
    series_id: str
    observation_date: date
    value: float | None
    unit: str | None
    region: str
    provenance: Provenance


@dataclass(frozen=True)
class FlowRecord:
    scope: str      # TICKER | SECTOR | MARKET
    scope_key: str
    trade_date: date
    foreign_buy_value: float | None
    foreign_sell_value: float | None
    foreign_net_value: float | None
    provenance: Provenance
    prop_net_value: float | None = None
    etf_net_value: float | None = None
    total_match_value: float | None = None
    foreign_buy_volume: float | None = None     # shares
    foreign_sell_volume: float | None = None


# ---------------------------------------------------------------------------
# Errors
# ---------------------------------------------------------------------------

class ProviderError(RuntimeError):
    """Base class for recoverable provider failures.

    Raising this is always preferable to returning approximate data. The
    collector runner catches it, marks the dataset stale, applies the
    confidence penalty from thresholds.yaml, and continues with what remains.
    """


class ProviderUnavailable(ProviderError):
    """Network, auth or rate-limit failure. Retryable."""


class ProviderDataMissing(ProviderError):
    """Source reachable but has no data for the requested scope. Not retryable."""


# ---------------------------------------------------------------------------
# Interfaces
# ---------------------------------------------------------------------------

class BaseProvider(abc.ABC):
    """Common contract shared by all provider kinds."""

    kind: str = "base"

    def __init__(self, source_id: str, source_level: int, config: dict[str, Any] | None = None):
        self.source_id = source_id
        self.source_level = SourceLevel(source_level)
        self.config = config or {}

    @property
    def capabilities(self) -> frozenset[str]:
        """What this provider can actually serve.

        The runner checks capabilities before dispatching, so a partially
        capable vendor can be registered without pretending to cover
        everything.
        """
        return frozenset(self.config.get("capabilities", ()))

    def supports(self, capability: str) -> bool:
        return capability in self.capabilities

    def health_check(self) -> bool:
        """Cheap reachability probe. Default: assume healthy."""
        return True

    def _provenance(self, url: str | None = None, observed_at: datetime | None = None) -> Provenance:
        return Provenance(
            source_id=self.source_id,
            source_level=self.source_level,
            observed_at=observed_at or datetime.now().astimezone(),
            source_url=url,
        )


class MarketDataProvider(BaseProvider):
    kind = "market_data"

    @abc.abstractmethod
    def get_daily_ohlcv(self, symbol: str, start: date, end: date) -> list[OHLCV]:
        """Adjusted daily bars, ascending by date.

        Adjustment matters more than usual here: VN securities firms issue
        bonus shares and stock dividends frequently, and an unadjusted series
        shows a phantom gap down on the ex-rights date that a naive breakdown
        detector will read as a real breakdown.
        """

    @abc.abstractmethod
    def get_index_ohlcv(self, symbol: str, start: date, end: date) -> list[OHLCV]:
        """Benchmark bars for VNINDEX / VN30 / HNXINDEX."""

    def get_intraday_ohlcv(self, symbol: str, interval_minutes: int) -> list[OHLCV]:
        raise ProviderDataMissing(f"{self.source_id} does not serve intraday data")

    def get_session_totals(self, symbol: str, day: date | None = None) -> OHLCV | None:
        """Today's session rebuilt from the tape, to check a daily bar has settled.

        None means this source has no tape, so today's bar cannot be verified.
        """
        return None


class NewsProvider(BaseProvider):
    kind = "news"

    @abc.abstractmethod
    def fetch_articles(self, tickers: Sequence[str], since: datetime) -> list[NewsArticle]:
        """Return articles published at or after `since`.

        Providers must not filter by perceived importance. Ranking is the
        pipeline's job; a provider that silently drops items breaks the
        novelty score.
        """

    @property
    def event_authority(self) -> bool:
        """Whether this source may originate a FACT-level corporate event."""
        return bool(self.config.get("event_authority", False))

    @property
    def rumour_only(self) -> bool:
        return bool(self.config.get("rumour_only", False)) or self.source_level == SourceLevel.SOCIAL_OR_FORUM


class FinancialDataProvider(BaseProvider):
    kind = "financial"

    @abc.abstractmethod
    def get_metrics(self, ticker: str, periods: int = 8) -> list[FinancialMetric]:
        """Most recent `periods` reporting periods, newest first."""

    def get_sector_specific_metrics(self, ticker: str, sector_profile: str, periods: int = 8) -> list[FinancialMetric]:
        """Metrics that only exist for a given sector profile.

        For `securities`: brokerage_revenue, margin_balance, margin_to_equity,
        prop_trading_pnl, fvtpl_portfolio_value, market_share_hose,
        capital_adequacy_ratio. Applying a generic industrial metric set to a
        broker produces a confidently wrong read.
        """
        raise ProviderDataMissing(f"{self.source_id} has no sector-specific metrics for {sector_profile}")


class MacroDataProvider(BaseProvider):
    kind = "macro"

    @abc.abstractmethod
    def get_series(self, series_id: str, start: date, end: date) -> list[MacroObservation]:
        ...

    def get_latest(self, series_id: str) -> MacroObservation | None:
        today = date.today()
        obs = self.get_series(series_id, date(today.year - 1, today.month, 1), today)
        return obs[-1] if obs else None


class FlowDataProvider(BaseProvider):
    kind = "flow"

    @abc.abstractmethod
    def get_flows(self, scope: str, scope_key: str, start: date, end: date) -> list[FlowRecord]:
        ...

    def get_market_breadth(self, trade_date: date) -> dict[str, int]:
        """Advancers / decliners / unchanged.

        Breadth is tracked separately from index level on purpose: an index
        can rise on four large caps while most of the market falls, and the
        regime classifier must be able to see that difference.
        """
        raise ProviderDataMissing(f"{self.source_id} does not serve breadth data")


PROVIDER_KINDS: dict[str, type[BaseProvider]] = {
    "market_data": MarketDataProvider,
    "news": NewsProvider,
    "financial": FinancialDataProvider,
    "macro": MacroDataProvider,
    "flow": FlowDataProvider,
}


def iter_provider_kinds() -> Iterable[str]:
    return PROVIDER_KINDS.keys()
