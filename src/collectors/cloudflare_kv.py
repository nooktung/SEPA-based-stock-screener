"""The lists ("Danh sách của tôi") kept in Cloudflare Workers KV (2026-10-13, user decision).

The published copy of the dashboard is static, so the lists edited from the web live in KV,
one value per account of the copy's login (key "mylist:<account>", since 2026-10-14), written
by the Pages Function cloudflare/functions/api/mylist.js for the logged-in account. Each value is
the master of its list: this machine reads them all (`mylist-sync`) -- site.local_account's into
`user_watchlist` (the local dashboard writes through that one, so the two never drift), the
others into `web_watchlist` -- and monitors and publishes every ticker in them. A value:

    {"items": [{"ticker": "PNJ", "added_at": "2026-10-13T07:00:00+00:00", "note": null}, ...],
     "updated_at": "..."}

Reads and writes go through Cloudflare's REST API with CLOUDFLARE_API_TOKEN (needs
"Workers KV Storage: Edit"), CLOUDFLARE_ACCOUNT_ID and CLOUDFLARE_KV_MYLIST_ID from .env.
Without them nothing here is used and the list stays local, as before. Never logs the token.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Callable

API = "https://api.cloudflare.com/client/v4/accounts/{account}/storage/kv/namespaces/{ns}/values/{key}"
PREFIX = "mylist:"
ENV_KEYS = ("CLOUDFLARE_API_TOKEN", "CLOUDFLARE_ACCOUNT_ID", "CLOUDFLARE_KV_MYLIST_ID")


class KvError(RuntimeError):
    """The KV value could not be read or written; nothing was changed on this side."""


def configured() -> bool:
    return all(os.environ.get(k) for k in ENV_KEYS)


def key_for(account: str) -> str:
    """The KV key of one account's list (the account names of src/web/site_users.py)."""
    return PREFIX + account


def _url(account: str) -> str:
    return API.format(account=os.environ["CLOUDFLARE_ACCOUNT_ID"], ns=os.environ["CLOUDFLARE_KV_MYLIST_ID"],
                      key=key_for(account))


def _session(session: Any | None) -> Any:
    if session is not None:
        return session
    import requests  # noqa: PLC0415

    return requests.Session()


def read_list(account: str, session: Any | None = None) -> dict[str, Any]:
    """One account's list; an empty one marked `missing` when its key was never written."""
    s = _session(session)
    try:
        r = s.get(_url(account), headers={"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}"}, timeout=20)
    except Exception as exc:  # noqa: BLE001
        raise KvError(f"không đọc được danh sách trên Cloudflare: {type(exc).__name__}") from exc
    if r.status_code == 404:
        return {"items": [], "updated_at": None, "missing": True}
    if r.status_code != 200:
        raise KvError(f"Cloudflare KV trả {r.status_code} khi đọc danh sách")
    try:
        data = json.loads(r.text)
    except ValueError as exc:
        raise KvError("giá trị danh sách trên Cloudflare không phải JSON") from exc
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        raise KvError("giá trị danh sách trên Cloudflare sai cấu trúc")
    return data


def read_or_seed(account: str, local_items: list[dict[str, Any]], session: Any | None = None) -> dict[str, Any]:
    """The local account's list. A value never written (just set up) gets this machine's list
    first: read as empty, it would have emptied the list here on the first `mylist-sync`. The web
    refuses to write the local account's list until then (functions/api/mylist.js)."""
    data = read_list(account, session)
    if data.get("missing"):
        return write_list(account, local_items, session)
    return data


def write_list(account: str, items: list[dict[str, Any]], session: Any | None = None,
               now: Callable[[], datetime] = lambda: datetime.now(timezone.utc)) -> dict[str, Any]:
    payload = {"items": items, "updated_at": now().isoformat(timespec="seconds")}
    s = _session(session)
    try:
        r = s.put(_url(account), data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
                  headers={"Authorization": f"Bearer {os.environ['CLOUDFLARE_API_TOKEN']}",
                           "Content-Type": "text/plain; charset=utf-8"}, timeout=20)
    except Exception as exc:  # noqa: BLE001
        raise KvError(f"không ghi được danh sách lên Cloudflare: {type(exc).__name__}") from exc
    if r.status_code != 200:
        raise KvError(f"Cloudflare KV trả {r.status_code} khi ghi danh sách")
    return payload
