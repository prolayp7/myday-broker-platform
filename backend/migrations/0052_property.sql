-- The properties on a combined auto+property quote.
--
-- WHEN THIS EXISTS AT ALL: only when lead.combined_policy_type is one of
-- 'Homeowners', 'Tenants' or 'Condo'. 'Standalone' means auto only and this
-- table stays empty for that lead. Note NULL and 'Standalone' are NOT the same
-- fact — NULL means the broker has not asked yet, 'Standalone' means they
-- asked and the answer was auto-only — and the enum below deliberately keeps
-- that distinction on the lead rather than collapsing it here.
--
-- HANGS OFF THE DRIVER, NOT THE LEAD — the same reasoning as vehicle (0050),
-- and for a sharper reason. The LEAD is whoever filled in the ad: a son can
-- enquire on behalf of his father, in which case lead.name is the son and the
-- applicant driver is the father. The house belongs to the father. A lead_id
-- here would file the house under the enquiry, which is not who owns it, and
-- would leave no way to say so.
--
-- ON DELETE CASCADE: removing a driver removes their properties in the same
-- statement, or the delete fails on a foreign-key violation and surfaces as a
-- 500. This is a backstop rather than a routine path — only the applicant
-- holds properties, and services/driver.delete_driver refuses to delete the
-- applicant (400, verified) — so in practice it fires only if the applicant is
-- ever removed as part of removing the whole lead.
--
-- ONE TABLE FOR THREE FORMS. Tenant, Homeowner and Condo intakes share an
-- address and seven more fields, then diverge; Homeowner adds fourteen and
-- Condo adds one more on top. That is the same 'shared table, some columns
-- conditionally used' shape as driver (principal vs additional), and the
-- alternative — three tables that are 30% the same — makes 'all properties on
-- this quote' a three-way union for no gain. Which columns apply is decided by
-- property_type, per row.
--
-- EVERY DETAIL COLUMN IS NULLABLE, including the four the data model marked
-- required. The Primary row is created the moment the broker picks a policy
-- type, which is the START of the conversation, not the end of it: nothing is
-- known yet. A NOT NULL on structure or year_built would mean the row cannot
-- be created until the customer has answered, and then there would be nowhere
-- to put the first answer. Same rule as vehicle and driver.
create table property (
  id uuid primary key default gen_random_uuid(),

  -- The applicant. See the note above on why this is not lead_id.
  driver_id uuid not null references driver(id) on delete cascade,

  -- WHICH property this is on the quote. 'Primary' is the applicant's own
  -- residence and is created by the policy-type flow, never added by hand.
  -- The other two are investment properties a Homeowner or Condo applicant
  -- rents out — a Tenant applicant cannot have them (enforced in the service
  -- layer, which can read the primary's type; a CHECK here cannot).
  property_role text not null check (
    property_role in ('Primary', 'Rental Dwelling', 'Rented Condo')
  ),

  -- WHICH INTAKE FORM applies to this row, and therefore which of the columns
  -- below are relevant. Stored per row rather than read from
  -- lead.combined_policy_type because a Rental Dwelling under a Condo primary
  -- is rated as a house, not as a condo: the applicant's own policy type does
  -- not decide what an investment property is.
  property_type text not null check (
    property_type in ('Tenant', 'Homeowner', 'Condo')
  ),

  -- === Where the property is ===
  -- Its OWN address, not a read through to the applicant's. Only the Primary
  -- ever starts out matching where the applicant lives; a Rental Dwelling or
  -- Rented Condo is a different building by definition and the broker types it
  -- fresh.
  --
  -- The Primary is SEEDED from driver's address when the row is created, then
  -- owned here — the same rule as the applicant being seeded from the lead
  -- (0049). Reading through to driver instead would be one address in one
  -- place, but it silently rewrites the insured property's address whenever
  -- the applicant's mailing address is corrected, including for someone who
  -- has moved. Seeded once means a correction sticks and nothing re-syncs
  -- behind the broker.
  --
  -- Same four column names as driver, deliberately: it is the same fact about
  -- the same kind of thing, and postal_code is a rating factor on both.
  street_address text,
  city text,
  province text,
  postal_code text,

  -- === Common to all three forms ===
  insured_since date,
  insured_with_property_since date,
  -- Confirmed identical on the Tenant and Homeowner intake forms.
  structure text check (
    structure in (
      'High Rise Apartment', 'Low Rise Apartment', 'Basement Apartment',
      'Row House/Town House (End)', 'Row House/Town House (Inside)',
      'Detached', 'Duplex', 'Triplex', 'Multi-plex (4 units or more)', 'Other'
    )
  ),
  year_built int,
  -- From the Tenant form's "add a note", kept available for every type.
  notes text,

  -- === Homeowner and Condo only ===
  -- Layout.
  stories numeric,
  full_baths int,
  half_baths int,
  number_of_mortgages int,
  -- text, not an enum: the real dropdown values are not confirmed yet. A CHECK
  -- on guessed values would reject what a broker is actually told. Same for
  -- burglar_alarm and sump_type below.
  finished_percent text,

  -- Year each system was last done.
  electrical_year int,
  plumbing_year int,
  heating_year int,
  roof_year int,
  water_heater_year int,

  -- Safety.
  burglar_alarm text,
  smoke_detectors int,
  fire_extinguishers int,
  sump_type text,

  -- === Condo only ===
  -- Dollar coverage for the unit's contents — the single field Condo adds
  -- beyond Homeowner's set.
  contents_value numeric,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- BACKSTOP: at most one Primary per applicant. Partial, so the rental rows
-- (which repeat freely) are unconstrained. This is what makes the get-or-
-- create in the policy-type flow safe under a double-click: two requests both
-- see no Primary and both insert, the index lets exactly one win, and the
-- loser re-reads the winner's row. A check-then-insert has a window between
-- the two statements; an index does not. Same pattern as
-- idx_driver_one_principal_per_lead (0035).
create unique index idx_property_one_primary_per_driver
  on property(driver_id)
  where property_role = 'Primary';

-- Every read is "this applicant's properties".
create index idx_property_driver_id on property(driver_id);

alter table property enable row level security;

create trigger property_set_updated_at
  before update on property
  for each row
  execute function set_updated_at();
