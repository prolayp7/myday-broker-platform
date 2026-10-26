from typing import Literal

from pydantic import BaseModel, EmailStr, Field


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=72)


class LoginResponse(BaseModel):
    # "password_change_required": an admin set this broker's password
    # (onboarding, or a future reset) — they must call
    # POST /auth/change-password with this SAME pending_token before doing
    # anything else. Checked BEFORE totp_enrolled_at, so a broker on an
    # admin-assigned password never reaches TOTP enrollment on their old
    # credentials.
    # "enrollment_required": broker has no totp_secret yet — totp_uri is the
    # otpauth:// URI for the frontend to render as a QR code. The broker
    # confirms the scan by submitting one code to /auth/login/verify, same
    # as a normal login — enrollment and first login are the same request.
    # "totp_required": broker is already enrolled — no totp_uri, just prompt
    # for the next 6-digit code.
    status: Literal["password_change_required", "enrollment_required", "totp_required"]
    pending_token: str
    totp_uri: str | None = None


class VerifyLoginRequest(BaseModel):
    pending_token: str
    code: str = Field(min_length=6, max_length=6, pattern=r"^\d{6}$")


class SessionResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: Literal["bearer"] = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class AccessTokenResponse(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"


class ChangePasswordRequest(BaseModel):
    # pending_token: the forced first-login change — broker has no real
    # session yet, only what /auth/login just issued. Omitted for a
    # voluntary change, where the broker is identified by their normal
    # Authorization: Bearer header instead (see routes/auth.py).
    pending_token: str | None = None
    current_password: str = Field(min_length=1, max_length=72)
    new_password: str = Field(min_length=8, max_length=72)
