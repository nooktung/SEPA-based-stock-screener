"""Signing and error mapping for the DNSE HTTP transport.

The signature tests recompute the expected value from the documented string
rather than pinning a magic constant: if the construction changes, the test
says which part changed instead of just that a hash differs.
"""

import base64
import hashlib
import hmac
from datetime import datetime, timezone
from urllib.parse import quote

import pytest

from src.collectors.base import ProviderDataMissing, ProviderError, ProviderUnavailable
from src.collectors.providers.dnse_client import (
    DnseAuthError,
    DnseCredentials,
    DnseHttpClient,
    build_signature,
    build_signature_header,
    rfc1123,
)

DATE_VALUE = "Wed, 26 Aug 2026 03:11:07 +0000"


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=""):
        self.status_code = status_code
        self._payload = payload
        self.text = text or (str(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("no json")
        return self._payload


class FakeSession:
    """Records requests and replays queued responses."""

    def __init__(self, *responses, raises=None):
        self.responses = list(responses)
        self.calls = []
        self.raises = raises

    def request(self, method, url, headers=None, timeout=None):
        self.calls.append({"method": method, "url": url, "headers": headers, "timeout": timeout})
        if self.raises:
            raise self.raises
        return self.responses.pop(0) if self.responses else FakeResponse(200, {})


def _client(session, **kwargs):
    return DnseHttpClient(
        credentials=DnseCredentials(api_key="key-123456789", api_secret="s3cr3t"),
        session=session,
        clock=lambda: datetime(2026, 8, 26, 3, 11, 7, tzinfo=timezone.utc),
        nonce_factory=lambda: "nonce-abc",
        **kwargs,
    )


# -- credentials -------------------------------------------------------------

def test_credentials_require_both_halves():
    with pytest.raises(DnseAuthError):
        DnseCredentials(api_key="key", api_secret="")


def test_credentials_from_env_explains_what_is_missing(monkeypatch):
    monkeypatch.delenv("DNSE_API_KEY", raising=False)
    with pytest.raises(DnseAuthError) as exc:
        DnseCredentials.from_env()
    assert "DNSE_API_KEY" in str(exc.value)


def test_secret_never_appears_in_repr():
    creds = DnseCredentials(api_key="abcdefghijklmnop", api_secret="super-secret")
    assert "super-secret" not in repr(creds)
    assert "redacted" in repr(creds)


def test_auth_error_is_a_recoverable_provider_error():
    # The runner retries ProviderUnavailable and marks stale; auth failures
    # must travel that path rather than crashing the collection run.
    assert issubclass(DnseAuthError, ProviderUnavailable)


# -- signature ---------------------------------------------------------------

def test_rfc1123_is_utc_regardless_of_input_offset():
    local = datetime(2026, 8, 26, 10, 11, 7, tzinfo=timezone.utc).astimezone(timezone.utc)
    assert rfc1123(local).endswith("+0000")


def test_signature_signs_request_target_date_and_nonce():
    headers, signature = build_signature(
        "s3cr3t", "GET", "/price/ohlc", DATE_VALUE, "hmac-sha256", "nonce-abc"
    )
    expected_string = (
        "(request-target): get /price/ohlc\n"
        f"date: {DATE_VALUE}\n"
        "nonce: nonce-abc"
    )
    expected = quote(
        base64.b64encode(
            hmac.new(b"s3cr3t", expected_string.encode(), hashlib.sha256).digest()
        ).decode(),
        safe="",
    )
    assert headers == "(request-target) date"
    assert signature == expected


def test_signature_omits_nonce_line_when_no_nonce():
    _, with_nonce = build_signature("s", "GET", "/p", DATE_VALUE, nonce="n")
    _, without = build_signature("s", "GET", "/p", DATE_VALUE)
    assert with_nonce != without


def test_signature_header_carries_key_algorithm_and_nonce():
    creds = DnseCredentials(api_key="key-123", api_secret="s3cr3t")
    header = build_signature_header(creds, "GET", "/accounts", DATE_VALUE, nonce="n1")
    assert 'keyId="key-123"' in header
    assert 'algorithm="hmac-sha256"' in header
    assert 'headers="(request-target) date"' in header
    assert 'nonce="n1"' in header
    assert "s3cr3t" not in header


def test_unknown_algorithm_is_rejected():
    with pytest.raises(DnseAuthError):
        build_signature("s", "GET", "/p", DATE_VALUE, algorithm="rot13")


# -- request assembly --------------------------------------------------------

def test_get_sends_the_four_required_headers():
    session = FakeSession(FakeResponse(200, {"ok": True}))
    _client(session).get("/market/working-dates")
    headers = session.calls[0]["headers"]
    assert headers["Date"] == DATE_VALUE
    assert headers["x-api-key"] == "key-123456789"
    assert headers["version"]
    assert headers["X-Signature"].startswith("Signature keyId=")


def test_query_string_is_sent_but_not_signed():
    session = FakeSession(FakeResponse(200, {"ok": True}))
    _client(session).get("/price/ohlc", {"symbol": "SSI", "resolution": "1D"})
    call = session.calls[0]
    assert "symbol=SSI" in call["url"]
    # The signature must match the bare path; a signed query string would break
    # every request the moment a parameter order changed.
    _, expected = build_signature("s3cr3t", "GET", "/price/ohlc", DATE_VALUE, nonce="nonce-abc")
    assert f'signature="{expected}"' in call["headers"]["X-Signature"]


def test_none_valued_query_parameters_are_dropped():
    session = FakeSession(FakeResponse(200, {}))
    _client(session).get("/price/ohlc", {"symbol": "SSI", "boardId": None})
    assert "boardId" not in session.calls[0]["url"]


def test_rate_limit_waits_between_calls():
    session = FakeSession(FakeResponse(200, {}), FakeResponse(200, {}))
    slept = []
    ticks = iter([0.0, 0.0, 0.1, 0.1])
    client = _client(
        session,
        rate_limit_per_min=60,
        monotonic=lambda: next(ticks),
        sleep=slept.append,
    )
    client.get("/a")
    client.get("/b")
    assert slept and slept[0] == pytest.approx(0.9, abs=0.01)


# -- error mapping -----------------------------------------------------------

@pytest.mark.parametrize("status", [401, 403])
def test_auth_rejection_names_the_likely_causes(status):
    session = FakeSession(FakeResponse(status, text="signature invalid"))
    with pytest.raises(DnseAuthError) as exc:
        _client(session).get("/accounts")
    message = str(exc.value)
    assert "clock" in message
    assert "s3cr3t" not in message


def test_404_is_missing_data_not_an_outage():
    session = FakeSession(FakeResponse(404, text="not found"))
    with pytest.raises(ProviderDataMissing):
        _client(session).get("/price/NOPE/close")


@pytest.mark.parametrize("status", [429, 500, 503])
def test_rate_limit_and_server_errors_are_retryable(status):
    session = FakeSession(FakeResponse(status, text="busy"))
    with pytest.raises(ProviderUnavailable):
        _client(session).get("/price/ohlc")


def test_other_client_errors_are_not_retried():
    session = FakeSession(FakeResponse(400, text="bad resolution"))
    with pytest.raises(ProviderError) as exc:
        _client(session).get("/price/ohlc")
    assert not isinstance(exc.value, ProviderUnavailable)


def test_network_failure_becomes_provider_unavailable():
    session = FakeSession(raises=OSError("connection reset"))
    with pytest.raises(ProviderUnavailable):
        _client(session).get("/price/ohlc")


def test_non_json_body_is_an_error_not_an_empty_result():
    session = FakeSession(FakeResponse(200, payload=None, text="<html>maintenance</html>"))
    with pytest.raises(ProviderError):
        _client(session).get("/price/ohlc")


def test_health_check_reports_false_instead_of_raising():
    session = FakeSession(FakeResponse(500, text="down"))
    assert _client(session).health_check() is False


# -- per-group pacing and quotas (2026-10-08) ----------------------------------

class HeaderResponse(FakeResponse):
    def __init__(self, status_code=200, payload=None, text="", headers=None):
        super().__init__(status_code, payload if payload is not None else {}, text)
        self.headers = headers or {}


LIMITS = {"ohlc": {"per_min": 300, "per_hour": 30000}, "secdef": {"per_min": 60, "per_hour": 3},
          "working_dates": {"per_min": 10, "per_hour": 60}}


def test_paths_map_to_dnse_limit_groups():
    from src.collectors.providers.dnse_client import rate_group

    assert rate_group("/price/ohlc") == "ohlc"
    assert rate_group("/price/VNINDEX/market-index") == "market_index"
    assert rate_group("/price/SSI/secdef") == "secdef" and rate_group("/price/SSI/trades") == "trades"
    assert rate_group("/price/SSI/foreign-trading") == "foreign"
    assert rate_group("/market/working-dates") == "working_dates" and rate_group("/somewhere/else") == "default"


def _clocked(session, **kwargs):
    t = {"now": 0.0}
    slept = []

    def sleep(s):
        slept.append(round(s, 3))
        t["now"] += s
    client = _client(session, monotonic=lambda: t["now"], sleep=sleep, **kwargs)
    return client, slept, t


def test_each_group_keeps_its_own_pace():
    client, slept, _ = _clocked(FakeSession(), rate_limits=LIMITS)
    client.get("/price/ohlc")
    client.get("/price/ohlc")                         # 300/min: 0.2 s apart
    client.get("/price/SSI/secdef")                   # another group: no wait for the ohlc pace
    client.get("/somewhere/else")                     # unlisted: the strictest pace (10/min) -- first call free
    client.get("/somewhere/else")
    assert slept == [0.2, 6.0]


def test_the_hourly_budget_waits_for_the_oldest_call_to_age_out():
    client, slept, t = _clocked(FakeSession(), rate_limits=LIMITS)
    for _ in range(3):
        client.get("/price/SSI/secdef")               # 60/min: 1 s apart, budget 3 per hour
    client.get("/price/SSI/secdef")
    assert slept[:2] == [1.0, 1.0] and slept[2] == pytest.approx(3600.0 - 2.0, abs=0.01)


def test_a_quota_nearly_used_stops_before_dnse_answers_403():
    from src.collectors.providers.dnse_client import DnseQuotaExceeded

    reset = int(datetime(2026, 8, 26, 10, 0, tzinfo=timezone.utc).timestamp())
    session = FakeSession(HeaderResponse(200, headers={"X-Ratelimit-Limit": "10000", "X-Ratelimit-Remaining": "40",
                                                        "X-Ratelimit-Reset": str(reset)}))
    client = _client(session, min_remaining=50)
    client.get("/price/VNINDEX/market-index")
    assert client.quota["market_index"]["remaining"] == 40
    with pytest.raises(DnseQuotaExceeded, match="quota"):
        client.get("/price/VN30/market-index")
    assert len(session.calls) == 1
    client.get("/price/ohlc")                         # other groups are unaffected


def test_403_with_a_quota_message_is_a_quota_error_not_a_bad_signature():
    from src.collectors.providers.dnse_client import DnseQuotaExceeded

    with pytest.raises(DnseQuotaExceeded):
        _client(FakeSession(HeaderResponse(403, text='{"message":"Quota exceeded"}'))).get("/price/ohlc")
    with pytest.raises(DnseQuotaExceeded):
        _client(FakeSession(HeaderResponse(403, text="forbidden", headers={"X-Ratelimit-Remaining": "0"}))).get("/price/ohlc")
    with pytest.raises(DnseAuthError, match="quota is used up"):
        _client(FakeSession(HeaderResponse(403, text="signature invalid"))).get("/price/ohlc")


def test_429_is_retried_after_retry_after_and_the_group_slows_down():
    session = FakeSession(HeaderResponse(429, text="busy", headers={"Retry-After": "2"}), HeaderResponse(200, {"ok": 1}))
    client, slept, _ = _clocked(session, rate_limits=LIMITS, retry_429=2)
    assert client.get("/price/ohlc") == {"ok": 1}
    assert 2.0 in slept and len(session.calls) == 2
    assert client._paces["ohlc"].min_interval == 0.5          # 0.2 s doubled, at least 0.5 s
    # with retries exhausted it is still a retryable error
    worn = FakeSession(*[HeaderResponse(429, text="busy") for _ in range(3)])
    with pytest.raises(ProviderUnavailable):
        _clocked(worn, rate_limits=LIMITS, retry_429=2)[0].get("/price/ohlc")
    assert len(worn.calls) == 3
