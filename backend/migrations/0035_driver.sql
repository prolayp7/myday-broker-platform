-- One row per person on a lead who drives — the principal applicant AND any
-- additional/occasional household drivers. Genuinely one-to-many off lead,
-- same pattern as attendance off broker. Same field list for everyone (DASH
-- itself lists principal and occasional operators identically), so ONE shared
-- table with is_principal distinguishing the applicant, rather than two tables
-- duplicating the same columns. Basics are captured by the broker over the
-- call and cross-checked/filled against DASH once the license triggers a pull.
--
-- Per the confirmed data model in
-- meta-crm-architecture/src/dataModelTables.ts. Enums are `check` constraints,
-- not native Postgres enum types, so adding an allowed value later is a simple
-- constraint change, not a type migration (same convention as broker).
--
-- The APPLICANT row: exactly one driver per lead has is_principal = true. It
-- always exists (get-or-created the first time a broker works the lead, for
-- any lead — Meta-sourced OR manually created), and starts BLANK — it is NOT
-- seeded from the lead's Meta-form name/phone/email, which are unverified
-- marketing data; the driver record is the real, verified intake profile,
-- filled from DASH and the broker's own confirmation. The partial unique index
-- below is the backstop guaranteeing a lead can never end up with two
-- applicants (e.g. from a double-open / two-tabs race), independent of the
-- get-or-create logic in the service layer.
create table driver (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references lead(id),

  -- True for exactly ONE driver per lead — the applicant. Enforced unique via
  -- the partial index below. False for every additional/occasional driver.
  is_principal boolean not null default false,

  -- Matches the real intake form's dropdown exactly. 'Applicant' is used for
  -- the principal's own row. 'Common Law' has no hyphen so it matches
  -- marital_status's vocabulary exactly.
  relationship_to_applicant text check (
    relationship_to_applicant in (
      'Applicant', 'Spouse', 'Common Law', 'Same Sex Partner',
      'Co-habitant', 'Child', 'Sibling', 'Parent'
    )
  ),

  -- Identity. Nullable because the applicant row starts blank and is filled
  -- from DASH / the broker's input over the call.
  first_name text,
  middle_name text,
  last_name text,
  date_of_birth date,
  gender text check (gender in ('Male', 'Female', 'Other')),
  marital_status text check (
    marital_status in ('Single', 'Married', 'Common Law', 'Divorced', 'Widowed')
  ),

  -- License. license_number is the field that triggers the DASH pull once the
  -- broker has it.
  license_number text,
  license_province text,

  -- Graduated-licensing stage dates — broker-collected from the customer
  -- (DASH only gives a rolled-up years-licensed figure, not these dates).
  g1_date date,
  g2_date date,
  g_date date,

  -- Address (kept as separate fields — city/postal are real rating factors and
  -- needed individually for Meta/TikTok signal matching).
  street_address text,
  unit_number text,
  city text,
  province text,
  postal_code text,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Every foreign key gets an index (Postgres does not auto-index FKs).
create index idx_driver_lead_id on driver(lead_id);

-- BACKSTOP: at most one applicant (is_principal = true) per lead. A partial
-- unique index — it only constrains the principal rows, leaving any number of
-- additional (is_principal = false) drivers per lead unconstrained. This makes
-- a duplicate applicant impossible at the database level even if the
-- service-layer get-or-create is bypassed or races.
create unique index idx_driver_one_principal_per_lead
  on driver(lead_id)
  where is_principal;

alter table driver enable row level security;
-- Deny-all: no policies defined. Only service_role (our FastAPI backend) can
-- access this table; direct anon/authenticated Supabase access is fully
-- blocked until a specific need arises. Same as every other table.

create trigger trg_driver_updated_at
  before update on driver
  for each row
  execute function set_updated_at();
