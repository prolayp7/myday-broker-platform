"""Tests for password hashing, TOTP secret encryption, and JWT issuance.

WHY THIS FILE EXISTS. These are the functions standing between a stranger and
a broker's book of business. Their failure modes are the quiet kind: a token
that should have been rejected but is not, a "stage" claim that lets the
half-authenticated token from the password step act as a full session. Nothing
here throws a visible error when it goes wrong — the request simply succeeds
for someone it should not have.

The tests set their own JWT_SECRET_KEY and TOTP_ENCRYPTION_KEY before import,
so they neither read the real .env nor depend on a deployed environment.
"""

import os
from datetime import timedelta

import pytest

# security.py reads both at import time (os.environ[...]), so these must be
# set before it is imported. Values are test-only and deliberately not the
# real ones.
# 64 hex chars, matching what the real key generator produces (see
# .env.example: secrets.token_hex(32)). Length matters even in tests — a
# shorter key makes PyJWT emit InsecureKeyLengthWarning on every call, and a
# suite that cries wolf is a suite people stop reading.
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
)
if "TOTP_ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet

    os.environ["TOTP_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

import jwt  # noqa: E402

import security  # noqa: E402


# ── passwords ────────────────────────────────────────────────────────────
class TestPasswordHashing:
    def test_hash_is_not_the_plaintext(self):
        hashed = security.hash_password("correct horse battery staple")
        assert "correct horse" not in hashed
        assert hashed.startswith("$2b$")

    def test_correct_password_verifies(self):
        hashed = security.hash_password("hunter2hunter2")
        assert security.verify_password("hunter2hunter2", hashed) is True

    def test_wrong_password_is_rejected(self):
        hashed = security.hash_password("hunter2hunter2")
        assert security.verify_password("hunter2hunter3", hashed) is False

    def test_same_password_hashes_differently_each_time(self):
        """bcrypt salts every hash. Identical digests for one password would
        mean two brokers who chose the same password are visibly identical in
        the database."""
        a = security.hash_password("same-password")
        b = security.hash_password("same-password")
        assert a != b
        assert security.verify_password("same-password", a)
        assert security.verify_password("same-password", b)

    def test_empty_password_still_verifies_against_its_own_hash(self):
        """Not an endorsement of empty passwords — the API layer enforces a
        minimum length. This pins that the primitive itself does not do
        something surprising with the edge case."""
        hashed = security.hash_password("")
        assert security.verify_password("", hashed) is True
        assert security.verify_password("x", hashed) is False


# ── TOTP secret encryption ───────────────────────────────────────────────
class TestTotpSecretEncryption:
    def test_roundtrips(self):
        """Unlike a password this must be REVERSIBLE — verifying a 6-digit
        code needs the plaintext secret back."""
        secret = "JBSWY3DPEHPK3PXP"
        assert security.decrypt_totp_secret(security.encrypt_totp_secret(secret)) == secret

    def test_ciphertext_does_not_contain_the_secret(self):
        secret = "JBSWY3DPEHPK3PXP"
        assert secret not in security.encrypt_totp_secret(secret)

    def test_same_secret_encrypts_differently_each_time(self):
        """Fernet includes a random IV. Identical ciphertexts would leak that
        two brokers share a secret."""
        secret = "JBSWY3DPEHPK3PXP"
        assert security.encrypt_totp_secret(secret) != security.encrypt_totp_secret(secret)

    def test_tampered_ciphertext_is_rejected_not_silently_wrong(self):
        """Fernet is authenticated encryption: a modified token must raise,
        never decrypt to garbage that would then be used to check a code."""
        from cryptography.fernet import InvalidToken

        encrypted = security.encrypt_totp_secret("JBSWY3DPEHPK3PXP")
        tampered = encrypted[:-4] + ("AAAA" if not encrypted.endswith("AAAA") else "BBBB")
        with pytest.raises(InvalidToken):
            security.decrypt_totp_secret(tampered)


# ── JWTs ─────────────────────────────────────────────────────────────────
class TestTokenStages:
    """The `stage` claim is the whole defence against a token from one step of
    the login flow being replayed at another."""

    BROKER_ID = "11111111-2222-3333-4444-555555555555"

    def test_access_token_carries_stage_and_role(self):
        claims = security.decode_token(security.create_access_token(self.BROKER_ID, "admin"))
        assert claims["stage"] == "access"
        assert claims["sub"] == self.BROKER_ID
        assert claims["role"] == "admin"

    def test_refresh_token_is_a_different_stage(self):
        claims = security.decode_token(security.create_refresh_token(self.BROKER_ID))
        assert claims["stage"] == "refresh"

    def test_pending_token_is_a_different_stage(self):
        """The token issued after the PASSWORD step but before TOTP. If this
        ever decoded with stage 'access', 2FA would be bypassable by anyone
        who knew a password."""
        claims = security.decode_token(security.create_pending_token(self.BROKER_ID))
        assert claims["stage"] == "pending_totp"
        assert claims["stage"] != "access"

    def test_pending_token_carries_no_role_claim(self):
        """It has not finished authenticating, so it must not carry the
        authority a finished session would."""
        claims = security.decode_token(security.create_pending_token(self.BROKER_ID))
        assert "role" not in claims

    def test_all_three_stages_are_distinct(self):
        stages = {
            security.decode_token(security.create_access_token(self.BROKER_ID, "broker"))["stage"],
            security.decode_token(security.create_refresh_token(self.BROKER_ID))["stage"],
            security.decode_token(security.create_pending_token(self.BROKER_ID))["stage"],
        }
        assert len(stages) == 3


class TestTokenExpiry:
    BROKER_ID = "11111111-2222-3333-4444-555555555555"

    def test_expired_token_is_rejected(self):
        expired = security._create_token(
            self.BROKER_ID, "access", timedelta(seconds=-1)
        )
        with pytest.raises(jwt.ExpiredSignatureError):
            security.decode_token(expired)

    def test_pending_token_is_short_lived(self):
        """Five minutes is the point — a token that has passed the password
        check but not TOTP should not sit around."""
        assert security.PENDING_TOKEN_TTL <= timedelta(minutes=10)

    def test_access_ttl_is_shorter_than_refresh_ttl(self):
        assert security.ACCESS_TOKEN_TTL < security.REFRESH_TOKEN_TTL


class TestTokenSignature:
    BROKER_ID = "11111111-2222-3333-4444-555555555555"

    def test_token_signed_with_another_key_is_rejected(self):
        """The forgery case: anyone can craft a payload, but without our
        secret the signature must not verify."""
        forged = jwt.encode(
            {"sub": self.BROKER_ID, "stage": "access", "role": "admin"},
            "attacker-chosen-key-also-64-chars-long-so-pyjwt-stays-quiet-here",
            algorithm="HS256",
        )
        with pytest.raises(jwt.InvalidTokenError):
            security.decode_token(forged)

    def test_tampered_payload_is_rejected(self):
        """Flipping a character in the claims section must break the
        signature rather than quietly change the claims."""
        token = security.create_access_token(self.BROKER_ID, "broker")
        header, payload, sig = token.split(".")
        tampered_payload = ("A" if payload[0] != "A" else "B") + payload[1:]
        with pytest.raises(jwt.InvalidTokenError):
            security.decode_token(f"{header}.{tampered_payload}.{sig}")

    def test_unsigned_token_is_rejected(self):
        """alg=none is the classic JWT bypass — a token with no signature at
        all must never be accepted."""
        unsigned = jwt.encode(
            {"sub": self.BROKER_ID, "stage": "access", "role": "admin"},
            key="",
            algorithm="none",
        )
        with pytest.raises(jwt.InvalidTokenError):
            security.decode_token(unsigned)

    def test_garbage_is_rejected(self):
        with pytest.raises(jwt.InvalidTokenError):
            security.decode_token("not-a-token")
