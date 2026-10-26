"""The broker self-pause endpoint — the decisions Python makes, not SQL.

The allocation behaviour lives in tests/test_lead_assignment.py against a real
database. THIS file covers the part that has burned us twice in review and
cannot be seen from SQL: what the API hands back, and what happens when the
backlog drain fails after the pause was already saved.

THE TRAP, written down because two drafts of this feature got it wrong in
opposite directions. The frontend rolls the switch back on ANY thrown error:

    } catch {
      setPaused(previous)
      toast.error('Could not change that — try again')
    }

So a 5xx raised after `self_paused` was successfully written leaves the
database saying "receiving leads" and the screen saying "paused". The broker
waits for work that is already being assigned to them. That is worse than the
failure it would be reporting, which is why a failed drain must arrive as
`backlog_drained: false` inside a 200.

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

from fastapi import HTTPException  # noqa: E402

from schemas.broker import AvailabilityResponse, SetSelfPausedRequest  # noqa: E402
from services import broker as service  # noqa: E402

BROKER_ID = uuid.UUID("11111111-2222-3333-4444-555555555555")
GROUP_ID = "99999999-8888-7777-6666-555555555555"


class Result:
    """What a supabase .execute() hands back — the object, not the payload.

    Present so the test fails if the service ever forgets `.data` again: a
    response object passed to the drain as though it were a group id is
    exactly the bug this shape catches.
    """

    def __init__(self, data):
        self.data = data


@pytest.fixture
def wired(monkeypatch):
    """Stand-ins for the two repositories, with the drain recorded."""
    calls = {"paused": [], "drained": []}

    monkeypatch.setattr(service, "_reject_if_archived", lambda broker_id: None)
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
    return calls


class TestWhatComesBack:
    def test_the_response_is_narrow_not_a_broker_row(self, wired):
        """One question, one answer.

        Returning the broker row would put licence numbers, caps and dates
        behind a toggle that has no business carrying them — and the row's
        field is called self_paused, which would not validate against a model
        promising `paused`.
        """
        result = service.set_my_self_paused(BROKER_ID, True)

        assert result == {"paused": True, "backlog_drained": True}
        # The declared response model must actually accept it.
        assert AvailabilityResponse(**result).paused is True

    def test_pausing_does_not_drain(self, wired):
        """Pausing frees no capacity and has no backlog to offer."""
        service.set_my_self_paused(BROKER_ID, True)

        assert wired["drained"] == []

    def test_resuming_drains_the_group_it_was_given(self, wired):
        """The group comes from the RPC's return value, not a second read —
        one round trip, so there is no window where the pause saves and the
        follow-up read fails."""
        service.set_my_self_paused(BROKER_ID, False)

        assert wired["drained"] == [GROUP_ID]

    def test_an_unknown_broker_is_a_404(self, monkeypatch):
        monkeypatch.setattr(service, "_reject_if_archived", lambda broker_id: None)
        monkeypatch.setattr(
            service.repo, "set_self_paused", lambda broker_id, paused: Result(None)
        )

        with pytest.raises(HTTPException) as err:
            service.set_my_self_paused(BROKER_ID, True)
        assert err.value.status_code == 404


class TestAFailedDrainIsNeverAnError:
    """The half that decides whether the screen tells the truth."""

    @pytest.fixture
    def drain_fails(self, monkeypatch):
        monkeypatch.setattr(service, "_reject_if_archived", lambda broker_id: None)
        monkeypatch.setattr(
            service.repo, "set_self_paused", lambda broker_id, paused: Result(GROUP_ID)
        )

        def boom(group_id):
            raise RuntimeError("postgrest is down")

        monkeypatch.setattr(service.lead_repo, "drain_pending_for_group", boom)

    def test_it_does_not_raise(self, drain_fails):
        """A raise here becomes a 5xx, the frontend catches it, and the switch
        rolls back over a change the database KEPT — the broker reads as
        paused while leads are being assigned to them."""
        result = service.set_my_self_paused(BROKER_ID, False)

        assert result["paused"] is False

    def test_it_says_the_backlog_was_not_handed_out(self, drain_fails):
        """Reported rather than swallowed. The waiting leads do NOT get picked
        up by the next arrival — that one assigns only itself — so they sit
        until an explicit drain or tomorrow's reset. Somebody has to be told.
        """
        result = service.set_my_self_paused(BROKER_ID, False)

        assert result["backlog_drained"] is False


class TestTheRequestCannotNameSomeoneElse:
    def test_a_broker_id_in_the_body_is_refused(self):
        """extra="forbid", so pausing a colleague is rejected rather than
        quietly ignored. The id always comes from the session token."""
        with pytest.raises(Exception):
            SetSelfPausedRequest(paused=True, broker_id=str(BROKER_ID))

    def test_only_paused_is_accepted(self):
        assert SetSelfPausedRequest(paused=True).paused is True


class TestMyProfileCarriesTheField:
    """The deny-by-default model strips anything it does not declare.

    Without self_paused on MyProfileResponse the failure is silent and points
    the wrong way: the database holds true, the response drops it, and the
    header renders the switch ON. The broker is told leads are coming while
    they are genuinely stopped.
    """

    def test_self_paused_is_required_not_defaulted(self):
        from schemas.broker import MyProfileResponse

        field = MyProfileResponse.model_fields["self_paused"]
        assert field.is_required(), (
            "a default would hide the field being missing from SAFE_COLUMNS, "
            "which is the exact bug this guards"
        )

    def test_it_survives_serialisation(self):
        from schemas.broker import MyProfileResponse

        model = MyProfileResponse(
            name="A Broker",
            email="broker@example.com",
            role="broker",
            self_paused=True,
        )
        assert model.model_dump()["self_paused"] is True

    def test_safe_columns_actually_selects_it(self):
        """The other half of the same bug: the model can declare the field and
        still receive nothing if the query never asked for it."""
        from repositories import broker as broker_repo

        assert "self_paused" in broker_repo.SAFE_COLUMNS

    def test_it_is_not_on_the_auth_path(self):
        """AUTH_COLUMNS is the login read and deliberately carries only what
        authentication needs. Widening it moves profile fields onto the
        password path for no reason."""
        from repositories import broker as broker_repo

        assert "self_paused" not in broker_repo.AUTH_COLUMNS
