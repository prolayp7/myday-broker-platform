from fastapi import APIRouter, Depends

from auth_dependencies import rate_limit, try_get_bearer_broker_id
from schemas.auth import ChangePasswordRequest, LoginRequest, RefreshRequest, VerifyLoginRequest
from services import auth as service

router = APIRouter(prefix="/auth", tags=["auth"])

# Per-IP limit on the credential-guessing routes: generous for a real person
# (who makes a handful of attempts), punishing for a script hammering many
# accounts from one host. Complements — does not replace — the per-account
# lockout in services/auth.py. /auth/refresh is deliberately NOT limited: it
# needs a valid signed token to do anything, so it isn't brute-forceable, and
# limiting it could break legitimate silent token renewal under load.
_LOGIN_RATE_LIMIT = Depends(rate_limit(max_requests=10, window_seconds=60))


@router.post("/login", dependencies=[_LOGIN_RATE_LIMIT])
def login(request: LoginRequest):
    """Step 1 of login: email + password. Returns a short-lived pending_token
    for step 2. If this broker has never enrolled TOTP (or an admin reset
    it), the response also carries a fresh totp_uri to render as a QR code —
    scanning it and submitting one code to /auth/login/verify completes
    enrollment and logs in, in the same step. 401 on any invalid credential
    (a wrong password and a non-existent email return the identical error,
    on purpose — see services/auth.py)."""
    return service.login(request)


@router.post("/login/verify", dependencies=[_LOGIN_RATE_LIMIT])
def verify_login(request: VerifyLoginRequest):
    """Step 2 of login: the pending_token from /auth/login plus a 6-digit
    TOTP code. Returns a real access_token (1hr) + refresh_token (12hr) on
    success. Also the enrollment-confirmation step — the very first correct
    code a broker submits marks their TOTP setup complete."""
    return service.verify_login(request)


@router.post("/refresh")
def refresh(request: RefreshRequest):
    """Silently mint a new access_token from a still-valid refresh_token —
    this is what keeps a broker logged in for their full shift without
    re-entering a password or TOTP code every hour."""
    return service.refresh(request)


@router.post("/change-password", dependencies=[_LOGIN_RATE_LIMIT])
def change_password(
    request: ChangePasswordRequest,
    bearer_broker_id: str | None = Depends(try_get_bearer_broker_id),
):
    """Change a broker's password. Two ways to be identified: a
    pending_token in the request body (the forced first-login change, right
    after /auth/login returns status="password_change_required" — no real
    session exists yet), or a normal Authorization: Bearer access token
    (a voluntary change any time later). current_password is always
    required either way. 401 if neither identifies a broker, or if
    current_password is wrong."""
    return service.change_password(request, bearer_broker_id)
