"""Tests for the uvicorn access-log filter in main.py.

WHY THIS FILE EXISTS. Meta verifies a webhook subscription by calling us with
the shared secret as a query parameter — we cannot change that — and uvicorn's
access log records the full request line including the query string. So the
token was written to Railway's log store on every verification handshake.

The filter drops the query string on that one path. These tests hold it to
both halves of that: the token must go, and every other request line must be
left exactly as it was — a filter that over-redacts would quietly destroy the
access log everyone reads when debugging.
"""

import logging

import pytest

from main import _RedactWebhookQueryString

TOKEN = "SECRET_VERIFY_TOKEN_VALUE"


def record(path: str) -> logging.LogRecord:
    """An access-log record shaped exactly as uvicorn emits one — see the
    access_logger.info call in uvicorn/protocols/http/h11_impl.py."""
    return logging.LogRecord(
        name="uvicorn.access",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg='%s - "%s %s HTTP/%s" %d',
        args=("127.0.0.1:1234", "GET", path, "1.1", 200),
        exc_info=None,
    )


@pytest.fixture
def redact():
    return _RedactWebhookQueryString()


class TestTheTokenIsRedacted:
    def test_the_verify_token_never_reaches_the_line(self, redact):
        r = record(f"/webhooks/meta?hub.mode=subscribe&hub.verify_token={TOKEN}")
        redact.filter(r)
        assert TOKEN not in (r.msg % r.args)

    def test_the_path_is_still_identifiable(self, redact):
        """Redaction must not cost us the ability to see the handshake
        happened, or debugging a failed verification becomes guesswork."""
        r = record(f"/webhooks/meta?hub.verify_token={TOKEN}")
        redact.filter(r)
        line = r.msg % r.args
        assert "/webhooks/meta" in line
        assert "<redacted>" in line
        assert "200" in line

    def test_the_record_is_still_emitted(self, redact):
        """filter() returning False would DROP the line entirely — losing the
        request rather than the secret."""
        assert redact.filter(record(f"/webhooks/meta?hub.verify_token={TOKEN}")) is True


class TestEverythingElseIsUntouched:
    @pytest.mark.parametrize(
        "path",
        [
            "/leads?stage=New&page=2",          # ordinary query string
            "/webhooks/meta",                    # same path, no query at all
            "/webhooks/metadata?token=x",        # prefix collision, different route
            "/brokers/abc/documents/photo/url",
            "/my-day",
        ],
    )
    def test_other_request_lines_are_unchanged(self, redact, path):
        r = record(path)
        before = r.msg % r.args
        redact.filter(r)
        assert (r.msg % r.args) == before

    def test_a_records_own_fields_are_preserved(self, redact):
        r = record(f"/webhooks/meta?hub.verify_token={TOKEN}")
        redact.filter(r)
        assert r.args[0] == "127.0.0.1:1234"
        assert r.args[1] == "GET"
        assert r.args[3] == "1.1"
        assert r.args[4] == 200


class TestItCannotCrashTheLogger:
    """A filter that raises takes down the log line it was meant to clean, and
    uvicorn emits one per request."""

    @pytest.mark.parametrize(
        "args",
        [
            (),
            ("only-one",),
            ("a", "b"),
            ("a", "b", None, "1.1", 200),
            {"mapping": "style"},
            None,
        ],
    )
    def test_unexpected_shapes_pass_through(self, redact, args):
        r = record("/x")
        r.args = args
        assert redact.filter(r) is True
