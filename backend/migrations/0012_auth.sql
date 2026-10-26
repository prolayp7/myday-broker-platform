-- Auth: TOTP (Google Authenticator) as the second factor, JWT access/refresh
-- tokens as the session mechanism. Login is two-step: password first (fails
-- fast, no enumeration signal beyond "invalid credentials"), then a separate
-- TOTP check. Enrollment is auto-prompted on a broker's first successful
-- password login when totp_secret is still null — no onboarding-wizard
-- involvement, no recovery codes: a lost phone means an admin nulls
-- totp_secret/totp_enrolled_at here and the broker re-enrolls from scratch.
--
-- totp_secret is encrypted at rest with a key held OUTSIDE this database
-- (TOTP_ENCRYPTION_KEY env var, separate from Supabase credentials) — a
-- leaked DB dump alone must not be enough to derive login codes. It must
-- NEVER be added to broker repo's SAFE_COLUMNS allowlist; treat it exactly
-- like password_hash.
alter table broker add column totp_secret text;
alter table broker add column totp_enrolled_at timestamptz;

-- Login-gate lockout only — 5 consecutive failures locks for 15 minutes.
-- Resets to 0 on a successful login. This never touches an already-issued
-- access/refresh token; it only blocks new login attempts.
alter table broker add column failed_login_count int not null default 0;
alter table broker add column locked_until timestamptz;

-- Append-only audit trail. Written by the service layer AFTER a mutating
-- action succeeds — purely observational, never gates or alters the
-- operation itself. actor_broker_id is nullable only for a rare
-- system-triggered event; every real admin action carries a real actor now
-- that login exists. No updated_at: a log entry is inserted once, never edited.
create table audit_log (
  id uuid primary key default gen_random_uuid(),
  actor_broker_id uuid references broker(id),
  action text not null,
  entity_type text not null,
  entity_id text not null,
  detail jsonb,
  created_at timestamptz not null default now()
);

create index idx_audit_log_actor_broker_id on audit_log(actor_broker_id);
create index idx_audit_log_entity on audit_log(entity_type, entity_id);

alter table audit_log enable row level security;
-- Deny-all: no policies defined. Only service_role (our FastAPI backend)
-- can access this table; direct anon/authenticated Supabase access is
-- fully blocked until a specific need arises.
