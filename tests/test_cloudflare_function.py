"""The web copy's Pages Functions (cloudflare/functions/), run under Node: the login middleware and
the list ("Danh sách của tôi")."""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent


def _node(script: str) -> str:
    run = subprocess.run(["node", str(ROOT / "cloudflare" / "test" / script)], capture_output=True,
                         text=True, encoding="utf-8", timeout=120)
    assert run.returncode == 0, run.stderr or run.stdout
    return run.stdout


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_nothing_of_the_site_without_a_login():
    assert "login middleware: ok" in _node("login.test.mjs")


@pytest.mark.skipif(shutil.which("node") is None, reason="Node is not installed")
def test_only_the_owner_writes_the_web_list():
    assert "mylist function: ok" in _node("mylist.test.mjs")
