"""A broker taps "Stop Reminder" on a renewal WhatsApp.

The message arrives as value.messages[] with type "button" — NOT
interactive.button_reply, which is the shape for non-template interactive
messages. Everything here is about resolving that press to the right lead and
refusing it when it is not genuinely the broker's.
"""

import pytest

from services import renewal_inbound


class FakeNotificationRepo:
    def __init__(self, rows):
        self._rows = rows
        self.cancelled_for = []

    def get_by_message_sid(self, message_sid):
        rows = [r for r in self._rows if r.get("message_sid") == message_sid]
        return type("R", (), {"data": rows})()

    def cancel_pending_ladder_for_lead(self, lead_id, reason):
        self.cancelled_for.append((lead_id, reason))


class FakeLeadRepo:
    def __init__(self):
        self.stopped = []

    def set_renewal_reminders_off(self, lead_id, off):
        self.stopped.append((lead_id, off))


def notification(*, sid="wamid.ORIGINAL", broker_phone="+14165550199", lead_id="l1"):
    return {
        "id": "n1",
        "message_sid": sid,
        "reminder": {
            "id": "r1",
            "broker_id": "b1",
            "auto_rule": "renewal_15",
            "lead": {"id": lead_id, "name": "Jane Smith"},
            "broker": {"name": "Alex Broker", "phone_e164": broker_phone},
        },
    }


def press(*, context_id="wamid.ORIGINAL", sender="14165550199", label="Stop Reminder"):
    """The payload Meta sends for a template quick-reply tap.

    `from` has NO plus — that asymmetry against broker.phone_e164 is the bug
    this module's _digits() exists to avoid.
    """
    return {
        "context": {"from": "15550001111", "id": context_id},
        "from": sender,
        "id": "wamid.INBOUND",
        "type": "button",
        "button": {"payload": label, "text": label},
    }


@pytest.fixture
def wired(monkeypatch):
    notif = FakeNotificationRepo([notification()])
    leads = FakeLeadRepo()
    monkeypatch.setattr(renewal_inbound, "repo", notif)
    monkeypatch.setattr(renewal_inbound, "lead_repo", leads)
    return notif, leads


class TestValidPress:
    def test_it_stops_the_ladder(self, wired):
        notif, leads = wired
        assert renewal_inbound.handle_button(press()) == "stopped"
        assert leads.stopped == [("l1", True)]

    def test_it_also_cancels_what_is_already_queued(self, wired):
        """Otherwise a rung sent minutes later contradicts the broker who just
        said stop."""
        notif, leads = wired
        renewal_inbound.handle_button(press())
        assert notif.cancelled_for == [
            ("l1", "The broker stopped renewal reminders for this lead")
        ]

    def test_a_duplicate_delivery_is_harmless(self, wired):
        """Meta delivers at least once. Setting a boolean twice is setting it
        once."""
        notif, leads = wired
        assert renewal_inbound.handle_button(press()) == "stopped"
        assert renewal_inbound.handle_button(press()) == "stopped"
        assert leads.stopped == [("l1", True), ("l1", True)]


class TestSenderNumber:
    """Meta's `from` has no plus; broker.phone_e164 does."""

    @pytest.mark.parametrize(
        "sender", ["14165550199", "+14165550199", "1 (416) 555-0199"]
    )
    def test_the_same_number_in_any_format_is_accepted(self, monkeypatch, sender):
        notif = FakeNotificationRepo([notification()])
        leads = FakeLeadRepo()
        monkeypatch.setattr(renewal_inbound, "repo", notif)
        monkeypatch.setattr(renewal_inbound, "lead_repo", leads)
        assert renewal_inbound.handle_button(press(sender=sender)) == "stopped"

    def test_a_different_broker_is_refused(self, wired):
        """One broker must never silence another's lead."""
        notif, leads = wired
        outcome = renewal_inbound.handle_button(press(sender="14165550000"))
        assert outcome == "sender is not the broker"
        assert leads.stopped == []
        assert notif.cancelled_for == []

    def test_a_missing_sender_is_refused(self, wired):
        notif, leads = wired
        assert renewal_inbound.handle_button(press(sender="")) != "stopped"
        assert leads.stopped == []


class TestUnmatched:
    def test_unknown_context_id_does_nothing(self, wired):
        notif, leads = wired
        outcome = renewal_inbound.handle_button(press(context_id="wamid.OTHER"))
        assert outcome == "no matching notification"
        assert leads.stopped == []

    def test_missing_context_does_nothing(self, wired):
        notif, leads = wired
        message = press()
        del message["context"]
        assert renewal_inbound.handle_button(message) == "no context id"
        assert leads.stopped == []

    def test_an_unrecognised_button_is_ignored(self, wired):
        """Guards a future second button being mistaken for this one."""
        notif, leads = wired
        outcome = renewal_inbound.handle_button(press(label="Something Else"))
        assert outcome == "unrecognised button"
        assert leads.stopped == []


class TestNeverRaises:
    """The webhook must answer 200 whatever this finds, or Meta retries the
    same delivery forever."""

    def test_a_repository_failure_is_swallowed(self, monkeypatch):
        class Exploding(FakeLeadRepo):
            def set_renewal_reminders_off(self, lead_id, off):
                raise RuntimeError("database is down")

        monkeypatch.setattr(
            renewal_inbound, "repo", FakeNotificationRepo([notification()])
        )
        monkeypatch.setattr(renewal_inbound, "lead_repo", Exploding())
        assert renewal_inbound.handle_button(press()) == "failed"

    def test_an_empty_message_is_survivable(self, wired):
        assert renewal_inbound.handle_button({}) == "no context id"
