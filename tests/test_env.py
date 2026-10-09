"""The .env reader. Credentials path, so it gets its own tests."""

import os

from src.env import load_dotenv


def _write(tmp_path, text):
    path = tmp_path / ".env"
    path.write_text(text, encoding="utf-8")
    return path


def test_values_are_loaded(tmp_path, monkeypatch):
    monkeypatch.delenv("DNSE_API_KEY", raising=False)
    load_dotenv(_write(tmp_path, "DNSE_API_KEY=abc123\n"))
    assert os.environ["DNSE_API_KEY"] == "abc123"


def test_real_environment_wins_over_the_file(tmp_path, monkeypatch):
    # A secret injected by CI, or prefixed onto one command, must not be
    # overridden by a stale .env sitting in the working directory.
    monkeypatch.setenv("DNSE_API_KEY", "from-environment")
    load_dotenv(_write(tmp_path, "DNSE_API_KEY=from-file\n"))
    assert os.environ["DNSE_API_KEY"] == "from-environment"


def test_override_is_available_when_asked_for(tmp_path, monkeypatch):
    monkeypatch.setenv("DNSE_API_KEY", "from-environment")
    load_dotenv(_write(tmp_path, "DNSE_API_KEY=from-file\n"), override=True)
    assert os.environ["DNSE_API_KEY"] == "from-file"


def test_empty_placeholder_is_not_set_at_all(tmp_path, monkeypatch):
    # .env.example ships blank keys for phases that do not exist yet. Setting
    # them to "" reads as configured-but-broken to most SDKs.
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    load_dotenv(_write(tmp_path, "ANTHROPIC_API_KEY=\nDNSE_API_KEY=abc\n"))
    assert "ANTHROPIC_API_KEY" not in os.environ
    assert os.environ["DNSE_API_KEY"] == "abc"


def test_comments_blank_lines_and_export_prefix(tmp_path, monkeypatch):
    monkeypatch.delenv("DNSE_API_SECRET", raising=False)
    applied = load_dotenv(
        _write(tmp_path, "# a comment\n\nexport DNSE_API_SECRET=s3cr3t\nnot a pair\n")
    )
    assert applied == 1
    assert os.environ["DNSE_API_SECRET"] == "s3cr3t"


def test_quoted_values_are_unwrapped(tmp_path, monkeypatch):
    monkeypatch.delenv("DNSE_BASE_URL", raising=False)
    load_dotenv(_write(tmp_path, 'DNSE_BASE_URL="https://openapi.dnse.com.vn"\n'))
    assert os.environ["DNSE_BASE_URL"] == "https://openapi.dnse.com.vn"


def test_a_missing_file_is_not_an_error(tmp_path):
    assert load_dotenv(tmp_path / "nope.env") == 0
