"""Shared test setup.

The suite must never touch the network. Real DNSE credentials in the ambient
environment would make the registry prefer the live source over the fixture,
so they are removed for the duration of every test -- a developer with a
working .env exported into their shell still gets the offline suite.
"""

import os

import pytest

DNSE_ENV_VARS = (
    "DNSE_API_KEY",
    "DNSE_API_SECRET",
    "DNSE_BASE_URL",
    "DNSE_WS_URL",
    "DNSE_API_VERSION",
)


@pytest.fixture(autouse=True)
def _no_live_credentials(monkeypatch):
    for name in DNSE_ENV_VARS:
        monkeypatch.delenv(name, raising=False)
    yield


@pytest.fixture
def dnse_credentials():
    from src.collectors.providers.dnse_client import DnseCredentials

    return DnseCredentials(api_key="test-key", api_secret="test-secret")


@pytest.fixture(autouse=True)
def _no_stored_trading_calendar(monkeypatch, tmp_path):
    """Tests see weekdays only, unless they write a calendar of their own."""
    import src.market_calendar as mc

    monkeypatch.setattr(mc, "CALENDAR_FILE", tmp_path / "no_calendar" / "working_dates.json")
    yield


@pytest.fixture(autouse=True)
def _audit_reports_in_tmp(monkeypatch, tmp_path):
    """index-check and invariant reports go to a temporary folder, never data/audit."""
    import src.collectors.index_check as ic
    import src.web.checks as checks

    monkeypatch.setattr(ic, "REPORT_DIR", tmp_path / "audit")
    monkeypatch.setattr(checks, "REPORT_DIR", tmp_path / "audit")
    yield
