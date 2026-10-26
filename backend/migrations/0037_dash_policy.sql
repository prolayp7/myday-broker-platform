-- One row per policy the SUBJECT (the driver this report is about) appears on.
-- THE SUBJECT'S OWN INVOLVEMENT in that policy is what this row is about — the
-- operator_* fields. The policyholder_* fields are REFERENCE CONTEXT ONLY
-- (whose policy it was, e.g. a parent's), not a second subject of equal weight.
-- Read the row as: "our operator was on this policy, as <relationship>, from
-- <term> — the policy itself was held by <policyholder>, for context."
--
-- Captured for EVERY policy in the report, not just the most recent —
-- insurance-history continuity (and any coverage gap / continuous-insurance
-- start date / current-company start date) is COMPUTED live from
-- effective_date / expiry_date / cancellation_date across all of a driver's
-- dash_policy rows, never stored as a flag or a derived date (an earlier
-- printed gap/overlap flag's source could not be reliably confirmed across
-- samples, so it is deliberately not persisted).
--
-- All values stored AS PRINTED (source wording preserved); dates normalized to
-- ISO by the extraction step (DASH prints DD/MM/YYYY — see the date-format
-- guardrail, docs/dash-integration-design.md §1.5).
create table dash_policy (
  id uuid primary key default gen_random_uuid(),
  dash_report_id uuid not null references dash_report(id),

  -- === The subject's own operator record on THIS policy (the point of the row) ===
  -- operator_name / operator_dln are the subject's own name/DLN as printed on
  -- this policy's operator block. Should match dash_report.subject_name / .dln;
  -- kept per-row so a mismatch surfaces as a parsing-error signal.
  operator_name text,
  operator_dln text,
  -- As printed, e.g. "NA", "Other", "Self". THE key field for reading the
  -- subject's own insurance maturity: "Self" = it was their own policy;
  -- anything else = they were riding on someone else's (e.g. a parent's).
  relationship_to_policyholder text,
  -- The SUBJECT's own coverage window on this policy — "Start of the Earliest
  -- Term" / "End of the Latest Term" — NOT the policy's own dates (below).
  earliest_term_start date,
  latest_term_end date,

  -- === The policy's own facts ===
  policy_number text,               -- e.g. "053264672"
  insurer_name text,                -- e.g. "Allstate Insurance Company of Canada"
  -- As printed, e.g. "Active", "Expired", "Cancelled - renewal not taken" —
  -- free text since cancellation reasons vary and aren't a fixed short list.
  status text,
  -- The POLICY's own effective date — may PREDATE the subject's own
  -- earliest_term_start (e.g. a parent's policy that existed before the
  -- subject was added to it).
  effective_date date,
  expiry_date date,
  cancellation_date date,           -- null = ran to term or still active

  -- === Policyholder — REFERENCE ONLY ===
  -- Whose name the policy is under. May be the subject themselves, or someone
  -- else entirely (a parent not otherwise tracked as a driver on this lead).
  -- Deliberately NOT a foreign key to driver — DASH often doesn't give enough
  -- to reliably match this to anyone, and sometimes the policyholder isn't any
  -- operator listed on the policy at all.
  policyholder_name text,
  policyholder_address text,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_dash_policy_dash_report_id on dash_policy(dash_report_id);

alter table dash_policy enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) can access
-- this table; direct anon/authenticated Supabase access is fully blocked.

create trigger trg_dash_policy_updated_at
  before update on dash_policy
  for each row
  execute function set_updated_at();
