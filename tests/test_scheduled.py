"""Scheduled jobs collect news each time and only publish after a successful export."""
from contextlib import contextmanager

import pytest

from src import scheduled as job


@pytest.fixture
def calls(monkeypatch):
    calls = []

    @contextmanager
    def lock(wait):
        yield True

    monkeypatch.setattr(job, "scheduled_lock", lock)
    monkeypatch.setattr(job, "wait_pipeline", lambda wait: True)
    monkeypatch.setattr(job.pl, "release_lock", lambda: calls.append(("unlock",)))
    monkeypatch.setattr(job, "command", lambda *args: calls.append(args) or 0)
    monkeypatch.setattr(job, "run", lambda args: calls.append(("deploy",)) or 0)
    return calls


def test_news_collects_and_publishes_even_with_unchanged_list(calls):
    assert job.workflow("news") == 0
    assert calls == [("mylist-sync", "--refresh"), ("news",),
                     ("export-site", "--update"), ("deploy",), ("unlock",)]


def test_daily_runs_start_once_with_news_and_then_publishes(calls):
    assert job.workflow("daily") == 0
    assert calls == [("mylist-sync",), ("unlock",),
                     ("start", "--update-only", "--refresh-news"),
                     ("ssi", "--days", "3"), ("crosscheck", "--days", "30"),
                     ("export-site", "--update"), ("deploy",), ("unlock",)]


def test_export_refusal_prevents_deployment(calls, monkeypatch):
    def command(*args):
        calls.append(args)
        return 2 if args[0] == "export-site" else 0

    monkeypatch.setattr(job, "command", command)
    assert job.workflow("news") == 1
    assert ("deploy",) not in calls
    assert calls[-1] == ("unlock",)


def test_news_yields_to_running_pipeline(calls, monkeypatch):
    monkeypatch.setattr(job, "wait_pipeline", lambda wait: False)
    assert job.workflow("news") == 0
    assert calls == []


def test_failed_news_does_not_publish(calls, monkeypatch):
    monkeypatch.setattr(job, "command", lambda *args: 1 if args[0] == "news" else 0)
    assert job.workflow("news") == 1
    assert calls == [("unlock",)]


def test_list_sync_failure_does_not_cancel_news(calls, monkeypatch):
    def command(*args):
        calls.append(args)
        return 1 if args[0] == "mylist-sync" else 0

    monkeypatch.setattr(job, "command", command)
    assert job.workflow("news") == 0
    assert ("news",) in calls and ("deploy",) in calls


def test_start_refresh_news_overrides_freshness_skip(monkeypatch):
    from types import SimpleNamespace
    from src import main

    news = job.pl.Step("news", "News", ["news"], "", run=False)
    price = job.pl.Step("backfill", "Prices", ["backfill"], "", run=False)
    plan = SimpleNamespace(steps=[price, news])
    monkeypatch.setattr(main, "init_db", lambda: None)
    monkeypatch.setattr(job.pl, "settings", lambda: {})
    monkeypatch.setattr(job.pl, "acquire_lock", lambda *args: None)
    monkeypatch.setattr(job.pl, "release_lock", lambda: None)
    monkeypatch.setattr(job.pl, "dnse_index_dates", lambda now: [])
    monkeypatch.setattr(job.pl, "plan_steps", lambda *args, **kwargs: plan)
    monkeypatch.setattr(job.pl, "summary", lambda plan: "")
    monkeypatch.setattr(job.pl, "run_steps", lambda plan: None)
    assert main.start(["--update-only", "--refresh-news"]) == 0
    assert news.run
    assert not price.run
