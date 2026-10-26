"""A broker's own Callback reminder, and the WhatsApp it turns into (0122).

THE ONE THAT MATTERS is the timing pair: a callback is created days before it
is due, so the drain must hold it until then. Until 0122 the claim filtered on
status alone — survivable for ladder rungs, which the sweep creates on the
morning they are due, and completely wrong for a callback. Without that filter
a broker choosing "next Tuesday" got a WhatsApp within the minute.

The rest guard the other half of the split: a callback must NOT inherit the
ladder's renewal rules, and an unconfigured callback template must not stop
renewals going out.
"""

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

import whatsapp_client
from services import renewal_notification as renewal

_TZ = ZoneInfo("America/Toronto")


def _now() -> datetime:
    return datetime.now(_TZ)


def callback_row(
    *,
    due_in_hours=-1,
    status="Open",
    broker_id="b1",
    owner="b1",
    opted_in="2026-08-30T00:00:00Z",
    answered=2,
    no_answer=1,
    stage="New",
    renewal_off=False,
    with_renewal_date=False,
    attempt_count=0,
):
    """One queued callback, joined as the repository returns it.

    Defaults to an hour PAST due — the ordinary case for a row the drain has
    just claimed.
    """
    due = _now() + timedelta(hours=due_in_hours)
    driver = []
    if with_renewal_date:
        driver = [
            {
                "renewal_date": (_now().date() + timedelta(days=200)).isoformat(),
                "is_principal": True,
            }
        ]
    return {
        "id": "n1",
        "reminder_id": "r1",
        "attempt_count": attempt_count,
        "fallback_email_sent_at": None,
        "reminder": {
            "id": "r1",
            "status": status,
            "broker_id": broker_id,
            "note": "Call back - no answer",
            "kind": "Callback",
            "due_at": due.isoformat(),
            # Null on everything a broker sets by hand. This is what keeps a
            # callback out of the ladder's queueing branch and its rules.
            "auto_rule": None,
            "auto_for_renewal_date": None,
            "lead": {
                "id": "l1",
                "name": "Sarah Mitchell",
                "phone": "416-555-0142",
                "phone_e164": "+14165550142",
                "stage": stage,
                "assigned_broker_id": owner,
                "renewal_reminders_off": renewal_off,
                "answered_count": answered,
                "no_answer_count": no_answer,
                "driver": driver,
            },
            "broker": {
                "name": "Alex Broker",
                "email": "broker@example.com",
                "phone_e164": "+14165550199",
                "whatsapp_opted_in_at": opted_in,
            },
        },
    }


class FakeRepo:
    """Stands in for the repository, recording what the drain decided.

    claim_pending mimics the real query's due_at filter, because that filter IS
    the behaviour under test — a fake that returned everything would let the
    timing test pass against a drain that had lost it.
    """

    def __init__(self, rows):
        self._rows = rows
        self.submitted, self.cancelled, self.failed = [], [], []

    def claim_pending(self, kind="callback", now=None, limit=25):
        if kind != "callback":
            return type("R", (), {"data": []})()
        cutoff = datetime.fromisoformat(now) if now else _now()

        def due_of(r):
            d = datetime.fromisoformat(r["reminder"]["due_at"])
            # Naive read as UTC, mirroring the service. The real filter is a
            # Postgres timestamptz comparison and never sees Python's TypeError.
            return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

        due = [r for r in self._rows if due_of(r) <= cutoff]
        return type("R", (), {"data": due})()

    def mark_submitted(self, row_id, message_sid):
        self.submitted.append((row_id, message_sid))

    def mark_cancelled(self, row_id, reason):
        self.cancelled.append((row_id, reason))

    def mark_failed(self, row_id, reason, *, attempt_count, exhausted):
        self.failed.append(
            {
                "id": row_id,
                "reason": reason,
                "attempt_count": attempt_count,
                "exhausted": exhausted,
            }
        )


@pytest.fixture
def drain_ready(monkeypatch):
    """Callback template configured, renewal NOT — the asymmetry is the point."""
    monkeypatch.setattr(whatsapp_client, "is_callback_configured", lambda: True)
    monkeypatch.setattr(whatsapp_client, "is_renewal_configured", lambda: False)
    monkeypatch.setattr(
        whatsapp_client, "callback_template", lambda: ("call_reminder", "en")
    )
    sent = []

    def sender(to, variables, **kwargs):
        sent.append({"to": to, "variables": variables, **kwargs})
        return "wamid.TEST"

    monkeypatch.setattr(whatsapp_client, "send_template", sender)
    return sent


def _run(monkeypatch, repo):
    monkeypatch.setattr(renewal, "repo", repo)
    return renewal.send_pending()


class TestTiming:
    """The bug 0122 exists to prevent."""

    def test_a_callback_due_tomorrow_is_not_sent(self, monkeypatch, drain_ready):
        repo = FakeRepo([callback_row(due_in_hours=24)])
        _run(monkeypatch, repo)

        assert drain_ready == [], "a reminder set for tomorrow went out today"
        assert repo.submitted == []
        # Not cancelled either — it is still wanted, just not yet.
        assert repo.cancelled == []

    def test_a_callback_past_due_is_sent_once(self, monkeypatch, drain_ready):
        repo = FakeRepo([callback_row(due_in_hours=-1)])
        _run(monkeypatch, repo)

        assert len(drain_ready) == 1
        assert len(repo.submitted) == 1

    def test_a_naive_due_at_does_not_abort_the_pass(self, monkeypatch, drain_ready):
        """The due_at check runs BEFORE the per-row try block. A naive
        timestamp compared to an aware `now` raises TypeError, which would
        abort every row in the batch, every minute, until someone noticed."""
        row = callback_row(due_in_hours=-1)
        # Naive AND in UTC, which is what the column stores. Stripping the
        # offset off a Toronto time instead would read four hours late and
        # fail as "Not due yet" for the wrong reason.
        naive_utc = datetime.now(timezone.utc) - timedelta(hours=1)
        row["reminder"]["due_at"] = naive_utc.replace(tzinfo=None).isoformat()
        repo = FakeRepo([row])
        _run(monkeypatch, repo)

        assert len(repo.submitted) == 1

    def test_it_goes_to_the_broker_not_the_customer(self, monkeypatch, drain_ready):
        repo = FakeRepo([callback_row()])
        _run(monkeypatch, repo)

        assert drain_ready[0]["to"] == "+14165550199"  # broker
        assert drain_ready[0]["to"] != "+14165550142"  # NOT the customer


class TestTemplate:
    def test_variables_match_the_approved_shape(self, monkeypatch, drain_ready):
        repo = FakeRepo([callback_row(answered=2, no_answer=1)])
        _run(monkeypatch, repo)

        assert drain_ready[0]["variables"] == {
            "1": "Sarah Mitchell",
            "2": "+14165550142",
            "3": "2",
            "4": "1",
        }

    def test_zero_counts_are_sent_as_zero(self, monkeypatch, drain_ready):
        """A reminder can be set without touching a counter. Meta rejects an
        empty variable, so both must still be present."""
        repo = FakeRepo([callback_row(answered=0, no_answer=0)])
        _run(monkeypatch, repo)

        assert drain_ready[0]["variables"]["3"] == "0"
        assert drain_ready[0]["variables"]["4"] == "0"

    def test_no_buttons(self, monkeypatch, drain_ready):
        """call_reminder is approved without buttons, and a template with
        buttons rejects a send that omits them — so the two must agree."""
        repo = FakeRepo([callback_row()])
        _run(monkeypatch, repo)

        assert not drain_ready[0].get("buttons")


class TestDoesNotInheritRenewalRules:
    """A callback has nothing to do with a renewal date."""

    def test_sends_with_no_renewal_date_at_all(self, monkeypatch, drain_ready):
        repo = FakeRepo([callback_row(with_renewal_date=False)])
        _run(monkeypatch, repo)

        assert len(repo.submitted) == 1, "the ladder's renewal-date rule cancelled it"

    def test_renewal_reminders_off_does_not_suppress_it(self, monkeypatch, drain_ready):
        """0119's Stop Reminder silences the LADDER for a lead. It is not a
        broker saying 'never remind me to call this person'."""
        repo = FakeRepo([callback_row(renewal_off=True)])
        _run(monkeypatch, repo)

        assert len(repo.submitted) == 1
        assert repo.cancelled == []

    def test_sends_in_a_stage_the_ladder_would_refuse(self, monkeypatch, drain_ready):
        repo = FakeRepo([callback_row(stage="Sold")])
        _run(monkeypatch, repo)

        assert len(repo.submitted) == 1


class TestStillRefusesWhatItShould:
    def test_a_closed_reminder_is_cancelled(self, monkeypatch, drain_ready):
        repo = FakeRepo([callback_row(status="Done")])
        _run(monkeypatch, repo)

        assert repo.submitted == []
        assert "closed" in repo.cancelled[0][1]

    def test_a_reassigned_lead_is_cancelled(self, monkeypatch, drain_ready):
        repo = FakeRepo([callback_row(broker_id="b1", owner="b2")])
        _run(monkeypatch, repo)

        assert repo.submitted == []
        assert "reassigned" in repo.cancelled[0][1]

    def test_an_unopted_broker_is_cancelled(self, monkeypatch, drain_ready):
        repo = FakeRepo([callback_row(opted_in=None)])
        _run(monkeypatch, repo)

        assert repo.submitted == []
        assert "opted in" in repo.cancelled[0][1]


class TestConfigurationIsIndependent:
    def test_unconfigured_callback_does_not_stop_renewals(self, monkeypatch):
        """The whole reason the two are claimed separately."""
        monkeypatch.setattr(whatsapp_client, "is_callback_configured", lambda: False)
        monkeypatch.setattr(whatsapp_client, "is_renewal_configured", lambda: True)
        monkeypatch.setattr(
            whatsapp_client, "renewal_template", lambda: ("renewal_nudge", "en")
        )
        monkeypatch.setattr(whatsapp_client, "send_template", lambda *a, **k: "wamid.X")

        repo = FakeRepo([callback_row()])
        result = _run(monkeypatch, repo)

        # The pass RAN (renewals are on) but claimed no callbacks.
        assert result["checked"] == 0
        assert repo.submitted == []

    def test_neither_configured_is_a_no_op(self, monkeypatch):
        monkeypatch.setattr(whatsapp_client, "is_callback_configured", lambda: False)
        monkeypatch.setattr(whatsapp_client, "is_renewal_configured", lambda: False)

        repo = FakeRepo([callback_row()])
        result = _run(monkeypatch, repo)

        assert result == {"checked": 0}
        assert repo.submitted == []
