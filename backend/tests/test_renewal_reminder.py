"""The renewal ladder: which rungs fire, and which queued nudges actually send.

Two halves, matching the two halves of the feature:

  - the SWEEP decides a rung is due (services/reminder.py)
  - the DRAIN decides a queued rung still deserves to go
    (services/renewal_notification.py)

Offline, like tests/test_whatsapp.py: a hand-rolled fake repository records the
decisions, and Meta is never called. There is no conftest in this backend, so
the fakes live here.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest

import whatsapp_client
from services import reminder as reminder_service
from services import renewal_notification as renewal

_TZ = ZoneInfo("America/Toronto")
_LADDER = ("renewal_30", "renewal_15", "renewal_5")


def _today() -> date:
    return datetime.now(_TZ).date()


# ── the sweep: which rung, if any, fires at a given distance ─────────────
class FakeSweepRepo:
    """Stands in for repositories.reminder for the sweep.

    leads_due_for_renewal_reminder is asked once per rule with that rule's
    stages, so the fake filters the same way the database would.
    """

    def __init__(self, leads):
        self._leads = leads
        self.inserted = []

    def leads_due_for_renewal_reminder(self, stages):
        rows = [lead for lead in self._leads if lead["stage"] in stages]
        return type("R", (), {"data": rows})()

    def insert_auto(self, values):
        self.inserted.append(values)
        return type("R", (), {"data": [values]})()


def lead_at(delta: int, *, stage="New", lead_id="l1", off=False):
    """A lead whose principal driver renews `delta` days from today."""
    renewal_date = _today() + timedelta(days=delta)
    return {
        "id": lead_id,
        "name": "Jane Smith",
        "stage": stage,
        "assigned_broker_id": "b1",
        "renewal_reminders_off": off,
        "driver": [{"renewal_date": renewal_date.isoformat(), "is_principal": True}],
    }


def sweep_with(monkeypatch, leads) -> FakeSweepRepo:
    repo = FakeSweepRepo(leads)
    monkeypatch.setattr(reminder_service, "repo", repo)
    reminder_service.sweep()
    return repo


class TestBands:
    """EXACTLY ONE RUNG PER DAY, or a lead entered late gets a burst.

    The bands are the core of the design: an earlier draft used
    `delta <= days_before`, which is true for two rungs at delta 12.
    """

    @pytest.mark.parametrize("delta", range(0, 32))
    def test_one_rung_at_most_for_an_active_lead(self, monkeypatch, delta):
        repo = sweep_with(monkeypatch, [lead_at(delta)])
        rungs = [v["auto_rule"] for v in repo.inserted if v["auto_rule"] in _LADDER]
        assert len(rungs) <= 1, f"delta {delta} matched {rungs}"
        # Everything 0-30 is inside some band; 31 is past the top.
        assert len(rungs) == (1 if delta <= 30 else 0), f"delta {delta} gave {rungs}"

    @pytest.mark.parametrize(
        "delta,expected",
        [(30, "renewal_30"), (16, "renewal_30"),
         (15, "renewal_15"), (6, "renewal_15"),
         (5, "renewal_5"), (0, "renewal_5")],
    )
    def test_band_edges(self, monkeypatch, delta, expected):
        repo = sweep_with(monkeypatch, [lead_at(delta)])
        assert [v["auto_rule"] for v in repo.inserted] == [expected]

    def test_nothing_past_the_top_or_below_zero(self, monkeypatch):
        for delta in (31, 60, -1, -30):
            repo = sweep_with(monkeypatch, [lead_at(delta)])
            assert repo.inserted == [], f"delta {delta} should fire nothing"

    def test_no_renewal_date_is_silent(self, monkeypatch):
        lead = lead_at(10)
        lead["driver"] = []
        repo = sweep_with(monkeypatch, [lead])
        assert repo.inserted == []


class TestCatchUp:
    """A date first seen late still gets ONE nudge, saying the real number."""

    def test_note_uses_the_actual_delta_not_the_rung_name(self, monkeypatch):
        repo = sweep_with(monkeypatch, [lead_at(12)])
        assert len(repo.inserted) == 1
        assert repo.inserted[0]["auto_rule"] == "renewal_15"
        # 12, not 15 — the broker is looking at the same calendar.
        assert "12 days" in repo.inserted[0]["note"]

    def test_today_and_tomorrow_read_naturally(self, monkeypatch):
        assert "today" in reminder_service._note(0)
        assert "tomorrow" in reminder_service._note(1)
        assert "2 days" in reminder_service._note(2)


class TestWhichLeads:
    """Sold and Issued are done; Lost has its own rule."""

    @pytest.mark.parametrize("stage", ["New", "In Progress", "Quote Done", "Pending"])
    def test_active_stages_get_the_ladder(self, monkeypatch, stage):
        repo = sweep_with(monkeypatch, [lead_at(20, stage=stage)])
        assert [v["auto_rule"] for v in repo.inserted] == ["renewal_30"]

    @pytest.mark.parametrize("stage", ["Sold", "Issued"])
    def test_closed_stages_get_nothing(self, monkeypatch, stage):
        repo = sweep_with(monkeypatch, [lead_at(20, stage=stage)])
        assert repo.inserted == []

    def test_lost_still_fires_its_own_rule_at_thirty(self, monkeypatch):
        """0104's behaviour, kept. Dropping it would silently end the
        re-approach of declined leads near their renewal."""
        repo = sweep_with(monkeypatch, [lead_at(30, stage="Lost")])
        assert [v["auto_rule"] for v in repo.inserted] == ["renewal_lost"]
        # Its own fixed wording, not the countdown.
        assert "may be open to a quote" in repo.inserted[0]["note"]

    def test_lost_is_exact_day_not_a_band(self, monkeypatch):
        for delta in (29, 31, 20):
            repo = sweep_with(monkeypatch, [lead_at(delta, stage="Lost")])
            assert repo.inserted == [], f"Lost at {delta} should not fire"

    def test_opted_out_lead_gets_no_new_rung(self, monkeypatch):
        repo = sweep_with(monkeypatch, [lead_at(20, off=True)])
        assert repo.inserted == []


# ── the drain: does a queued row still deserve to send? ──────────────────
class FakeDrainRepo:
    def __init__(self, rows):
        self._rows = rows
        self.submitted, self.cancelled, self.failed = [], [], []

    def claim_pending(self, kind="renewal", now=None, limit=25):
        # The real one filters by kind and due_at in PostgREST (0122). These
        # fixtures are all ladder rungs, so a callback pass sees nothing —
        # which is what the real query would do too.
        if kind == "callback":
            return type("R", (), {"data": []})()
        return type("R", (), {"data": self._rows})()

    def mark_submitted(self, row_id, message_sid):
        self.submitted.append((row_id, message_sid))

    def mark_cancelled(self, row_id, reason):
        self.cancelled.append((row_id, reason))

    def mark_failed(self, row_id, reason, *, attempt_count, exhausted):
        self.failed.append({"id": row_id, "reason": reason,
                            "attempt_count": attempt_count, "exhausted": exhausted})


def queued(
    *,
    delta=15,
    queued_for=None,
    status="Open",
    stage="New",
    broker_id="b1",
    owner="b1",
    opted_in="2026-08-30T00:00:00Z",
    off=False,
    attempt_count=0,
):
    """One renewal_notification row, joined as the repository returns it.

    `queued_for` defaults to the driver's current date — the ordinary case where
    nothing has changed since the rung was made.
    """
    renewal_date = _today() + timedelta(days=delta)
    return {
        "id": "n1",
        "reminder_id": "r1",
        "attempt_count": attempt_count,
        "fallback_email_sent_at": None,
        "reminder": {
            "id": "r1",
            "status": status,
            "broker_id": broker_id,
            "note": "Renewal in 15 days — worth a call",
            "auto_rule": "renewal_15",
            "auto_for_renewal_date": queued_for or renewal_date.isoformat(),
            "lead": {
                "id": "l1",
                "name": "Jane Smith",
                "phone": "416-555-0134",
                "phone_e164": "+14165550134",
                "stage": stage,
                "assigned_broker_id": owner,
                "renewal_reminders_off": off,
                "driver": [
                    {"renewal_date": renewal_date.isoformat(), "is_principal": True}
                ],
            },
            "broker": {
                "name": "Alex Broker",
                "email": "broker@example.com",
                "phone_e164": "+14165550199",
                "whatsapp_opted_in_at": opted_in,
            },
        },
    }


@pytest.fixture
def drain_ready(monkeypatch):
    monkeypatch.setattr(whatsapp_client, "is_renewal_configured", lambda: True)
    monkeypatch.setattr(
        whatsapp_client, "renewal_template", lambda: ("renewal_nudge", "en")
    )
    sent = []

    def sender(to, variables, **kwargs):
        sent.append({"to": to, "variables": variables, **kwargs})
        return "wamid.TEST"

    monkeypatch.setattr(whatsapp_client, "send_template", sender)
    return sent


def drain(monkeypatch, rows) -> FakeDrainRepo:
    repo = FakeDrainRepo(rows)
    monkeypatch.setattr(renewal, "repo", repo)
    renewal.send_pending()
    return repo


class TestDrainSends:
    def test_a_good_row_sends(self, monkeypatch, drain_ready):
        repo = drain(monkeypatch, [queued()])
        assert repo.submitted == [("n1", "wamid.TEST")]
        assert repo.cancelled == []

    def test_it_goes_to_the_broker_not_the_customer(self, monkeypatch, drain_ready):
        drain(monkeypatch, [queued()])
        assert drain_ready[0]["to"] == "+14165550199"

    def test_variables_are_broker_customer_phone_days(self, monkeypatch, drain_ready):
        drain(monkeypatch, [queued(delta=15)])
        assert drain_ready[0]["variables"] == {
            "1": "Alex Broker",       # the BROKER, not the customer
            "2": "Jane Smith",
            "3": "+14165550134",
            "4": "15",
        }

    def test_days_are_recomputed_at_send_time(self, monkeypatch, drain_ready):
        """A row queued at 30 that goes out late must say the real number."""
        drain(monkeypatch, [queued(delta=28)])
        assert drain_ready[0]["variables"]["4"] == "28"

    def test_both_button_components_are_sent(self, monkeypatch, drain_ready):
        """A template with buttons rejects a send that omits their components."""
        drain(monkeypatch, [queued()])
        buttons = drain_ready[0]["buttons"]
        assert [b["sub_type"] for b in buttons] == ["quick_reply", "url"]
        assert [b["index"] for b in buttons] == ["0", "1"]
        # The URL parameter is the SUFFIX only — the base lives in the template.
        assert buttons[1]["parameters"][0]["text"] == "l1"

    def test_the_renewal_template_is_named(self, monkeypatch, drain_ready):
        drain(monkeypatch, [queued()])
        assert drain_ready[0]["template"] == "renewal_nudge"


class TestDrainCancels:
    """Queue time proves nothing about send time."""

    def test_renewal_date_changed_is_a_different_cycle(self, monkeypatch, drain_ready):
        """THE SUBTLE ONE. A corrected date makes a NEW reminder under
        idx_reminder_auto_cycle, so both can sit in the window at once. A live
        delta alone would send both."""
        stale = (_today() + timedelta(days=20)).isoformat()
        repo = drain(monkeypatch, [queued(delta=15, queued_for=stale)])
        assert repo.submitted == []
        assert "renewal date changed" in repo.cancelled[0][1]

    def test_opted_out_after_queueing(self, monkeypatch, drain_ready):
        repo = drain(monkeypatch, [queued(off=True)])
        assert repo.submitted == []
        assert "stopped renewal reminders" in repo.cancelled[0][1]

    @pytest.mark.parametrize("stage", ["Sold", "Issued", "Lost"])
    def test_stage_left_the_pipeline(self, monkeypatch, drain_ready, stage):
        repo = drain(monkeypatch, [queued(stage=stage)])
        assert repo.submitted == []
        assert stage in repo.cancelled[0][1]

    def test_reassigned_between_queue_and_send(self, monkeypatch, drain_ready):
        repo = drain(monkeypatch, [queued(broker_id="b1", owner="b2")])
        assert repo.submitted == []
        assert "reassigned" in repo.cancelled[0][1]

    @pytest.mark.parametrize("status", ["Done", "Dismissed"])
    def test_broker_already_closed_it(self, monkeypatch, drain_ready, status):
        repo = drain(monkeypatch, [queued(status=status)])
        assert repo.submitted == []
        assert "already closed" in repo.cancelled[0][1]

    def test_consent_withdrawn(self, monkeypatch, drain_ready):
        repo = drain(monkeypatch, [queued(opted_in=None)])
        assert repo.submitted == []
        assert "not opted in" in repo.cancelled[0][1]

    def test_stale_backlog_past_the_window(self, monkeypatch, drain_ready):
        """A row stuck through an outage must not fire after the renewal."""
        repo = drain(monkeypatch, [queued(delta=-3)])
        assert repo.submitted == []
        assert "outside the window" in repo.cancelled[0][1]


class TestDrainKillSwitch:
    def test_unconfigured_template_is_a_no_op(self, monkeypatch):
        monkeypatch.setattr(whatsapp_client, "is_renewal_configured", lambda: False)
        assert renewal.send_pending() == {"checked": 0}


class TestTemplateConfigIsIndependent:
    """One template's absence must not disable the other's drain."""

    def test_renewal_configured_ignores_the_lead_alert_template(self, monkeypatch):
        monkeypatch.setattr(whatsapp_client, "_ACCESS_TOKEN", "tok")
        monkeypatch.setattr(whatsapp_client, "_PHONE_NUMBER_ID", "123")
        monkeypatch.setattr(whatsapp_client, "_TEMPLATE_NAME", "")
        monkeypatch.setattr(whatsapp_client, "_RENEWAL_TEMPLATE_NAME", "renewal_nudge")
        assert whatsapp_client.is_renewal_configured()
        assert not whatsapp_client.is_configured()

    def test_lead_alert_configured_ignores_the_renewal_template(self, monkeypatch):
        monkeypatch.setattr(whatsapp_client, "_ACCESS_TOKEN", "tok")
        monkeypatch.setattr(whatsapp_client, "_PHONE_NUMBER_ID", "123")
        monkeypatch.setattr(whatsapp_client, "_TEMPLATE_NAME", "lead_alert")
        monkeypatch.setattr(whatsapp_client, "_RENEWAL_TEMPLATE_NAME", "")
        assert whatsapp_client.is_configured()
        assert not whatsapp_client.is_renewal_configured()

    def test_no_transport_disables_both(self, monkeypatch):
        monkeypatch.setattr(whatsapp_client, "_ACCESS_TOKEN", "")
        monkeypatch.setattr(whatsapp_client, "_TEMPLATE_NAME", "lead_alert")
        monkeypatch.setattr(whatsapp_client, "_RENEWAL_TEMPLATE_NAME", "renewal_nudge")
        assert not whatsapp_client.is_configured()
        assert not whatsapp_client.is_renewal_configured()
