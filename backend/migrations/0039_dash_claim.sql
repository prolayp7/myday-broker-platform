-- One row per claim in the report. Always belongs to the driver's dash_report.
-- dash_policy_id is NULLABLE because a claim's printed policy # doesn't always
-- match a policy independently captured elsewhere in the same report.
--
-- Carries explicit FIRST-PARTY and THIRD-PARTY blocks because DASH shows BOTH
-- sides of every claim, redacting whichever side is NOT the report subject
-- (seen directly: a claim where the subject is the third party has the
-- first-party block redacted, and vice versa). Every redactable field is
-- nullable and stored EXACTLY AS PRINTED ('******', 'Not available') rather
-- than normalized — 'store raw, source wording preserved' is the rule for this
-- whole DASH domain. Redaction and unavailability are REAL, EXPECTED values,
-- not errors.
--
-- Dollar amounts are kept AS PRINTED text (e.g. "$5,126.00") — not parsed to
-- numeric here; they are clean dollar strings, safe to cast later for
-- analysis. Dates normalized to ISO by the extraction step (DASH's DD/MM/YYYY).
create table dash_claim (
  id uuid primary key default gen_random_uuid(),
  dash_report_id uuid not null references dash_report(id),
  -- Which captured policy this claim ties back to, when its printed policy #
  -- matches one. NULL when it doesn't (see header comment).
  dash_policy_id uuid references dash_policy(id),

  -- === Claim identity / context ===
  date_of_loss date,
  insurer_name text,
  coverage text,                    -- e.g. "DCPD", "CL"
  date_reported date,
  claim_status text,                -- e.g. "Closed"
  policy_number text,               -- as printed ON THE CLAIM (may be redacted "******")
  claim_number text,                -- insurer's claim #, as printed (may be redacted)
  at_fault_percent text,            -- headline at-fault % from the banner, e.g. "50%"
  -- True when the banner carries the "*THIRD PARTY*" marker — meaning the
  -- report SUBJECT is the third party on this claim, not the first-party driver.
  is_third_party boolean not null default false,

  -- === Financials (as printed) ===
  total_loss text,                  -- e.g. "$5,126.00"
  total_expense text,               -- e.g. "$0.00"

  -- === Vehicle named on the claim (free text — NOT an FK to dash_vehicle) ===
  -- A claim can reference a vehicle from an older policy whose vehicles were
  -- never extracted, or one that otherwise doesn't match a stored row.
  vehicle_description text,         -- e.g. "2021 ACURA - TLX A-SPEC 4DR AWD - 19UUB6F53MA801038"
  vehicle_class text,               -- e.g. "01 / Private Passenger Vehicle"
  vehicle_loss text,                -- e.g. "Partial", "Total"

  -- === First-party driver block (each nullable; "Not available"/"******" real) ===
  first_party_driver text,
  first_party_dln text,
  first_party_date_of_birth text,
  first_party_listed_on_policy text,   -- e.g. "Yes" / "No" / "Not available"
  first_party_excluded text,
  first_party_at_fault text,           -- e.g. "50%", "0%"

  -- === Third-party driver block (each nullable; redaction real) ===
  third_party_driver text,
  third_party_dln text,
  third_party_insurer_name text,
  third_party_policy_number text,
  third_party_at_fault text,

  -- Whether we actually know which real person this claim's parties refer to.
  --   'Confirmed by DASH'          — a name/DLN on either side matches the subject.
  --   'Needs broker confirmation'  — BOTH sides redacted/unavailable; the claim
  --                                  can't be attributed by parsing alone, so a
  --                                  broker must ask the customer whose it was.
  --   'Confirmed by broker'        — once they have.
  -- A queryable state (list claims pending confirmation across every lead) —
  -- never silently guessed or dropped.
  driver_attribution_status text not null default 'Needs broker confirmation' check (
    driver_attribution_status in (
      'Confirmed by DASH', 'Needs broker confirmation', 'Confirmed by broker'
    )
  ),

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_dash_claim_dash_report_id on dash_claim(dash_report_id);
create index idx_dash_claim_dash_policy_id on dash_claim(dash_policy_id);

alter table dash_claim enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) can access
-- this table; direct anon/authenticated Supabase access is fully blocked.

create trigger trg_dash_claim_updated_at
  before update on dash_claim
  for each row
  execute function set_updated_at();
