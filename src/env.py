"""Minimal .env loader.

Deliberately not python-dotenv: this needs twenty lines, and a credential path
with no third-party code in it is one less thing to audit.

Real environment variables always win over the file, so a CI secret or a
`DNSE_API_KEY=... python -m src.main collect` invocation is never silently
overridden by a stale .env on disk.
"""

from __future__ import annotations

import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_PATH = PROJECT_ROOT / ".env"


def load_dotenv(path: Path | None = None, override: bool = False) -> int:
    """Read KEY=VALUE lines into os.environ. Returns how many were applied.

    Understands `export KEY=value`, `#` comments, blank lines and quoted
    values. Anything malformed is skipped rather than raising -- a typo in the
    file should not stop a collection run that has its credentials from the
    real environment anyway.

    An empty value is skipped too. .env.example ships placeholders for phases
    that are not built yet, and a key set to the empty string reads as
    configured-but-broken to most SDKs, which is a worse error than absent.
    """
    target = path or DEFAULT_ENV_PATH
    if not target.exists():
        return 0

    applied = 0
    for raw_line in target.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export "):].lstrip()
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if not value:
            continue
        if override or key not in os.environ:
            os.environ[key] = value
            applied += 1
    return applied
