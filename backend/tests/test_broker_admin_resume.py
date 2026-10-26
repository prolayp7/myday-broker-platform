"""The ADMIN resume endpoint — the decisions Python makes, not SQL.

Sibling to test_broker_self_pause.py, which covers the broker doing this to
themselves. The allocation behaviour and the pause clock live in
tests/test_lead_assignment.py against a real database; this file covers what
only Python decides: who may do it, in which direction, what gets recorded,
and what comes back when the drain fails after the resume was already saved.

WHY THIS ENDPOINT EXISTS AT ALL. Migration 0105 made self_paused persist until
a person clears it. A broker who is away and unreachable — the exact case the
feature was built for — cannot clear their own, so without an admin path the
switch is a trap that needs database access to escape.

THE ONE-DIRECTION RULE IS THE POINT OF HALF THIS FILE. An admin may CLEAR a
broker's own pause and may never SET it. Setting it is what
PATCH /brokers/{id}/block is for, and that route records a reason, an actor and
a timestamp. If an admin could also write self_paused, "away sick" and "stopped
by an admin" would become the same state in the database and the question "why
did this broker receive nothing" would stop having an answer. The guarantee is
structural — resume_broker_leads takes no `paused` argument — and it is
asserted here so a later refactor cannot quietly add one.

No database and no TestClient — the service takes its repositories as module
globals, so monkeypatching them reaches the decision directly.
"""

import os
import uuid

import pytest

os.environ.setdefault(
    "JWT_SECRET_KEY",
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
)
if "TOTP_ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet

    os.environ["TOTP_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")

import inspect  # noqa: E402

from fastapi import HTTPException  # noqa: E402

from repositories import broker as broker_repo  # noqa: E402
from services import broker as service  # noqa: E402

BROKER_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
ADMIN_ID = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")
GROUP_ID = "99999999-8888-7777-6666-555555555555"
PAUSED_SINCE = "2026-01-05T14:30:00+00:00"


class Result:
    """What a supabase .execute() hands back — the object, not the payload.

    Present so the test fails if the service ever forgets `.data`: a response
    object passed to the drain as though it were a group id is exactly the bug
    this shape catches.
    """

    def __init__(self, data):
        self.data = data


def _broker_row(**overrides):
    row = {
        "id": str(BROKER_ID),
        "name": "Away Broker",
        "self_paused": True,
        "self_paused_at": PAUSED_SINCE,
    }
    row.update(overrides)
    return row


@pytest.fixture
def wired(monkeypatch):
    """Stand-ins for the repositories, with every write recorded."""
    calls = {"paused": [], "drained": [], "audit": []}

    monkeypatch.setattr(service, "_reject_if_archived", lambda broker_id: None)
    monkeypatch.setattr(service, "_safe_broker_or_404", lambda broker_id: _broker_row())
    monkeypatch.setattr(
        service.repo,
        "set_self_paused",
        lambda broker_id, paused: (
            calls["paused"].append((broker_id, paused)) or Result(GROUP_ID)
        ),
    )
    monkeypatch.setattr(
        service.lead_repo,
        "drain_pending_for_group",
        lambda group_id: calls["drained"].append(group_id),
    )
    monkeypatch.setattr(
        service.audit_log_repo,
        "insert",
        lambda **kw: calls["audit"].append(kw),
    )
    return calls


class TestItOnlyEverResumes:
    def test_it_clears_the_pause_and_never_sets_one(self, wired):
        service.resume_broker_leads(BROKER_ID, ADMIN_ID)

        assert wired["paused"] == [(str(BROKER_ID), False)]

    def test_there_is_no_way_to_ask_it_to_pause(self):
        """THE STRUCTURAL GUARANTEE, asserted rather than trusted.

        The one-direction rule is enforced by the signature: there is no
        `paused` parameter to pass True to. A refactor that added one would
        make an admin able to stop a broker without recording a reason, which
        is the capability is_blocked_by_admin exists to keep separate — so the
        absence is worth a test rather than a comment.
        """
        params = inspect.signature(service.resume_broker_leads).parameters

        assert "paused" not in params
        assert list(params) == ["broker_id", "admin_broker_id"]


class TestWhatComesBack:
    def test_it_answers_the_availability_shape_not_a_broker_row(self, wired):
        """The narrow shape, and specifically one that CARRIES backlog_drained.

        Returning the broker row instead would look richer and would drop the
        only field saying whether the leads already waiting actually moved.
        The admin is the one person who can go and reassign them, so hiding
        that from them is the one thing this response must not do.
        """
        result = service.resume_broker_leads(BROKER_ID, ADMIN_ID)

        assert result == {"paused": False, "backlog_drained": True}


class TestTheBacklog:
    def test_resuming_drains_the_group_it_was_given(self, wired):
        """The group id comes from the RPC's return value, not a second read."""
        service.resume_broker_leads(BROKER_ID, ADMIN_ID)

        assert wired["drained"] == [GROUP_ID]

    def test_a_failed_drain_is_reported_not_raised(self, wired, monkeypatch):
        """Same contract as the broker's own path.

        The resume is already saved by the time the drain runs. Raising here
        would tell the admin nothing happened when something did, and they
        would press it again on a broker who is already back on.
        """
        def boom(group_id):
            raise RuntimeError("drain exploded")

        monkeypatch.setattr(service.lead_repo, "drain_pending_for_group", boom)

        result = service.resume_broker_leads(BROKER_ID, ADMIN_ID)

        assert result == {"paused": False, "backlog_drained": False}


class TestTheAuditRow:
    def test_it_records_the_admin_as_the_actor(self, wired):
        """One person acting on another — unlike /ready-for-more, which is not
        logged at all. The broker will find leads arriving again without
        having asked, and this row is what answers "who did that"."""
        service.resume_broker_leads(BROKER_ID, ADMIN_ID)

        assert len(wired["audit"]) == 1
        row = wired["audit"][0]
        assert row["actor_broker_id"] == str(ADMIN_ID)
        assert row["entity_type"] == "broker"
        assert row["entity_id"] == str(BROKER_ID)

    def test_it_has_its_own_action_name(self, wired):
        """NOT broker_self_pause_cleared, which was the nightly reset's name
        for an event that can no longer happen (0105). Reusing it would blur
        two different things in the one table somebody reads to find out who
        did what."""
        service.resume_broker_leads(BROKER_ID, ADMIN_ID)

        assert wired["audit"][0]["action"] == "broker_leads_resumed_by_admin"

    def test_it_captures_how_long_they_were_away(self, wired):
        """Read BEFORE the write, because the write clears self_paused_at.
        This row is the only place the length of the absence survives."""
        service.resume_broker_leads(BROKER_ID, ADMIN_ID)

        assert wired["audit"][0]["detail"] == {"paused_since": PAUSED_SINCE}


class TestTheGuards:
    def test_a_broker_who_is_not_paused_is_a_400(self, wired, monkeypatch):
        """Not idempotent-by-silence: an admin pressing this on somebody
        already receiving leads has misread the screen, and a cheerful 200
        would confirm the misreading."""
        monkeypatch.setattr(
            service, "_safe_broker_or_404",
            lambda broker_id: _broker_row(self_paused=False, self_paused_at=None),
        )

        with pytest.raises(HTTPException) as err:
            service.resume_broker_leads(BROKER_ID, ADMIN_ID)

        assert err.value.status_code == 400

    def test_a_no_op_writes_nothing_at_all(self, wired, monkeypatch):
        """The 400 must come BEFORE any write. An audit row for a resume that
        never happened is worse than no row: it says somebody acted."""
        monkeypatch.setattr(
            service, "_safe_broker_or_404",
            lambda broker_id: _broker_row(self_paused=False, self_paused_at=None),
        )

        with pytest.raises(HTTPException):
            service.resume_broker_leads(BROKER_ID, ADMIN_ID)

        assert wired["paused"] == []
        assert wired["audit"] == []
        assert wired["drained"] == []

    def test_an_unknown_broker_is_a_404(self, wired, monkeypatch):
        """Null from the RPC means no such broker, or one in no group."""
        monkeypatch.setattr(
            service.repo, "set_self_paused",
            lambda broker_id, paused: Result(None),
        )

        with pytest.raises(HTTPException) as err:
            service.resume_broker_leads(BROKER_ID, ADMIN_ID)

        assert err.value.status_code == 404


class TestTheColumnIsReadable:
    def test_safe_columns_actually_selects_the_pause_clock(self):
        """SAFE_COLUMNS is an explicit list, so a column it does not name is a
        column no caller can read however correct the migration was. Without
        this the admin screens would render a duration from `undefined`."""
        assert "self_paused_at" in broker_repo.SAFE_COLUMNS

    def test_it_is_not_on_the_auth_path(self):
        """AUTH_COLUMNS is for verifying a password or a TOTP code. Nothing
        about when somebody went on holiday belongs in that query."""
        assert "self_paused_at" not in broker_repo.AUTH_COLUMNS
