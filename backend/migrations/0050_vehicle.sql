-- The cars being quoted, as the broker enters them.
--
-- A rebuild, not a restore. An earlier `vehicle` table (0045, dropped in 0048)
-- was designed around DASH: it hung off the LEAD, carried a dash_vehicle_id
-- foreign key, and was seeded automatically by matching VINs against a parsed
-- report. None of that machinery exists any more — the broker reads DASH's own
-- website during the call and types what they see — so the table is designed
-- for that flow instead of carrying the old one's assumptions.
--
-- HANGS OFF THE DRIVER, NOT THE LEAD. Vehicles belong to the primary
-- applicant. In practice driver_id and lead_id would select the same rows,
-- since a lead has exactly one applicant (enforced by
-- idx_driver_one_principal_per_lead), but the FK should say whose cars these
-- are rather than merely which file they are filed under.
--
-- ON DELETE CASCADE: removing a driver removes their vehicles in the same
-- statement. Without it, deleting a driver who had a vehicle would fail on a
-- foreign-key violation and surface as a 500 rather than anything useful.
-- Rarely fires in practice — only the applicant has vehicles, and
-- services/driver.delete_driver refuses to delete the applicant — so this is
-- a backstop, not a routine path.
--
-- NO UNIQUE CONSTRAINT ON VIN. Deliberate. The old table had one to keep
-- DASH's automatic VIN matching unambiguous; nothing matches on VIN now, so
-- the only thing it would catch is a broker typing the same car twice, at the
-- cost of making a mistyped VIN awkward to correct.
--
-- Every field is nullable. A quote is built over a phone call and has to be
-- saveable half-finished — a NOT NULL here would mean a broker cannot store
-- what they have so far.
create table vehicle (
  id uuid primary key default gen_random_uuid(),
  driver_id uuid not null references driver(id) on delete cascade,

  -- === Identity — read off the DASH report by the broker ===
  vin text,
  year_make_model text,             -- e.g. "2015 HONDA - CIVIC LX 4DR"

  -- === Acquisition — asked of the customer ===
  condition text check (condition in ('New', 'Used')),
  purchase_date date,
  -- Odometer reading at purchase. A number, not text: unlike anything read off
  -- a report, this is our own captured figure.
  km_at_purchase integer check (km_at_purchase >= 0),
  -- How the car is held. Drives whether a lienholder goes on the policy.
  ownership text check (ownership in ('Paid Off', 'Financed', 'Leased')),
  -- The lienholder, when Financed or Leased. Free text: the list of finance
  -- companies is long, regional, and changes.
  finance_company text,

  -- === Usage — asked of the customer ===
  annual_km integer check (annual_km >= 0),
  -- One-way commute distance. Named for what the broker actually asks ("how
  -- far, one day?") rather than the rating term.
  one_day_distance_km integer check (one_day_distance_km >= 0),
  -- Where it sits overnight — a real rating factor for theft and vandalism.
  parking_at_night text check (
    parking_at_night in (
      'Secure Condo/Apt Garage', 'Private Garage', 'Driveway',
      'Street', 'Parking Lot', 'Other'
    )
  ),

  -- === Equipment — both discount-bearing in Ontario ===
  winter_tires boolean,
  anti_theft boolean,

  -- === Cover being quoted ===
  -- Free text rather than a check constraint: what counts as "Full Coverage"
  -- versus "Liability Only" varies by insurer, and pinning a vocabulary here
  -- would fight the rating system rather than describe it.
  coverage_type text,

  -- NOTE: premium is deliberately NOT here. It is quoted per POLICY, not per
  -- vehicle, and in four parts (auto/home, current/proposed) that belong
  -- together on one row. Still no table for it; New Policy holds those figures
  -- as local state, as it does the quote header.

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- Postgres does not auto-index foreign keys, and every read of this table is
-- "the vehicles for this driver".
create index idx_vehicle_driver_id on vehicle(driver_id);

alter table vehicle enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) reaches this
-- table; direct anon/authenticated Supabase access is fully blocked. Same rule
-- as every other table in this schema.

create trigger trg_vehicle_updated_at
  before update on vehicle
  for each row
  execute function set_updated_at();
