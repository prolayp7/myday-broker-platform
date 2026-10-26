"""Business logic for the two-step login flow (password, then TOTP) plus
enrollment and lockout. Every HTTPException decision for auth lives here.

SECURITY (see the auth security review, 2026-07-10):
- Login failures are intentionally generic ("Invalid email or password") for
  BOTH a wrong password and a non-existent email — the response must never
  let a caller distinguish "no such broker" from "wrong password", or the
  login endpoint becomes an account-enumeration oracle.
- Lockout is checked BEFORE the password is verified. If we checked the
  password first, a locked-but-correct-password attempt would return a
  different error than a locked-and-wrong-password attempt, leaking whether
  the password was actually right while locked.
- TOTP verification window is pinned to valid_window=1 (~90 seconds of
  drift tolerance total) — wide enough for clock skew, narrow enough that a
  captured code can't be replayed long after the fact.
"""

import os
from datetime import datetime, timedelta, timezone

import pyotp
from fastapi import HTTPException
from jwt import PyJWTError

import security
from repositories import broker as repo
from schemas.auth import (
    AccessTokenResponse,
    ChangePasswordRequest,
    LoginRequest,
    LoginResponse,
    RefreshRequest,
    SessionResponse,
    VerifyLoginRequest,
)

MAX_FAILED_ATTEMPTS = 5
LOCKOUT_DURATION = timedelta(minutes=15)

# What an authenticator app shows beside the code. ENV-DEPENDENT because a
# broker testing on staging otherwise gets a second entry named exactly like
# the production one, with no way to tell which code belongs where.
#
# THE NAME IS BAKED INTO THE ENROLLED SECRET, not read at verification time —
# changing it renames nothing that already exists, it only labels enrolments
# made from here on.
_TOTP_ISSUER = os.environ.get("TOTP_ISSUER", "MyDay CRM")

# Generic on purpose — see module docstring. Used for every login-step
# failure that must not reveal whether the email exists or the account is
# locked, so an attacker learns nothing beyond "that attempt didn't work".
_INVALID_CREDENTIALS = HTTPException(401, "Invalid email or password")


def _totp_uri(secret: str, email: str) -> str:
    return pyotp.TOTP(secret).provisioning_uri(name=email, issuer_name=_TOTP_ISSUER)


def login(request: LoginRequest) -> LoginResponse:
    rows = repo.get_auth_by_email(request.email).data
    if not rows:
        # No such broker — still run a bcrypt check against a dummy hash so
        # this branch takes roughly the same time as a real wrong-password
        # check (a cheap, standard timing-attack mitigation), then fail
        # with the same generic error as a real wrong password.
        security.verify_password(request.password, security.hash_password("dummy-timing-guard"))
        raise _INVALID_CREDENTIALS

    broker = rows[0]

    if broker["locked_until"] and datetime.fromisoformat(broker["locked_until"]) > datetime.now(timezone.utc):
        raise _INVALID_CREDENTIALS

    if not security.verify_password(request.password, broker["password_hash"]):
        _record_failed_attempt(broker)
        raise _INVALID_CREDENTIALS

    # Password correct — but blocked/archived/deactivated accounts still
    # can't proceed past this point, same live checks as get_current_broker.
    if broker["is_archived"]:
        raise HTTPException(403, "This account is archived")
    if broker["is_blocked_by_admin"]:
        raise HTTPException(403, "This account is blocked")
    if not broker["is_active"]:
        raise HTTPException(403, "This account is deactivated")

    pending_token = security.create_pending_token(broker["id"])

    if broker["must_change_password"]:
        # An admin set this password — the broker must replace it before
        # anything else, including TOTP enrollment. The SAME pending_token
        # is reused for POST /auth/change-password (see that function's
        # docstring for why accepting a pending_token there is safe).
        return LoginResponse(status="password_change_required", pending_token=pending_token)

    if not broker["totp_enrolled_at"]:
        # First login (or an admin reset the TOTP secret) — generate a fresh
        # secret now so the QR can be shown immediately. Saved to the row
        # right away, but totp_enrolled_at stays null until the broker
        # proves the scan worked by submitting one correct code — see
        # verify_login below.
        plain_secret = pyotp.random_base32()
        repo.set_totp_secret(broker["id"], security.encrypt_totp_secret(plain_secret))
        return LoginResponse(
            status="enrollment_required",
            pending_token=pending_token,
            totp_uri=_totp_uri(plain_secret, broker["email"]),
        )

    return LoginResponse(status="totp_required", pending_token=pending_token)


def verify_login(request: VerifyLoginRequest) -> SessionResponse:
    try:
        payload = security.decode_token(request.pending_token)
    except PyJWTError:
        raise HTTPException(401, "Invalid or expired login attempt — start over")

    if payload.get("stage") != "pending_totp":
        raise HTTPException(401, "Invalid or expired login attempt — start over")

    rows = repo.get_auth_by_id(payload["sub"]).data
    if not rows:
        raise HTTPException(401, "Invalid or expired login attempt — start over")

    broker = rows[0]

    if broker["locked_until"] and datetime.fromisoformat(broker["locked_until"]) > datetime.now(timezone.utc):
        raise HTTPException(401, "Too many failed attempts — try again later")

    if not broker["totp_secret"]:
        # Shouldn't happen through normal use (login() always sets one
        # before issuing a pending_token for an unenrolled broker), but
        # guards against a stale pending_token from before an admin reset.
        raise HTTPException(401, "Invalid or expired login attempt — start over")

    plain_secret = security.decrypt_totp_secret(broker["totp_secret"])
    totp = pyotp.TOTP(plain_secret)

    if not totp.verify(request.code, valid_window=1):
        _record_failed_attempt(broker)
        raise HTTPException(401, "Incorrect code")

    if not broker["totp_enrolled_at"]:
        repo.confirm_totp_enrollment(broker["id"])

    repo.record_login_success(broker["id"])

    return SessionResponse(
        access_token=security.create_access_token(broker["id"], broker["role"]),
        refresh_token=security.create_refresh_token(broker["id"]),
    )


def change_password(request: ChangePasswordRequest, bearer_broker_id: str | None) -> dict:
    """Serves both the forced first-login change (identified by the
    pending_token /auth/login just issued) and a voluntary later change
    (identified by a normal Authorization: Bearer access token, resolved by
    the route into bearer_broker_id). Exactly one of pending_token /
    bearer_broker_id must identify the broker.

    current_password is required in BOTH cases — even with a valid
    pending_token (which already proves the broker knew their old
    password), re-checking it here means a stolen/leaked pending_token
    alone still isn't enough to change the password without also knowing
    the current one. No new privilege beyond what step 1 of login already
    proved."""
    if request.pending_token:
        try:
            payload = security.decode_token(request.pending_token)
        except PyJWTError:
            raise HTTPException(401, "Invalid or expired login attempt — start over")
        if payload.get("stage") != "pending_totp":
            raise HTTPException(401, "Invalid or expired login attempt — start over")
        broker_id = payload["sub"]
    elif bearer_broker_id:
        broker_id = bearer_broker_id
    else:
        raise HTTPException(401, "Not authenticated")

    rows = repo.get_auth_by_id(broker_id).data
    if not rows:
        raise HTTPException(401, "Broker not found")
    broker = rows[0]

    # A blocked/archived/deactivated broker can do nothing — same live gate
    # as login() and refresh(). Without this, a broker blocked mid-session
    # (access token still valid for up to an hour) could still change their
    # password. Checked before the password so the account-state answer
    # doesn't depend on whether the current password was right.
    if broker["is_archived"] or broker["is_blocked_by_admin"] or not broker["is_active"]:
        raise HTTPException(403, "This account can't be changed right now")

    if not security.verify_password(request.current_password, broker["password_hash"]):
        raise HTTPException(401, "Current password is incorrect")

    # The new password must actually differ — otherwise a broker forced to
    # replace an admin-set temp password could re-set the same one and stay
    # on a password the admin knows, defeating must_change_password.
    if request.new_password == request.current_password:
        raise HTTPException(400, "New password must be different from your current one")

    repo.set_password(broker["id"], security.hash_password(request.new_password))
    return {"status": "ok"}


def refresh(request: RefreshRequest) -> AccessTokenResponse:
    try:
        payload = security.decode_token(request.refresh_token)
    except PyJWTError:
        raise HTTPException(401, "Invalid or expired refresh token")

    if payload.get("stage") != "refresh":
        raise HTTPException(401, "Invalid or expired refresh token")

    rows = repo.get_auth_by_id(payload["sub"]).data
    if not rows:
        raise HTTPException(401, "Invalid or expired refresh token")

    broker = rows[0]

    # Same live checks as every other gate — a blocked/archived broker's
    # refresh token must die immediately, not just their access token.
    if broker["is_archived"] or broker["is_blocked_by_admin"] or not broker["is_active"]:
        raise HTTPException(401, "Invalid or expired refresh token")

    return AccessTokenResponse(access_token=security.create_access_token(broker["id"], broker["role"]))


def _record_failed_attempt(broker: dict) -> None:
    new_count = broker["failed_login_count"] + 1
    locked_until = None
    if new_count >= MAX_FAILED_ATTEMPTS:
        locked_until = (datetime.now(timezone.utc) + LOCKOUT_DURATION).isoformat()
    repo.record_login_failure(broker["id"], new_count, locked_until)
