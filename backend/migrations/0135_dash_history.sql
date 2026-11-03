-- Insurance gaps and claims read off a DASH report.
--
-- Two lists per applicant, both DASH FACTS rather than broker input: a new
-- reading of the report replaces them. Shown read-only on New Policy as their
-- own sections, between Vehicles and Premium.
--
--   driver_insurance_gap   one row per break in the main driver's coverage in
--                          the last 6 years, with its reason
--   driver_claim           one row per claim in the last 8 years, with whose
--                          it was, the policy's at-fault %, and the amounts in
--                          the four boxes a quote asks for (Loss + Expense)
--
-- REPLACED IN ONE STATEMENT. replace_dash_history() deletes and re-inserts
-- both lists inside a single function call, so a failure part-way leaves the
-- previous rows untouched — never an empty list that reads as "no claims".
-- It is only called when the report was read in full; the regex fallback
-- cannot read these lists and leaves them alone.
--
-- Days of a gap are not stored — worked out from the two dates.
--
-- SAFE TO RE-RUN. APPLY BY HAND in the Supabase SQL editor, staging first.

begin;

create table if not exists driver_insurance_gap (
  id uuid primary key default gen_random_uuid(),
  driver_id uuid not null references driver(id) on delete cascade,
  gap_from date not null,
  gap_to date not null check (gap_to > gap_from),
  reason text not null
    check (reason in ('Non-payment', 'Possible new vehicle', 'Other')),
  created_at timestamptz not null default now()
);
create index if not exists idx_driver_insurance_gap_driver
  on driver_insurance_gap (driver_id, gap_from);
alter table driver_insurance_gap enable row level security;

create table if not exists driver_claim (
  id uuid primary key default gen_random_uuid(),
  driver_id uuid not null references driver(id) on delete cascade,
  date_of_loss date not null,
  -- The "At-Fault : X%" on the claim's top line: the share of fault on the
  -- report driver's POLICY side. Null when DASH printed none — unknown, never 0.
  at_fault_percent numeric check (at_fault_percent between 0 and 100),
  whose text not null
    check (whose in ('applicant', 'additional', 'not_on_policy', 'unknown')),
  claim_driver_license text,
  claim_driver_name text,
  insurer text,
  claim_number text,
  tp_bi numeric not null default 0,
  tp_pd numeric not null default 0,
  ab numeric not null default 0,
  coll numeric not null default 0,
  -- The broker's "Leave out": a claim by a driver not on the policy who has
  -- their own insurance. Greyed on screen, never deleted.
  left_out boolean not null default false,
  created_at timestamptz not null default now()
);
-- Added after the first draft of this migration ran on staging: a no-op on a
-- fresh database, the missing column on one that ran the draft.
alter table driver_claim
  add column if not exists left_out boolean not null default false;
create index if not exists idx_driver_claim_driver
  on driver_claim (driver_id, date_of_loss);
alter table driver_claim enable row level security;

-- "Check these": what the last reading wants the broker to look at (an unsure
-- name split, amounts that don't add up, read by the backup method). Kept on
-- the driver so it survives a reload; cleared by Dismiss or the next reading.
alter table driver add column if not exists dash_notes jsonb;

create or replace function replace_dash_history(
  p_driver_id uuid, p_gaps jsonb, p_claims jsonb
) returns void as $$
begin
  delete from driver_insurance_gap where driver_id = p_driver_id;
  delete from driver_claim where driver_id = p_driver_id;

  insert into driver_insurance_gap (driver_id, gap_from, gap_to, reason)
  select p_driver_id, (g->>'from')::date, (g->>'to')::date, g->>'reason'
  from jsonb_array_elements(coalesce(p_gaps, '[]'::jsonb)) g;

  insert into driver_claim (
    driver_id, date_of_loss, at_fault_percent, whose,
    claim_driver_license, claim_driver_name, insurer, claim_number,
    tp_bi, tp_pd, ab, coll
  )
  select
    p_driver_id,
    (c->>'date_of_loss')::date,
    (c->>'at_fault_percent')::numeric,
    c->>'whose',
    c->>'claim_driver_license',
    c->>'claim_driver_name',
    c->>'insurer',
    c->>'claim_number',
    coalesce((c->>'tp_bi')::numeric, 0),
    coalesce((c->>'tp_pd')::numeric, 0),
    coalesce((c->>'ab')::numeric, 0),
    coalesce((c->>'coll')::numeric, 0)
  from jsonb_array_elements(coalesce(p_claims, '[]'::jsonb)) c;
end;
$$ language plpgsql;

commit;

-- After applying:
--
--   select count(*) from driver_insurance_gap;   -- 0
--   select count(*) from driver_claim;           -- 0
