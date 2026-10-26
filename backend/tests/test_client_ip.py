"""Tests for rate-limit IP bucketing (auth_dependencies._client_ip).

WHAT THIS PINS DOWN. _client_ip reads the RESOLVED client — scope["client"] —
rather than parsing X-Forwarded-For itself. Uvicorn's ProxyHeadersMiddleware
owns that header (enabled via --proxy-headers in the Procfile), and two places
parsing it would eventually disagree about which hop is the client.

WHAT THESE TESTS DELIBERATELY DO NOT CLAIM. Spoof resistance. The Procfile
currently passes --forwarded-allow-ips='*', which trusts every hop and so
resolves to the FIRST entry — attacker-controlled. Asserting the bypass is
closed would be asserting something false today; asserting it is OPEN is
honest, and the test below says so out loud so the day someone tightens
--forwarded-allow-ips, this file is what tells them the guarantee changed.

The staged plan: move Railway's proxy to a private IP, set that range, then
this becomes spoof-proof and the xfail below flips to a real assertion.
"""

import pytest
from starlette.requests import Request

from auth_dependencies import _client_ip


def request_with(client: tuple[str, int] | None, headers: dict | None = None) -> Request:
    """A Request shaped as the ASGI server hands it over. `client` is what
    uvicorn puts in scope — already rewritten by ProxyHeadersMiddleware when
    that middleware is on."""
    raw = [
        (k.lower().encode(), v.encode()) for k, v in (headers or {}).items()
    ]
    return Request({"type": "http", "method": "POST", "path": "/auth/login",
                    "headers": raw, "client": client})


class TestItUsesTheResolvedClient:
    def test_returns_the_client_host(self):
        assert _client_ip(request_with(("203.0.113.9", 54321))) == "203.0.113.9"

    def test_ignores_the_raw_header(self):
        """The header is the middleware's business. If this read it directly
        it would override whatever the middleware resolved — including, once
        the trusted range is set, deliberately discarding a spoofed hop."""
        request = request_with(
            ("203.0.113.9", 54321),
            {"X-Forwarded-For": "1.2.3.4, 203.0.113.9"},
        )
        assert _client_ip(request) == "203.0.113.9"

    def test_a_hostile_header_value_cannot_reach_the_bucket_key(self):
        """The IP is a dict key, never logged, stored or queried — but it
        should not be attacker-writable regardless."""
        request = request_with(
            ("203.0.113.9", 54321),
            {"X-Forwarded-For": "'; DROP TABLE broker--"},
        )
        assert _client_ip(request) == "203.0.113.9"

    def test_no_client_falls_back_to_unknown(self):
        """Fails CLOSED: every such request shares one bucket, so the limit
        gets stricter, never looser."""
        assert _client_ip(request_with(None)) == "unknown"

    def test_two_clients_get_different_buckets(self):
        a = _client_ip(request_with(("203.0.113.9", 1)))
        b = _client_ip(request_with(("198.51.100.7", 1)))
        assert a != b


class TestTheKnownGap:
    @pytest.mark.xfail(
        reason=(
            "Procfile uses --forwarded-allow-ips='*', so uvicorn takes the "
            "FIRST X-Forwarded-For hop — attacker-controlled. Closes once "
            "Railway's proxy moves to a private IP and that range is trusted; "
            "when this xfail starts passing unexpectedly, the gap is fixed."
        ),
        strict=True,
    )
    def test_rotating_the_header_should_not_change_the_bucket(self):
        """Written as a failing expectation on purpose.

        With '*' the middleware hands us a different scope["client"] for each
        forged header, so each request lands in its own bucket and the limit is
        bypassed. This test models that: it FAILS today (correctly recorded as
        xfail) and will start passing the moment the trusted range is set —
        pytest reports strict xpass as an error, so nobody has to remember to
        come back and check.
        """
        # Under '*', uvicorn resolves whatever the attacker put first.
        first = _client_ip(request_with(("9.9.9.1", 1)))
        second = _client_ip(request_with(("9.9.9.2", 1)))
        assert first == second, "rotating the forged hop still yields new buckets"
