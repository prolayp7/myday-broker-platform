"""Password hashing, JWT, and TOTP-secret-encryption helpers. Kept separate
from any one table's code so the auth flow can reuse these against the same
underlying primitives everywhere.

SECURITY:
- JWT_SECRET_KEY signs access/pending/refresh tokens. Rotating it instantly
  invalidates every outstanding token — treat it like any other credential.
- TOTP_ENCRYPTION_KEY encrypts broker.totp_secret at rest. It is DELIBERATELY
  a separate secret from the Supabase service-role key: a leaked DB dump
  alone must not be enough to derive a broker's live TOTP codes.
- Decoding ALWAYS pins algorithms=["HS256"] explicitly. Never accept
  whatever algorithm the token claims to use (the classic "alg confusion"
  JWT vulnerability) — only HS256, signed with our own secret, is trusted.
"""

import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

import bcrypt
import jwt
from cryptography.fernet import Fernet
from dotenv import load_dotenv

# Load .env directly rather than relying on some other module (e.g. db.py)
# having already done it first — module import order isn't guaranteed, and
# this module must never silently start with a missing secret.
load_dotenv(Path(__file__).resolve().parent / ".env")

JWT_ALGORITHM = "HS256"
JWT_SECRET_KEY = os.environ["JWT_SECRET_KEY"]
_fernet = Fernet(os.environ["TOTP_ENCRYPTION_KEY"].encode())

ACCESS_TOKEN_TTL = timedelta(hours=1)
REFRESH_TOKEN_TTL = timedelta(hours=12)
# Short-lived, single-purpose token issued between the password step and the
# TOTP step. It has ALREADY passed the password check, so it must never be
# usable as a real access token — the "stage" claim is what a route checking
# for a real session must reject.
PENDING_TOKEN_TTL = timedelta(minutes=5)

# The head-office MVR report is opened with a password, not a broker login.
# Its token is a stage of its own so it can never pass as a session:
# get_current_broker accepts stage "access" only.
MVR_REPORT_TOKEN_TTL = timedelta(hours=8)

TokenStage = Literal["access", "refresh", "pending_totp", "mvr_report"]


def hash_password(plain: str) -> str:
    """One-way bcrypt hash of a plaintext password. The plaintext is never
    stored — only this hash, which cannot be reversed."""
    return bcrypt.hashpw(plain.encode(), bcrypt.gensalt()).decode()


def verify_password(plain: str, hashed: str) -> bool:
    """Check a plaintext password against a stored bcrypt hash."""
    return bcrypt.checkpw(plain.encode(), hashed.encode())


def encrypt_totp_secret(plain_secret: str) -> str:
    """Encrypt a TOTP shared secret before storing it. Unlike a password,
    this must be reversible (verifying a code requires the plaintext
    secret) — so it's encrypted, not hashed."""
    return _fernet.encrypt(plain_secret.encode()).decode()


def decrypt_totp_secret(encrypted_secret: str) -> str:
    """Recover the plaintext TOTP secret to verify a broker's 6-digit code."""
    return _fernet.decrypt(encrypted_secret.encode()).decode()


def _create_token(broker_id: str, stage: TokenStage, ttl: timedelta, **extra_claims) -> str:
    now = datetime.now(timezone.utc)
    payload = {
        "sub": broker_id,
        "stage": stage,
        "iat": now,
        "exp": now + ttl,
        **extra_claims,
    }
    return jwt.encode(payload, JWT_SECRET_KEY, algorithm=JWT_ALGORITHM)


def create_access_token(broker_id: str, role: str) -> str:
    """A real, fully-authenticated session token. role is embedded so
    require_admin can check it without a DB round-trip, but every protected
    route still re-fetches the broker row for the live active/blocked/
    archived state — the embedded role is only ever a fast-path hint."""
    return _create_token(broker_id, "access", ACCESS_TOKEN_TTL, role=role)


def create_refresh_token(broker_id: str) -> str:
    """Long-lived token whose ONLY job is minting new access tokens via
    /auth/refresh. Carries no role claim — a refresh token can never be
    used directly as an access token even if someone tries to pass it as one
    (decode_token's stage check rejects it)."""
    return _create_token(broker_id, "refresh", REFRESH_TOKEN_TTL)


def create_pending_token(broker_id: str) -> str:
    """Issued after a correct password but before the TOTP check. Deliberately
    short-lived and stage-tagged so it can never be mistaken for (or reused
    as) a real access token."""
    return _create_token(broker_id, "pending_totp", PENDING_TOKEN_TTL)


def create_mvr_report_token(password_version: str) -> str:
    """Opens the head-office MVR report for MVR_REPORT_TOKEN_TTL.

    No broker behind it, so `sub` names the report. `pwv` is the password's
    version (mvr_report_settings.updated_at): the report refuses a token whose
    version is not the current one, which is how changing the password shuts
    out whoever had the old one."""
    return _create_token("mvr-report", "mvr_report", MVR_REPORT_TOKEN_TTL, pwv=password_version)


def decode_token(token: str) -> dict:
    """Decode and verify a JWT's signature/expiry. Raises jwt.PyJWTError
    (caught by callers) on anything invalid or expired. algorithms is
    pinned to exactly HS256 — never trust whatever alg the token itself
    claims."""
    return jwt.decode(token, JWT_SECRET_KEY, algorithms=[JWT_ALGORITHM])
