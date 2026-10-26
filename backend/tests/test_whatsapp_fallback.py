"""The email fallback: what happens when WhatsApp gives up on a lead.

THE RULE UNDER TEST — every assigned lead reaches its broker through at least
one channel. WhatsApp is primary; email is what happens when WhatsApp has
definitively given up (migration 0110).

THE FAILURE THESE GUARD AGAINST is the 2026-09-01 incident: a broker was
assigned 14 leads and told about almost none of them. Every notification was
queued, submitted, and rejected; the rows sat at Failed and nothing read them.
Nobody noticed for two days, because the leads were still in the CRM so there
was nothing to complain about.

Kept in its own file rather than appended to test_whatsapp.py because it tests a
different decision: not "did the message send" but "does a human still need
telling, and how".
"""

import pytest

import email_client
import whatsapp_client
from services import whatsapp, whatsapp_fallback

from tests.test_whatsapp import FakeRepo, configured, row, use  # noqa: F401


class FakeFallback:
    """Records whether the fallback fired, and for what."""

    def __init__(self):
        self.calls = []

    def send_for_row(self, notification_row, reason):
        self.calls.append((notification_row["id"], reason))

    def send_for_row_id(self, row_id, reason):
        self.calls.append((row_id, reason))


class TestFiresOnlyOnTerminalFailure:
    """The whole design rests on this distinction.

    A transient failure is retried five seconds later and usually succeeds, so
    emailing on one would double-notify on every hiccup — and a channel that
    cries wolf gets muted, which is how the original silence happens again.
    """

    def _run(self, monkeypatch, err, attempt_count=0):
        repo = FakeRepo([row(attempt_count=attempt_count)])
        fallback = FakeFallback()

        def boom(to, variables):
            raise err

        use(monkeypatch, repo, boom)
        monkeypatch.setattr(whatsapp, "whatsapp_fallback", fallback)
        whatsapp.send_pending()
        return repo, fallback

    def test_permanent_error_emails_immediately(self, monkeypatch, configured):
        # Nothing about a permanent error changes on a retry, so the broker is
        # told now rather than after three more pointless attempts.
        err = whatsapp_client.WhatsAppError("not a WhatsApp user", permanent=True)
        repo, fallback = self._run(monkeypatch, err)
        assert repo.failed[0]["exhausted"] is True
        assert len(fallback.calls) == 1

    def test_first_transient_failure_does_not_email(self, monkeypatch, configured):
        # THE IMPORTANT NEGATIVE. The row stays Pending and the drain will try
        # again in five seconds; emailing here would be premature.
        err = whatsapp_client.WhatsAppError("gateway timeout")
        repo, fallback = self._run(monkeypatch, err, attempt_count=0)
        assert repo.failed[0]["exhausted"] is False
        assert fallback.calls == []

    def test_last_transient_failure_emails(self, monkeypatch, configured):
        # attempt_count is already MAX_ATTEMPTS - 1, so this attempt spends the
        # last one and the channel is out of options.
        err = whatsapp_client.WhatsAppError("gateway timeout")
        repo, fallback = self._run(
            monkeypatch, err, attempt_count=whatsapp.MAX_ATTEMPTS - 1
        )
        assert repo.failed[0]["exhausted"] is True
        assert len(fallback.calls) == 1

    def test_unexpected_error_follows_the_same_rule(self, monkeypatch, configured):
        # The unmodelled-exception path is treated as transient, so it must not
        # email on the first surprise either.
        _, fallback = self._run(monkeypatch, RuntimeError("bug"), attempt_count=0)
        assert fallback.calls == []

        _, fallback = self._run(
            monkeypatch, RuntimeError("bug"), attempt_count=whatsapp.MAX_ATTEMPTS - 1
        )
        assert len(fallback.calls) == 1


class TestDoesNotFireOnNonFailures:
    """Delivered and Cancelled are not failures, and must never email."""

    def _run(self, monkeypatch, notification_row):
        repo = FakeRepo([notification_row])
        fallback = FakeFallback()
        use(monkeypatch, repo, lambda to, v: "SM1")
        monkeypatch.setattr(whatsapp, "whatsapp_fallback", fallback)
        whatsapp.send_pending()
        return repo, fallback

    def test_a_successful_send_does_not_email(self, monkeypatch, configured):
        repo, fallback = self._run(monkeypatch, row())
        assert repo.submitted
        assert fallback.calls == []

    def test_a_reassigned_lead_does_not_email_the_old_broker(self, monkeypatch, configured):
        # Cancelled means the lead moved on. The NEW broker gets their own
        # notification; telling the old one about a lead they no longer own
        # would be worse than silence.
        repo, fallback = self._run(monkeypatch, row(broker_id="b1", owner="b2"))
        assert repo.cancelled
        assert fallback.calls == []

    def test_a_broker_without_consent_does_not_email(self, monkeypatch, configured):
        # Also Cancelled, not a failure: WhatsApp was never attempted.
        repo, fallback = self._run(monkeypatch, row(opted_in=None))
        assert repo.cancelled
        assert fallback.calls == []


class TestComposition:
    """What the broker actually reads."""

    def _compose(self, **over):
        r = row(**over)
        return whatsapp_fallback._compose(r["lead"], r["broker"], "code 131026")

    def test_carries_the_three_facts(self):
        subject, body = self._compose()
        assert "Jane Smith" in subject
        assert "Jane Smith" in body
        assert "+14165550134" in body
        assert "2020 or before" in body  # the label, not the raw stored value

    def test_says_why_it_arrived(self):
        # A broker who may already have seen a WhatsApp needs to know instantly
        # whether this is a duplicate.
        subject, body = self._compose()
        assert "WhatsApp" in subject
        assert "could not be delivered" in body

    def test_does_not_leak_the_technical_reason(self):
        # "code 131026" is not actionable by a broker and only undermines
        # confidence in the tool. It lives on the row, for the tracking screen.
        _, body = self._compose()
        assert "131026" not in body

    def test_includes_a_link_when_the_crm_url_is_set(self, monkeypatch):
        monkeypatch.setattr(whatsapp_fallback, "_CRM_URL", "https://crm.example.com")
        _, body = self._compose()
        # There is no /leads/{id} route, so the link is a filtered list search.
        assert "https://crm.example.com/leads?q=%2B14165550134" in body

    def test_omits_the_link_rather_than_printing_a_broken_one(self, monkeypatch):
        monkeypatch.setattr(whatsapp_fallback, "_CRM_URL", "")
        _, body = self._compose()
        assert "Open in MyDay CRM" not in body

    def test_a_missing_licence_answer_still_sends(self):
        # A missing answer must never suppress the notification — the broker
        # still needs to know the lead exists.
        r = row()
        r["lead"]["licensed_in_canada"] = None
        _, body = whatsapp_fallback._compose(r["lead"], r["broker"], "x")
        assert "Not provided" in body


class ClaimRepo:
    """Stands in for the atomic claim, which real Postgres decides."""

    def __init__(self, first_claim_wins=True):
        self.claimed = 0
        self.errors = []
        self._wins = first_claim_wins

    def claim_fallback_email(self, row_id):
        self.claimed += 1
        return self._wins and self.claimed == 1

    def record_fallback_email_error(self, row_id, reason):
        self.errors.append(reason)


class TestNeverSendsTwice:
    """The duplicate guard, which is a CLAIM rather than a check.

    Two paths can reach the same row's terminal state at once — the drain
    spending its last attempt while a delivery webhook reports failed — so the
    guard has to be atomic, not a read-then-write.
    """

    def _send(self, monkeypatch, repo, sent, notification_row=None):
        monkeypatch.setattr(whatsapp_fallback, "repo", repo)
        monkeypatch.setattr(email_client, "is_configured", lambda: True)
        monkeypatch.setattr(email_client, "send", lambda **kw: sent.append(kw) or "re_1")
        whatsapp_fallback.send_for_row(notification_row or row(), "failed")

    def test_the_second_attempt_sends_nothing(self, monkeypatch):
        repo, sent = ClaimRepo(), []
        self._send(monkeypatch, repo, sent)
        self._send(monkeypatch, repo, sent)
        assert len(sent) == 1  # not two emails about one lead

    def test_a_lost_claim_sends_nothing(self, monkeypatch):
        repo, sent = ClaimRepo(first_claim_wins=False), []
        self._send(monkeypatch, repo, sent)
        assert sent == []


class TestFailureIsRecordedNotRaised:
    """A broken fallback must be visible, and must not break its caller.

    Recording the reason is what stops the fallback repeating the original bug:
    without it, a failed email looks exactly like one that was never needed.
    """

    def test_a_provider_error_is_recorded(self, monkeypatch):
        repo = ClaimRepo()
        monkeypatch.setattr(whatsapp_fallback, "repo", repo)
        monkeypatch.setattr(email_client, "is_configured", lambda: True)

        def boom(**kw):
            raise email_client.EmailError("Resend 422: domain not verified")

        monkeypatch.setattr(email_client, "send", boom)
        whatsapp_fallback.send_for_row(row(), "failed")  # must not raise
        assert "domain not verified" in repo.errors[0]

    def test_a_broker_without_an_email_is_recorded(self, monkeypatch):
        repo = ClaimRepo()
        monkeypatch.setattr(whatsapp_fallback, "repo", repo)
        monkeypatch.setattr(email_client, "is_configured", lambda: True)
        monkeypatch.setattr(email_client, "send", lambda **kw: "re_1")
        whatsapp_fallback.send_for_row(row(email=""), "failed")
        assert "no email address" in repo.errors[0]

    def test_an_unconfigured_provider_is_a_silent_skip(self, monkeypatch):
        # No Resend credentials is a deployment choice, not a fault — the same
        # way no Twilio credentials means no WhatsApp. Nothing is recorded, and
        # nothing is claimed.
        repo = ClaimRepo()
        monkeypatch.setattr(whatsapp_fallback, "repo", repo)
        monkeypatch.setattr(email_client, "is_configured", lambda: False)
        whatsapp_fallback.send_for_row(row(), "failed")
        assert repo.errors == []
        assert repo.claimed == 0

    def test_an_unmodelled_error_does_not_escape(self, monkeypatch):
        # The caller is mid-drain or mid-webhook and must finish its own work.
        monkeypatch.setattr(email_client, "is_configured", lambda: True)

        class Exploding:
            def claim_fallback_email(self, row_id):
                raise RuntimeError("supabase down")

        monkeypatch.setattr(whatsapp_fallback, "repo", Exploding())
        whatsapp_fallback.send_for_row(row(), "failed")  # must not raise


class TestEmailClient:
    """The provider client itself."""

    def test_is_not_configured_without_both_key_and_sender(self, monkeypatch):
        for key, sender in [("", "a@b.com"), ("re_1", ""), ("", "")]:
            monkeypatch.setattr(email_client, "_API_KEY", key)
            monkeypatch.setattr(email_client, "_FROM", sender)
            assert email_client.is_configured() is False

        monkeypatch.setattr(email_client, "_API_KEY", "re_1")
        monkeypatch.setattr(email_client, "_FROM", "alerts@policyscanner.ca")
        assert email_client.is_configured() is True

    def test_sending_unconfigured_raises_rather_than_silently_passing(self, monkeypatch):
        monkeypatch.setattr(email_client, "_API_KEY", "")
        with pytest.raises(email_client.EmailError):
            email_client.send(to="a@b.com", subject="s", text="t")

    def test_an_empty_recipient_raises(self, monkeypatch):
        monkeypatch.setattr(email_client, "_API_KEY", "re_1")
        monkeypatch.setattr(email_client, "_FROM", "alerts@policyscanner.ca")
        with pytest.raises(email_client.EmailError):
            email_client.send(to="", subject="s", text="t")
