"""Tests for require_admin — the only authorization decision in the backend.

WHY THIS FILE EXISTS. Every admin route in the application reaches the same
function, so one wrong comparison there opens every admin screen to every
broker at once. It had no tests at all until the `manager` role was removed,
which is a lot of blast radius for an untested `if`.

The failure mode is silent in the dangerous direction: a gate that wrongly
ADMITS someone raises nothing and logs nothing — the request simply succeeds.
Only a test asserting the rejection can catch it.

require_admin takes its broker dict through Depends, so it can be called
directly with a plain dict — no TestClient, no database. Same env-key setup as
tests/test_security.py, since auth_dependencies imports security.
"""

import os

import pytest
from fastapi import HTTPException

os.environ.setdefault(
    "JWT_SECRET_KEY",
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
)
if "TOTP_ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet

    os.environ["TOTP_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import auth_dependencies  # noqa: E402


def broker_row(role: str) -> dict:
    """The shape get_current_broker returns — the live DB row, not token
    claims. Only `role` matters to require_admin; the rest is realism."""
    return {
        "id": "11111111-2222-3333-4444-555555555555",
        "email": "someone@example.com",
        "role": role,
        "is_active": True,
        "is_blocked_by_admin": False,
        "is_archived": False,
    }


class TestRequireAdmin:
    def test_admin_passes_and_gets_the_row_back(self):
        """Routes depend on the returned dict for audit attribution
        (`admin["id"]`), so passing the gate is not enough — it must hand the
        broker back."""
        row = broker_row("admin")
        assert auth_dependencies.require_admin(row) is row

    def test_broker_is_rejected(self):
        with pytest.raises(HTTPException) as exc:
            auth_dependencies.require_admin(broker_row("broker"))
        assert exc.value.status_code == 403

    def test_manager_is_rejected(self):
        """THE CHANGE. Until migrations/0066 this passed — require_admin
        accepted ("admin", "manager"), making the two tiers identical. If this
        test ever fails, the membership check has come back and `manager`
        silently has admin rights again."""
        with pytest.raises(HTTPException) as exc:
            auth_dependencies.require_admin(broker_row("manager"))
        assert exc.value.status_code == 403

    def test_403_names_what_is_required(self):
        with pytest.raises(HTTPException) as exc:
            auth_dependencies.require_admin(broker_row("broker"))
        assert "admin" in exc.value.detail.lower()

    @pytest.mark.parametrize(
        "role", ["superuser", "Admin", "ADMIN", "", "owner", "root"]
    )
    def test_unknown_roles_fail_closed(self, role):
        """A value the check constraint no longer permits — or one differing
        only in case — must be REFUSED, never waved through. Fail-closed is
        the property that matters here: a gate that admits the unexpected is
        worse than one that rejects something valid, because nothing reports
        it."""
        with pytest.raises(HTTPException) as exc:
            auth_dependencies.require_admin(broker_row(role))
        assert exc.value.status_code == 403


class TestRoleVocabulary:
    """The role vocabulary is written down in four places — the DB check
    constraint, this Literal, the gate above, and the frontend dropdown. They
    have to agree; these pin the two that live in Python."""

    def test_schema_allows_exactly_broker_and_admin(self):
        import typing

        from schemas.broker import Role

        assert set(typing.get_args(Role)) == {"broker", "admin"}

    def test_manager_is_rejected_by_the_request_schema(self):
        """Belt and braces with the DB constraint: a client sending the
        retired value gets a 422 naming the field, rather than a constraint
        violation surfacing as a 500."""
        import pydantic

        from schemas.broker import UpdateBrokerRequest

        with pytest.raises(pydantic.ValidationError):
            UpdateBrokerRequest(
                name="X", email="x@example.com", phone="4165550000", role="manager"
            )


class TestPartialUpdateDoesNotResetFields:
    """Regression for a silent data-loss bug found while removing the role.

    UpdateBrokerRequest carries CREATE-oriented defaults (role -> "broker",
    resident_province -> "Ontario", lines_of_authority -> []). services/
    broker.py used a bare model_dump(), which cannot tell an omitted field
    from one set to its default, so it materialised and WROTE those defaults:
    a PATCH that left role out demoted an admin, and one that left
    lines_of_authority out cleared their licensing. Nothing errored."""

    def payload(self):
        from schemas.broker import UpdateBrokerRequest

        # Only the three always-required fields — the shape of "change this
        # broker's phone number and nothing else".
        return UpdateBrokerRequest(
            name="Eldho George", email="e@example.com", phone="4165550000"
        )

    def test_omitted_role_is_not_written(self):
        written = self.payload().model_dump(mode="json", exclude_unset=True)
        assert "role" not in written, "an omitted role must not be written back"

    def test_omitted_licensing_fields_are_not_written(self):
        written = self.payload().model_dump(mode="json", exclude_unset=True)
        for field in ("lines_of_authority", "resident_province"):
            assert field not in written, f"{field} would be reset to its default"

    def test_only_the_sent_fields_are_written(self):
        written = self.payload().model_dump(mode="json", exclude_unset=True)
        assert set(written) == {"name", "email", "phone"}

    def test_an_explicitly_sent_role_still_applies(self):
        """The fix must not break the real use — the edit form does send
        role, and that has to keep working."""
        from schemas.broker import UpdateBrokerRequest

        request = UpdateBrokerRequest(
            name="X", email="x@example.com", phone="4165550000", role="admin"
        )
        written = request.model_dump(mode="json", exclude_unset=True)
        assert written["role"] == "admin"
