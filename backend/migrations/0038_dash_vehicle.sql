-- One row per vehicle on a dash_policy — but in practice ONLY ever populated
-- for the PRINCIPAL APPLICANT's dash_report, and only from their MOST RECENT
-- policy, and only where the vehicle's type reads "Private Passenger Vehicle"
-- (any of the 01/02/03 sub-codes — Pleasure/Commute/Business — qualify; only
-- genuinely different classes like commercial are excluded). ALL THREE FILTERS
-- ARE ENFORCED IN THE EXTRACTION/MIDDLEWARE CODE, NOT BY A SCHEMA CONSTRAINT —
-- this table stays general enough to hold any policy's vehicles if that rule
-- ever changes. (Rationale: vehicle extraction for additional drivers, older
-- policies, and non-PPV classes is noise for the quote — see
-- docs/dash-integration-design.md.)
--
-- This is DASH's own FROZEN record of a vehicle. It is distinct from — and
-- never overwritten by — the broker's own editable `vehicle` table (0035-era
-- intake), which is SEEDED from a row here (vehicle.dash_vehicle_id) but then
-- lives independently.
--
-- All values stored AS PRINTED (source wording preserved). Fields DASH doesn't
-- always print are nullable.
create table dash_vehicle (
  id uuid primary key default gen_random_uuid(),
  dash_policy_id uuid not null references dash_policy(id),

  -- The subject's role on THIS vehicle specifically, e.g. "Principal Operator",
  -- "Occasional Operator", "Other Operator" — per-vehicle because the same
  -- policy can show the subject as Principal on one vehicle and Other on another.
  operator_role text,

  year_make_model text,             -- e.g. "2024 HONDA - CIVIC TOURING 4DR"
  vin text,
  vehicle_code text,                -- DASH's own rating code, e.g. "025106" — not always printed
  -- Coverage-code string as printed, e.g. "AB, TP-BI, CL, CM, DCPD, TP-PD" —
  -- kept as one string (source wording preserved).
  coverage text,
  vehicle_use text,                 -- e.g. "Commute", "Pleasure"
  commuting_distance_one_way text,  -- e.g. "15km"
  commuting_distance_annual text,   -- e.g. "14000km"
  vehicle_location text,            -- FSA prefix, e.g. "M2R" — not always printed
  business_use text,                -- e.g. "0%"
  branding text,                    -- e.g. "None" — not always printed
  -- Full printed string, e.g. "02 / Private Passenger Vehicle" — kept whole
  -- (the extraction filter reads the whole string; not split out).
  type_of_use_vehicle_class text,
  -- e.g. "2270kg" or "Not available" — kept as text, not forced numeric, since
  -- "Not available" is a common real value.
  gross_vehicle_weight text,
  driving_record text,              -- DASH's own driving-record score for this vehicle/operator

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_dash_vehicle_dash_policy_id on dash_vehicle(dash_policy_id);

alter table dash_vehicle enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) can access
-- this table; direct anon/authenticated Supabase access is fully blocked.

create trigger trg_dash_vehicle_updated_at
  before update on dash_vehicle
  for each row
  execute function set_updated_at();
