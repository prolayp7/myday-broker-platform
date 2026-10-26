"""The admin alert for conversion signals that failed for good (0113).

WHAT IS ACTUALLY BEING TESTED HERE is the word "once". Sending an email is the
easy half; the design is entirely about NOT sending it again on the next drain
pass, and again fifteen minutes later, for the same failures. Migration 0111 made
the argument on the WhatsApp channel: "a channel that sends noise is a channel
people mute. Losing the real alerts to habituation is a far worse outcome than
missing this one."

So every test below is about the stamp — when it is written, when it is withheld,
and which rows it covers. A test suite that only proved "an email goes out" would
pass against a level-triggered implementation that mails every fifteen minutes
forever, which is the failure mode this module exists to avoid.

Stubbed at the boundaries (the repository and email_client); the module under
test runs as written.
"""

import sys

import pytest

sys.path.insert(0, ".")

from services import meta_signal_alert as alert  # noqa: E402


def _row(signal_id, *, code=190, name="Asha Menon", event="CompleteRegistration"):
    return {
        "id": signal_id,
        "event_name": event,
        "error_code": code,
        "error_message": "Error validating access token",
        "attempt_count": 1,
        "created_at": "2026-09-13T12:00:00+00:00",
        "dataset_target": "legacy",
        "lead": {"id": f"lead-{signal_id}", "name": name},
    }


class _Result:
    def __init__(self, data):
        self.data = data


@pytest.fixture
def wired(monkeypatch):
    """Everything stubbed, configured and working. Each test breaks one thing."""
    state = {"sent": [], "stamped": [], "rows": [_row("s1")]}

    monkeypatch.setattr(
        alert.repo, "list_unalerted_failures",
        lambda limit=50: _Result(state["rows"]),
    )
    monkeypatch.setattr(
        alert.repo, "mark_failures_alerted",
        lambda ids: state["stamped"].append(list(ids)),
    )
    monkeypatch.setattr(alert.email_client, "is_configured", lambda: True)
    monkeypatch.setattr(
        alert.email_client, "send",
        lambda *, to, subject, text: state["sent"].append(
            {"to": to, "subject": subject, "text": text}
        ),
    )
    monkeypatch.setattr(alert, "_ALERT_TO", "admin@example.com")
    return state


class TestItTellsSomeoneOnce:
    def test_a_new_failure_is_alerted_and_stamped(self, wired):
        out = alert.alert_new_failures()

        assert out == {"alerted": 1}
        assert len(wired["sent"]) == 1
        assert wired["stamped"] == [["s1"]]

    def test_nothing_to_report_sends_nothing(self, wired):
        """No email on an empty queue. A heartbeat saying "all fine" every
        fifteen minutes is the same habituation problem from the other side."""
        wired["rows"] = []

        assert alert.alert_new_failures() == {"alerted": 0}
        assert wired["sent"] == []
        assert wired["stamped"] == []

    def test_only_the_rows_described_are_stamped(self, wired):
        """THE BLANKET-UPDATE BUG, GUARDED. Stamping by re-running the filter
        ("all Failed rows where failure_alerted_at is null") would also silence a
        failure that arrived between the read and the write — a row this email
        never mentioned, now permanently marked as reported."""
        wired["rows"] = [_row("s1"), _row("s2"), _row("s3")]

        alert.alert_new_failures()

        assert wired["stamped"] == [["s1", "s2", "s3"]]


class TestItDoesNotStampWhatItDidNotSend:
    def test_a_send_failure_leaves_the_rows_unstamped(self, wired, monkeypatch):
        """THE ORDERING THAT MATTERS. Stamp-then-send would lose the alert
        entirely when the send fails: the rows look reported, and the failure is
        never mentioned again. Read-send-stamp means a failed send simply tries
        again next pass."""
        def _boom(*, to, subject, text):
            raise alert.email_client.EmailError("Resend is down")

        monkeypatch.setattr(alert.email_client, "send", _boom)

        out = alert.alert_new_failures()

        assert out["alerted"] == 0
        assert "error" in out
        assert wired["stamped"] == [], "nothing may be stamped after a failed send"

    def test_an_unconfigured_channel_leaves_the_rows_unstamped(self, wired, monkeypatch):
        """Not an error — an environment without Resend credentials has no alert
        channel, the same stance services/whatsapp_fallback.py takes.

        CRITICALLY, still unstamped: configuring the channel later must surface
        these failures, rather than starting from a silence that has already
        swallowed every one of them."""
        monkeypatch.setattr(alert.email_client, "is_configured", lambda: False)

        out = alert.alert_new_failures()

        assert out["unconfigured"] is True
        assert wired["sent"] == []
        assert wired["stamped"] == []

    def test_a_missing_recipient_also_leaves_them_unstamped(self, wired, monkeypatch):
        """Resend configured but CAPI_ALERT_EMAIL unset. Same outcome: no
        channel, nothing claimed."""
        monkeypatch.setattr(alert, "_ALERT_TO", "")

        out = alert.alert_new_failures()

        assert out["unconfigured"] is True
        assert wired["stamped"] == []

    def test_it_never_raises_into_the_drain(self, wired, monkeypatch):
        """Called from inside the drain pass, which has rows to finish sending.
        An alerter that could raise would turn a reporting gap into a stalled
        queue — so even an unmodelled failure is swallowed."""
        def _boom(limit=50):
            raise RuntimeError("supabase unreachable")

        monkeypatch.setattr(alert.repo, "list_unalerted_failures", _boom)

        assert alert.alert_new_failures() == {"alerted": 0}


class TestTheMessageSaysWhatToDo:
    def test_a_dead_token_is_named_in_the_subject(self, wired):
        """A subject saying "3 signals failed" prompts a click; one saying the
        token is dead prompts the fix, which is the point of sending it."""
        alert.alert_new_failures()

        subject = wired["sent"][0]["subject"]
        assert "token" in subject.lower()

    def test_mixed_codes_do_not_claim_a_single_cause(self, wired):
        """Two different failures share no single diagnosis, and inventing one
        would send an admin after the wrong thing."""
        wired["rows"] = [_row("s1", code=190), _row("s2", code=100)]

        alert.alert_new_failures()

        subject = wired["sent"][0]["subject"]
        assert "token" not in subject.lower()
        assert "2 conversion signal(s) failed" in subject

    def test_the_body_says_the_leads_are_unaffected(self, wired):
        """A CAPI failure is a reporting problem, not an operational one — the
        broker has the lead and is working it. An alert that reads like lost
        leads would send someone chasing brokers for no reason."""
        alert.alert_new_failures()

        assert "leads themselves are unaffected" in wired["sent"][0]["text"]

    def test_the_body_names_the_lead(self, wired):
        """Opaque signal ids tell an admin nothing about whose conversion was
        lost."""
        alert.alert_new_failures()

        assert "Asha Menon" in wired["sent"][0]["text"]

    def test_a_long_list_is_summarised_not_dumped(self, wired):
        """Past a point the count IS the finding, and a wall of rows is a mail
        nobody reads."""
        wired["rows"] = [_row(f"s{i}", name=f"Lead {i}") for i in range(40)]

        alert.alert_new_failures()

        text = wired["sent"][0]["text"]
        assert "and 25 more" in text
        assert "Lead 39" not in text, "past the cap, rows are counted not listed"
        assert wired["stamped"][0] == [f"s{i}" for i in range(40)], (
            "every row is still stamped, even the ones not listed individually"
        )

    def test_the_bulk_recovery_is_mentioned_for_a_dead_token(self, wired):
        """The fix for code 190 is a specific button, and an admin who does not
        know it exists will retry rows one at a time."""
        alert.alert_new_failures()

        assert "Retry dead-token failures" in wired["sent"][0]["text"]
