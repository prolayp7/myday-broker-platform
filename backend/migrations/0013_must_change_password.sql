-- Forces a broker through a real password-change before their session
-- starts whenever an admin has set their password for them (onboarding's
-- auto-generated temp password, or a future admin "reset password" action).
-- Cleared the moment the broker successfully changes it themselves via
-- POST /auth/change-password. Checked in services/auth.py's login() BEFORE
-- the TOTP step — a broker never reaches TOTP enrollment (or a real
-- session) while still on an admin-assigned password.
alter table broker add column must_change_password boolean not null default false;
