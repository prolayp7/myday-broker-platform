-- The broker's own editable vehicle record — what is actually being quoted.
--
-- Distinct from dash_vehicle (0038), which is DASH's FROZEN record of what a
-- previous insurer had on file. That table is never edited; this one is seeded
-- from it (dash_vehicle_id) and then lives independently, because the two
-- answer different questions:
--   dash_vehicle  "what did the last insurer have?"  — evidence, immutable
--   vehicle       "what are we insuring, on what terms?" — the working record
-- Keeping them apart means re-pulling DASH never overwrites what the broker
-- collected from the customer, and the customer's answers never masquerade as
-- something DASH reported.
--
-- The fields below split cleanly in two:
--   * Identity (vin, year_make_model) — seeded from DASH, correctable.
--   * Everything else — collected from the customer over the call. DASH does
--     not print any of it: it has no idea how the car was bought, where it
--     sleeps, or whether it has winter tires, and those are exactly the facts
--     that move a premium.
--
-- Every field is nullable. A quote is built over a conversation, and the
-- record has to be saveable half-finished — a NOT NULL here would mean a
-- broker cannot store what they have so far.

create table vehicle (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references lead(id),

  -- Where this vehicle was seeded from, when it came off a DASH report rather
  -- than being added by hand. Nullable: a customer can be quoted on a car that
  -- appears on no prior policy. ON DELETE SET NULL — losing the DASH row must
  -- never take the broker's own record with it.
  dash_vehicle_id uuid references dash_vehicle(id) on delete set null,

  -- === Identity — seeded from DASH, editable ===
  vin text,
  year_make_model text,             -- e.g. "2015 HONDA - CIVIC LX 4DR"

  -- === Acquisition — customer-collected ===
  condition text check (condition in ('New', 'Used')),
  purchase_date date,
  -- Odometer reading when they bought it. Integer km: unlike DASH's text
  -- fields this is our own captured number, so it is stored as a number.
  km_at_purchase integer check (km_at_purchase >= 0),
  -- How the car is held. Drives whether a lienholder has to go on the policy.
  ownership text check (
    ownership in ('Paid Off', 'Financed', 'Leased')
  ),
  -- The lienholder, when ownership is Financed or Leased. Left free text:
  -- the list of finance companies is long, regional and changes.
  finance_company text,

  -- === Usage — customer-collected ===
  annual_km integer check (annual_km >= 0),
  -- One-way commute distance. Named for what the broker asks ("how far, one
  -- day?") rather than the rating term.
  one_day_distance_km integer check (one_day_distance_km >= 0),
  -- Where it sits overnight — a real rating factor for theft and vandalism.
  parking_at_night text check (
    parking_at_night in (
      'Secure Condo/Apt Garage', 'Private Garage', 'Driveway',
      'Street', 'Parking Lot', 'Other'
    )
  ),

  -- === Equipment — customer-collected, both discount-bearing in Ontario ===
  winter_tires boolean,
  anti_theft boolean,

  -- === Cover being quoted ===
  -- Free text rather than a check constraint: what counts as "Full Coverage"
  -- vs "Liability Only" varies by insurer, and pinning a vocabulary here would
  -- fight the rating system rather than describe it.
  coverage_type text,

  -- NOTE: premium is deliberately NOT a column here. It is quoted per POLICY,
  -- not per vehicle, and in four parts (auto / home / combined / total) against
  -- BOTH the customer's current cover and what we are offering — eight figures
  -- that belong together on one row. They have no table yet; the UI holds them
  -- as wireframe state pending that decision.

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_vehicle_lead_id on vehicle(lead_id);
create index idx_vehicle_dash_vehicle_id on vehicle(dash_vehicle_id);

-- NOTE: the unique (lead_id, vin) index lives in 0047, not here — it was
-- designed after this migration had already been applied, and back-dating it
-- into this file would make 0045 disagree with what actually ran.

alter table vehicle enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) reaches this
-- table; direct anon/authenticated Supabase access is fully blocked. Same rule
-- as every other table in this schema.

create trigger trg_vehicle_updated_at
  before update on vehicle
  for each row
  execute function set_updated_at();
