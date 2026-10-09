"""Reading price history out of the database and writing features back.

The quant layer talks to storage through this module only, so the indicator
code stays pure functions over series and can be tested without a database.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Mapping, Sequence

import pandas as pd

from src.database import connect, utcnow

PRICE_COLUMNS = ("open", "high", "low", "close", "volume", "value_vnd", "adj_close", "adj_factor")
INDEX_COLUMNS = ("open", "high", "low", "close", "volume", "value_vnd")


class InsufficientHistory(RuntimeError):
    """Fewer bars than the requested calculation needs."""


def _frame(rows: Sequence, columns: Sequence[str]) -> pd.DataFrame:
    frame = pd.DataFrame([dict(row) for row in rows])
    if frame.empty:
        return pd.DataFrame(columns=["trade_date", *columns]).set_index("trade_date")
    frame["trade_date"] = pd.to_datetime(frame["trade_date"])
    return frame.set_index("trade_date").sort_index()


def load_prices(ticker: str, end: date | None = None, path: Path | None = None) -> pd.DataFrame:
    """Daily bars for one ticker, ascending, indexed by trade date.

    `end` clips the history so a calculation can be reproduced as it would have
    looked on a past date -- the backtester in Phase 13 depends on being able
    to ask for exactly that, with no later bar visible.
    """
    query = f"SELECT trade_date, {', '.join(PRICE_COLUMNS)} FROM prices_daily WHERE ticker = ?"
    params: list = [ticker.upper()]
    if end is not None:
        query += " AND trade_date <= ?"
        params.append(end.isoformat())
    query += " ORDER BY trade_date"

    with connect(path) as conn:
        rows = conn.execute(query, params).fetchall()
    return _frame(rows, PRICE_COLUMNS)


def load_index(symbol: str, end: date | None = None, path: Path | None = None) -> pd.DataFrame:
    query = f"SELECT trade_date, {', '.join(INDEX_COLUMNS)} FROM index_daily WHERE symbol = ?"
    params: list = [symbol.upper()]
    if end is not None:
        query += " AND trade_date <= ?"
        params.append(end.isoformat())
    query += " ORDER BY trade_date"

    with connect(path) as conn:
        rows = conn.execute(query, params).fetchall()
    return _frame(rows, INDEX_COLUMNS)


FLOW_COLUMNS = (
    "foreign_buy_value",
    "foreign_sell_value",
    "foreign_net_value",
    "prop_net_value",
    "etf_net_value",
    "total_match_value",
)


def load_flows(
    scope_key: str,
    scope: str = "TICKER",
    end: date | None = None,
    path: Path | None = None,
) -> pd.DataFrame:
    """Daily flow records for one scope, ascending, indexed by trade date."""
    query = f"SELECT trade_date, {', '.join(FLOW_COLUMNS)} FROM flow_daily WHERE scope = ? AND scope_key = ?"
    params: list = [scope, scope_key.upper()]
    if end is not None:
        query += " AND trade_date <= ?"
        params.append(end.isoformat())
    query += " ORDER BY trade_date"

    with connect(path) as conn:
        rows = conn.execute(query, params).fetchall()
    return _frame(rows, FLOW_COLUMNS)


def price_series(frame: pd.DataFrame) -> pd.Series:
    """The series indicators should run on.

    Prefers `adj_close`. Falls back to `close` only when every adjusted value
    is missing -- a series that is half adjusted would put a step in the middle
    of every moving average, which is worse than either choice consistently.
    """
    if "adj_close" in frame and frame["adj_close"].notna().any():
        adjusted = frame["adj_close"]
        if adjusted.notna().all():
            return adjusted.astype("float64")
    return frame["close"].astype("float64")


def uses_adjusted_prices(frame: pd.DataFrame) -> bool:
    return bool("adj_close" in frame and frame["adj_close"].notna().all() and not frame.empty)


def upsert_features(
    ticker: str,
    trade_date: date,
    features: Mapping[str, float | None],
    path: Path | None = None,
) -> int:
    """Write one session's feature vector.

    Missing values are written as NULL rather than skipped. An absent row means
    "never computed"; a NULL row means "computed, and there was not enough
    history" -- and the difference is exactly what the data-completeness score
    needs to read later.
    """
    if not features:
        return 0

    computed_at = utcnow()
    rows = [
        (ticker.upper(), trade_date.isoformat(), name, None if value is None else float(value), computed_at)
        for name, value in features.items()
    ]
    with connect(path) as conn:
        conn.executemany(
            """
            INSERT INTO technical_features (ticker, trade_date, feature_name, value, computed_at)
            VALUES (?, ?, ?, ?, ?)
            ON CONFLICT(ticker, trade_date, feature_name) DO UPDATE SET
                value=excluded.value,
                computed_at=excluded.computed_at
            """,
            rows,
        )
    return len(rows)


def load_features(ticker: str, trade_date: date, path: Path | None = None) -> dict[str, float | None]:
    with connect(path) as conn:
        rows = conn.execute(
            "SELECT feature_name, value FROM technical_features WHERE ticker = ? AND trade_date = ?",
            (ticker.upper(), trade_date.isoformat()),
        ).fetchall()
    return {row["feature_name"]: row["value"] for row in rows}
