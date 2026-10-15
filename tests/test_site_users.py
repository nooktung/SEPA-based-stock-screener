"""Accounts of the web copy's login: hashes only, the same PBKDF2 as the Cloudflare middleware."""

import json

import pytest

from src import main
from src.web import site_users as su

CFG = {"min_password_length": 8, "pbkdf2_iterations": 5000}


def test_the_hash_is_the_one_the_middleware_checks():
    # The same vector as cloudflare/test/login.test.mjs (WebCrypto PBKDF2 there).
    rec = su.make_record("mật-khẩu thử", "owner", 5000, salt=b"0123456789abcdef")
    assert rec == {"role": "owner", "salt": "MDEyMzQ1Njc4OWFiY2RlZg==",
                   "hash": "W/jVDKe8W6JXKFagk2XBOho6lJXhcU077vgnzseVkeg=", "iter": 5000}


def test_names_passwords_and_roles_are_checked():
    users = su.add({}, " Admin ", "mot-mat-khau", "owner", CFG)
    assert list(users) == ["admin"] and users["admin"]["role"] == "owner"
    assert users["admin"]["salt"] != su.add({}, "admin", "mot-mat-khau", "owner", CFG)["admin"]["salt"]   # own salt
    for name, pw, role, why in (("a", "mot-mat-khau", "viewer", "tên"), ("ad min", "mot-mat-khau", "viewer", "tên"),
                                ("ban", "ngan", "viewer", "ít nhất 8"), ("admin123", "Admin123", "viewer", "trùng"),
                                ("ban", "mot-mat-khau", "root", "vai trò")):
        with pytest.raises(su.SiteUserError, match=why):
            su.add({}, name, pw, role, CFG)
    assert su.remove(users, "ADMIN") == {}
    with pytest.raises(su.SiteUserError):
        su.remove(users, "khong-co")


class Resp:
    def __init__(self, status):
        self.status_code = status


class Session:
    def __init__(self, status=200):
        self.status, self.calls = status, []

    def patch(self, url, data, headers, timeout):
        self.calls.append((url, json.loads(data), headers))
        return Resp(self.status)


def test_upload_sends_hashes_and_a_new_session_secret_as_secrets(monkeypatch):
    monkeypatch.setenv("CLOUDFLARE_API_TOKEN", "tok")
    monkeypatch.setenv("CLOUDFLARE_ACCOUNT_ID", "acc")
    users = su.add({}, "admin", "mot-mat-khau", "owner", CFG)
    s = Session()
    su.upload(users, "sepa-vn", s)
    url, body, headers = s.calls[0]
    assert url.endswith("/accounts/acc/pages/projects/sepa-vn") and headers["Authorization"] == "Bearer tok"
    for env in ("production", "preview"):
        v = body["deployment_configs"][env]["env_vars"]
        assert v["SITE_USERS"]["type"] == v["SESSION_SECRET"]["type"] == "secret_text"
        assert json.loads(v["SITE_USERS"]["value"]) == users and len(v["SESSION_SECRET"]["value"]) >= 32
    assert "mot-mat-khau" not in json.dumps(body)
    with pytest.raises(su.SiteUserError, match="403"):
        su.upload(users, "sepa-vn", Session(403))
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN")
    with pytest.raises(su.SiteUserError, match="CLOUDFLARE_API_TOKEN"):
        su.upload(users, "sepa-vn", Session())


@pytest.fixture
def cli(tmp_path, monkeypatch):
    path = tmp_path / "site_users.json"
    monkeypatch.setattr(su, "USERS_FILE", path)
    monkeypatch.setattr("src.env.load_dotenv", lambda *a, **k: None)     # never the real .env in a test
    sent = []
    monkeypatch.setattr(su, "upload", lambda users, project, session=None: sent.append((users, project)))

    def typed(*answers):
        it = iter(answers)
        monkeypatch.setattr("getpass.getpass", lambda prompt="": next(it))
    return path, sent, typed


def test_the_command_stores_hashes_only_and_uploads(cli, capsys):
    path, sent, typed = cli
    typed("mot-mat-khau", "mot-mat-khau")
    assert main.site_user(["add", "Admin", "--role", "owner"]) == 0
    stored = json.loads(path.read_text(encoding="utf-8"))
    assert list(stored) == ["admin"] and stored["admin"]["role"] == "owner"
    assert "mot-mat-khau" not in path.read_text(encoding="utf-8")
    assert sent[-1] == (stored, "sepa-vn")
    typed("mat-khau-ban", "khac-han-nhe")                                   # the two entries differ
    assert main.site_user(["add", "ban"]) == 1
    assert list(json.loads(path.read_text(encoding="utf-8"))) == ["admin"] and len(sent) == 1
    typed("mat-khau-ban", "mat-khau-ban")
    assert main.site_user(["add", "ban"]) == 0                              # a viewer by default
    assert json.loads(path.read_text(encoding="utf-8"))["ban"]["role"] == "viewer"
    assert main.site_user(["list"]) == 0
    assert "admin  (owner)" in capsys.readouterr().out
    assert main.site_user(["remove", "ban"]) == 0
    assert list(json.loads(path.read_text(encoding="utf-8"))) == ["admin"] and list(sent[-1][0]) == ["admin"]


def test_the_window_hands_the_password_on_standard_input(cli, monkeypatch):
    import io

    path, sent, typed = cli
    typed()                                                                 # getpass must not be asked
    monkeypatch.setattr("sys.stdin", io.StringIO("﻿mật-khẩu thử\r\n"))         # as PowerShell 5.1 pipes it
    assert main.site_user(["add", "admin", "--role", "owner", "--password-stdin"]) == 0
    rec = json.loads(path.read_text(encoding="utf-8"))["admin"]
    salt = __import__("base64").b64decode(rec["salt"])
    assert su.make_record("mật-khẩu thử", "owner", rec["iter"], salt=salt) == rec       # no stray CR / LF
    monkeypatch.setattr("sys.stdin", io.StringIO("ngan\n"))
    assert main.site_user(["add", "ban", "--password-stdin"]) == 1          # the rules still apply


def test_a_failed_upload_changes_nothing_here(cli, monkeypatch):
    path, sent, typed = cli
    def fail(users, project, session=None):
        raise su.SiteUserError("Cloudflare trả 403 khi lưu tài khoản")
    monkeypatch.setattr(su, "upload", fail)
    typed("mot-mat-khau", "mot-mat-khau")
    assert main.site_user(["add", "admin", "--role", "owner"]) == 1
    assert not path.exists()
