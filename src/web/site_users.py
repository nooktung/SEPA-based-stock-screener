"""Accounts of the published copy's login (cloudflare/functions/_middleware.js), 2026-10-13.

The user chose an account and password of their own for the web copy instead of Cloudflare
Access's email codes. Each account is a PBKDF2-SHA256 hash of its password with its own salt;
the password is typed at a prompt (`python -m src.main site-user add <name>`) and kept in clear
nowhere -- not in this repository, not in data/, not on Cloudflare. The records (hashes only) are
kept in data/site_users.json (never committed) and uploaded as the Pages secret SITE_USERS
together with a fresh SESSION_SECRET, so any change of accounts signs everyone out. Cloudflare
applies secrets from the next deployment (scripts/deploy_site.ps1).

Roles: "owner" may add / remove tickers in "Danh sách của tôi" on the web; "viewer" only reads.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import secrets
from pathlib import Path
from typing import Any

from src.config import PROJECT_ROOT

USERS_FILE = PROJECT_ROOT / "data" / "site_users.json"
NAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{1,31}$")
ROLES = ("owner", "viewer")
API = "https://api.cloudflare.com/client/v4/accounts/{account}/pages/projects/{project}"


class SiteUserError(ValueError):
    """An account name, password or role that is not accepted; nothing was changed."""


def make_record(password: str, role: str, iterations: int, salt: bytes | None = None) -> dict[str, Any]:
    salt = salt if salt is not None else secrets.token_bytes(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return {"role": role, "salt": base64.b64encode(salt).decode("ascii"),
            "hash": base64.b64encode(digest).decode("ascii"), "iter": iterations}


def load(path: Path | None = None) -> dict[str, dict[str, Any]]:
    p = path or USERS_FILE
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def save(users: dict[str, dict[str, Any]], path: Path | None = None) -> None:
    p = path or USERS_FILE
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(users, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")
    os.replace(tmp, p)


def check_name(name: str) -> str:
    n = name.strip().lower()
    if not NAME.match(n):
        raise SiteUserError("tên tài khoản: 2-32 ký tự a-z, 0-9, dấu . _ - (VD: admin)")
    return n


def add(users: dict[str, dict[str, Any]], name: str, password: str, role: str, cfg: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """A new account, or a new password / role for an existing one."""
    n = check_name(name)
    if role not in ROLES:
        raise SiteUserError(f"vai trò phải là {' hoặc '.join(ROLES)}")
    if len(password) < int(cfg["min_password_length"]):
        raise SiteUserError(f"mật khẩu cần ít nhất {cfg['min_password_length']} ký tự")
    if password.strip().lower() == n:
        raise SiteUserError("mật khẩu không được trùng tên tài khoản")
    return {**users, n: make_record(password, role, int(cfg["pbkdf2_iterations"]))}


def remove(users: dict[str, dict[str, Any]], name: str) -> dict[str, dict[str, Any]]:
    n = check_name(name)
    if n not in users:
        raise SiteUserError(f"không có tài khoản {n}")
    return {k: v for k, v in users.items() if k != n}


def upload(users: dict[str, dict[str, Any]], project: str, session: Any | None = None) -> None:
    """SITE_USERS and a fresh SESSION_SECRET as secrets of the Pages project (production and
    preview), with CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID from the environment (.env)."""
    token, account = os.environ.get("CLOUDFLARE_API_TOKEN"), os.environ.get("CLOUDFLARE_ACCOUNT_ID")
    if not token or not account:
        raise SiteUserError("chưa có CLOUDFLARE_API_TOKEN / CLOUDFLARE_ACCOUNT_ID trong .env")
    if session is None:
        import requests  # noqa: PLC0415

        session = requests.Session()
    env_vars = {"SITE_USERS": {"type": "secret_text", "value": json.dumps(users, separators=(",", ":"))},
                "SESSION_SECRET": {"type": "secret_text", "value": secrets.token_urlsafe(48)}}
    body = {"deployment_configs": {"production": {"env_vars": env_vars}, "preview": {"env_vars": env_vars}}}
    try:
        r = session.patch(API.format(account=account, project=project), data=json.dumps(body),
                          headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"}, timeout=30)
    except Exception as exc:  # noqa: BLE001
        raise SiteUserError(f"không gửi được lên Cloudflare: {type(exc).__name__}") from exc
    if r.status_code != 200:
        raise SiteUserError(f"Cloudflare trả {r.status_code} khi lưu tài khoản (token cần quyền Cloudflare Pages: Edit)")
