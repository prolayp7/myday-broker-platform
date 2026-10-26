-- Premium capture, step 1 of the money work: make the four figures a broker
-- types on New Policy actually survive a refresh, and give commission
-- (a later stage) the two things it needs — a carrier and a rate.
--
-- WHAT IS BROKEN TODAY. premium_history has existed since 0055 with seven
-- money columns and NOT ONE LINE OF PYTHON touching it: no repository, no
-- service, no route, no schema. It is written by nothing and read by exactly
-- one thing, the Meta CAPI trigger below. So every Purchase event we send
-- Meta carries no `value` and no `currency` — both of which Meta marks
-- REQUIRED on a Purchase — because the trigger reads an empty table. Meta's
-- optimisation cannot see a single dollar of revenue we have written.
--
-- FOUR SECTIONS, ONE PASS. §4 rewrites fn_meta_signal_on_stage_history
-- because §3 renames the very columns it reads. Splitting them would leave a
-- window where that function selects NULL forever with no error and no log
-- line — the failure would be invisible until someone checked Events Manager
-- weeks later. A constraint and its dependent function are one change; 0051
-- set that precedent and this follows it.

-- ── 1. insurer ───────────────────────────────────────────────────────────
--
-- The carriers a broker can place business with, and what each pays us.
--
-- A LOOKUP TABLE, not a check constraint, for the same reason ad_platform
-- (0004) and community (0001) are: the list is driven by who the business
-- actually deals with and grows over time, so adding a carrier should be a
-- plain insert rather than a schema migration.
--
-- These 20 names live today in a hardcoded TypeScript array
-- (frontend/src/domain/newPolicy.ts) rendered straight into the New Policy
-- dropdown. That was fine while it was only a dropdown. It stops being fine
-- now that commission keys on the carrier: a rate cannot reference a string
-- that exists only in the browser.
--
-- THREE RATE COLUMNS, NOT A SECOND TABLE. The textbook shape here is an
-- insurer_commission_rate child table, one row per carrier per product. That
-- pays off when the product set grows — and it does not: Auto, Homeowners,
-- Tenants, with condo earning the homeowner rate. For a fixed set of three,
-- columns beat rows: one row per carrier instead of four, a flat admin grid
-- instead of a per-product editor, no join to read a rate, and no
-- half-configured carrier with some products rated and others missing.
create table insurer (
  id uuid primary key default gen_random_uuid(),

  -- The carrier's legal name, e.g. 'Intact Insurance Company'.
  name text not null unique,

  -- What this carrier pays us on each product, as a percentage (15 = 15%).
  --
  -- NULL IS MEANINGFUL: it says "we do not place that business with them",
  -- and the New Policy insurer dropdown reads it that way — a policy needing
  -- an auto rate simply does not list a carrier whose auto_rate_pct is null.
  -- A broker therefore cannot pick a carrier whose commission we would be
  -- unable to calculate, which is a whole class of error removed rather than
  -- validated against after the fact.
  auto_rate_pct numeric,
  -- Condo policies use THIS rate. A condo is owner-occupied property, and a
  -- separate condo rate would be a distinction nothing uses.
  homeowner_rate_pct numeric,
  -- Separate from homeowner because carriers genuinely pay a different
  -- percentage on tenant business.
  tenant_rate_pct numeric,

  -- Retire a carrier without deleting the row — premium rows reference it,
  -- and history must not break because we stopped placing business there.
  is_active boolean not null default true,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger trg_insurer_updated_at
  before update on insurer
  for each row execute function set_updated_at();

alter table insurer enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) reaches this
-- table, same rule as every other table in this schema.

-- Seeded from the frontend array this replaces, so nothing is lost in the
-- move. Rates are deliberately left NULL — an admin enters them, and until
-- they do, that carrier does not appear in the dropdown. That is the correct
-- failure: "set your rates up" rather than "here is a carrier we cannot pay
-- commission on".
insert into insurer (name) values
  ('Allstate Insurance Company of Canada'),
  ('Aviva General Insurance Company'),
  ('Certas Home and Auto Insurance Company'),
  ('CAA Insurance Company'),
  ('Definity Insurance Company'),
  ('Economical Insurance'),
  ('Gore Mutual Insurance Company'),
  ('Intact Insurance Company'),
  ('Jevco Insurance Company'),
  ('Pafco Insurance Company'),
  ('Pembridge Insurance Company'),
  ('RSA Insurance'),
  ('SGI Canada'),
  ('Sonnet Insurance Company'),
  ('TD Insurance'),
  ('The Co-operators General Insurance Company'),
  ('The Personal Insurance Company'),
  ('Travelers Insurance Company of Canada'),
  ('Wawanesa Mutual Insurance Company'),
  ('Zenith Insurance Company');

-- ── 2. lead.combined_policy_type — six values, not four ──────────────────
--
-- Every value in the old vocabulary assumed auto was present: 'Standalone'
-- meant auto-only, and the other three meant auto PLUS a property policy.
-- Standalone property business is real — a tenant policy or a home policy
-- with no auto attached — and the old list could not express it at all.
--
-- This column now drives two things, which is why it has to be right:
--   * which premium columns are REQUIRED before a lead can be marked Sold
--     (a Stage 2 guard), and
--   * which carriers the New Policy dropdown offers.
--
-- DROP, THEN REWRITE, THEN ADD — in that order, and the order is the whole
-- point.
--
-- A CHECK constraint is validated per STATEMENT, not deferred to commit. So
-- rewriting 'Standalone' to 'Auto Only' while the old constraint still stands
-- fails immediately, even inside a transaction — which is exactly what
-- happened on the first attempt at this migration, rolling the whole script
-- back. The column has to be unconstrained for the moment the data moves.
--
-- Dropped BY NAME rather than looked up. That first attempt assumed 0008 had
-- declared the constraint inline and let Postgres generate a name, so it
-- searched pg_constraint for one; the constraint is in fact already named
-- lead_combined_policy_type_check and the search pattern missed it, leaving
-- the old check in place to reject the new values. `if exists` keeps this
-- re-runnable.
alter table lead drop constraint if exists lead_combined_policy_type_check;

-- Idempotent-safe: rows already holding the new name simply match nothing.
update lead set combined_policy_type = 'Auto Only'
where combined_policy_type = 'Standalone';

alter table lead add constraint lead_combined_policy_type_check check (
  combined_policy_type in (
    'Auto Only', 'Home Only', 'Tenant Only',
    'Homeowners', 'Tenants', 'Condo'
  )
);

-- ── 3. premium_history — reshaped for what brokers actually type ─────────
--
-- MONTHLY IS WHAT THE BROKER ENTERS. The old columns were bare `numeric`
-- with nothing saying whether 200 meant $200 a month or $200 a year. That is
-- how a 12x error reaches Meta and nobody notices. The _monthly suffix makes
-- the unit part of the name; the annual figures are derived, never typed.
--
-- PROPERTY SPLITS INTO HOMEOWNER AND TENANT. One blended property figure
-- would have to consult lead.combined_policy_type to know which commission
-- rate applied — and that column is editable, so a stored commission would
-- depend on a value someone can change afterwards. Two columns, each paired
-- with exactly one rate, removes the question. Condo uses the homeowner pair.
--
-- GENERATED, NOT STORED — AND THIS REVERSES 0055's OWN DECISION. That
-- migration stored the totals deliberately, arguing they should be "locked in
-- permanently even if a later year's premiums change". The argument does not
-- hold: the inputs are immutable per row, so a generated total is exactly as
-- stable, and it recomputes only when this row's own figures change — which
-- is precisely when it should. What the old design actually bought was a
-- total that NOTHING computed or checked, free to disagree with its parts
-- with no error. This is the first use of `generated always` in this schema;
-- the established alternative here is a BEFORE trigger writing a real column
-- (set_updated_at, set_lead_phone_e164), which is the right tool when the
-- value depends on more than the row itself. These totals do not.
--
-- NO policy_year. 0055 was built as one-row-per-year for the life of a
-- customer. Renewals now run through their own pipeline with their own
-- record, so this table holds new business only: one row per lead, and a
-- second row would be a double-save rather than a legitimate later year.

alter table premium_history
  rename column current_auto_premium to current_auto_premium_monthly;
alter table premium_history
  rename column new_auto_premium to new_auto_premium_monthly;
alter table premium_history
  rename column current_property_premium to current_homeowner_premium_monthly;
alter table premium_history
  rename column new_property_premium to new_homeowner_premium_monthly;

alter table premium_history
  add column current_tenant_premium_monthly numeric,
  add column new_tenant_premium_monthly numeric;

-- The three stored totals go; generated ones replace them below.
alter table premium_history
  drop column total_current_premium,
  drop column total_new_premium,
  drop column savings;

-- One row per lead now, so the year is neither needed nor meaningful.
drop index if exists idx_premium_history_lead_year;
alter table premium_history drop column policy_year;
create unique index idx_premium_history_lead_id_unique
  on premium_history(lead_id);

alter table premium_history
  -- Which carrier wrote it. LOAD-BEARING, not descriptive: the commission
  -- rate varies by insurer, so without this the total cannot be calculated.
  add column insurer_id uuid references insurer(id),
  -- The insurer's own identifier, for reconciling against carrier
  -- statements. Cheap now, painful to backfill later.
  add column policy_number text,
  -- THE DATE EVERYTHING REPORTS ON. Set when the broker marks the policy
  -- Issued, and CHOSEN BY THEM rather than taken from the clock — a policy
  -- issued on the 30th but recorded on the 2nd must count in the right
  -- month. Editable afterwards: changing it moves which month this row
  -- lands in, and never re-runs a commission whose amount is already stored.
  add column issued_at date,
  -- When the policy actually starts, which can be weeks after it was issued.
  add column effective_date date;

create index idx_premium_history_insurer_id on premium_history(insurer_id);
-- The shape every "what did I write this month" query will use.
create index idx_premium_history_issued_at on premium_history(issued_at);

-- coalesce(...,0) so a HALF-FILLED ROW STILL TOTALS CORRECTLY. A broker fills
-- these in over several conversations; without it, one null column would make
-- the whole total null and the screen would show nothing rather than the
-- figure so far.
alter table premium_history
  add column current_auto_premium_annual numeric
    generated always as (current_auto_premium_monthly * 12) stored,
  add column new_auto_premium_annual numeric
    generated always as (new_auto_premium_monthly * 12) stored,
  add column current_homeowner_premium_annual numeric
    generated always as (current_homeowner_premium_monthly * 12) stored,
  add column new_homeowner_premium_annual numeric
    generated always as (new_homeowner_premium_monthly * 12) stored,
  add column current_tenant_premium_annual numeric
    generated always as (current_tenant_premium_monthly * 12) stored,
  add column new_tenant_premium_annual numeric
    generated always as (new_tenant_premium_monthly * 12) stored,

  add column total_current_premium_monthly numeric
    generated always as (
      coalesce(current_auto_premium_monthly, 0)
      + coalesce(current_homeowner_premium_monthly, 0)
      + coalesce(current_tenant_premium_monthly, 0)
    ) stored,
  add column total_new_premium_monthly numeric
    generated always as (
      coalesce(new_auto_premium_monthly, 0)
      + coalesce(new_homeowner_premium_monthly, 0)
      + coalesce(new_tenant_premium_monthly, 0)
    ) stored,

  add column total_current_premium_annual numeric
    generated always as (
      (coalesce(current_auto_premium_monthly, 0)
       + coalesce(current_homeowner_premium_monthly, 0)
       + coalesce(current_tenant_premium_monthly, 0)) * 12
    ) stored,
  -- THE NUMBER META RECEIVES as a Purchase event's value, and the one
  -- commission multiplies. Annual, not monthly: reporting a sale at a twelfth
  -- of its real worth would train Meta's optimisation on the wrong figure.
  add column total_new_premium_annual numeric
    generated always as (
      (coalesce(new_auto_premium_monthly, 0)
       + coalesce(new_homeowner_premium_monthly, 0)
       + coalesce(new_tenant_premium_monthly, 0)) * 12
    ) stored,

  -- The customer-facing "how much we saved you" figure, and one a broker
  -- repeats on calls. Free, because both sides are stored.
  add column savings_monthly numeric
    generated always as (
      (coalesce(current_auto_premium_monthly, 0)
       + coalesce(current_homeowner_premium_monthly, 0)
       + coalesce(current_tenant_premium_monthly, 0))
      - (coalesce(new_auto_premium_monthly, 0)
         + coalesce(new_homeowner_premium_monthly, 0)
         + coalesce(new_tenant_premium_monthly, 0))
    ) stored,
  add column savings_annual numeric
    generated always as (
      ((coalesce(current_auto_premium_monthly, 0)
        + coalesce(current_homeowner_premium_monthly, 0)
        + coalesce(current_tenant_premium_monthly, 0))
       - (coalesce(new_auto_premium_monthly, 0)
          + coalesce(new_homeowner_premium_monthly, 0)
          + coalesce(new_tenant_premium_monthly, 0))) * 12
    ) stored;

-- 0055 gave this table an updated_at column and no trigger to maintain it, so
-- the value has been frozen at insert time since the table was created. A
-- real gap, fixed here rather than left for whoever next wonders why an
-- edited row still reads as untouched.
create trigger trg_premium_history_updated_at
  before update on premium_history
  for each row execute function set_updated_at();

-- ── 4. The CAPI trigger, rewritten for the new columns ───────────────────
--
-- MUST LAND WITH §3. This function reads premium_history, and §3 renamed both
-- columns it used: total_new_premium (now total_new_premium_annual) and
-- policy_year (now gone, since there is one row per lead). Left untouched it
-- would not error — it would select NULL forever, and every Purchase event
-- would silently carry no value. No exception, no log line, nothing to notice
-- until someone opened Events Manager weeks later.
--
-- Two changes beyond the rename:
--   * ANNUAL, not monthly. total_new_premium_annual is the policy's real
--     worth; reporting a twelfth of it would train Meta's model on a figure
--     that is wrong by an order of magnitude.
--   * No `order by policy_year desc limit 1`. One row per lead means there is
--     nothing to order; the unique index on lead_id guarantees it.
--
-- The old comment claimed "the New Policy UI requires a premium to be entered
-- before 'Sold' can be reached at all, so by the time this row exists the
-- value is already there." That was never true — nothing enforced it, which
-- is exactly why Purchase events have been going out empty. Stage 2 adds the
-- guard that makes it true; until then the null is real and the sender is
-- expected to leave such a signal Pending rather than send it incomplete.
create or replace function fn_meta_signal_on_stage_history()
returns trigger as $$
declare
  v_platform text;
  v_total_new_premium numeric;
begin
  if new.to_stage not in ('Quote Done', 'Sold') then
    return new;
  end if;

  select source into v_platform from lead where id = new.lead_id;
  if v_platform <> 'meta' then
    return new;
  end if;

  if new.to_stage = 'Quote Done' then
    insert into meta_signal (lead_id, platform, event_name, triggered_by)
    values (new.lead_id, 'Meta', 'CompleteRegistration', new.broker_id);

  elsif new.to_stage = 'Sold' then
    -- This lead's premium row — one per lead, enforced by
    -- idx_premium_history_lead_id_unique.
    select total_new_premium_annual into v_total_new_premium
    from premium_history
    where lead_id = new.lead_id
    limit 1;

    insert into meta_signal (lead_id, platform, event_name, triggered_by, payload)
    values (
      new.lead_id, 'Meta', 'Purchase', new.broker_id,
      -- Not the final POST payload (that's built and hashed by the sender) —
      -- a small note-to-self so a 'Sold' lead with no premium row is visible
      -- immediately in this table, rather than only discovered when the
      -- sender tries to build a Purchase event with nothing to put in value.
      jsonb_build_object('total_new_premium_at_sale', v_total_new_premium)
    );
  end if;

  return new;
end;
$$ language plpgsql;
