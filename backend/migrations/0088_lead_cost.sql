-- Lead cost: what a broker owes for the leads the system gave them.
--
-- The brokerage buys leads. A lead the ROUND-ROBIN DELIVERS costs a flat
-- amount set by an admin; a lead a broker ENTERS BY HAND costs nothing,
-- because nobody bought it. Each month a broker owes the sum of what they were
-- given, less any leads that should not have been charged — a wrong number, a
-- duplicate, a test.
--
-- 0008_lead.sql:3-6 deferred exactly this work: "billing fields (company_cost,
-- broker_charge, payment_status, etc.) are deliberately deferred to their own
-- migration". This is that migration, and it is smaller than that comment
-- expected — because lead_delivery and lead_assignment already answer almost
-- everything billing needs to know.
--
-- ── WHAT IS *NOT* HERE, and why ─────────────────────────────────────────
--
-- NO billing-owner column. Who pays is a QUESTION, not a stored fact:
--
--     month open   -> lead.assigned_broker_id      (the cost follows the lead)
--     month frozen -> the last lead_assignment row before the freeze instant
--
-- lead_assignment is append-only (0023), so "who held this lead at any past
-- moment" is already answerable. A billed_broker_id column would have to be
-- kept in step by every reassignment path forever, to store something the
-- database can already derive.
--
-- NO adjustment table. One delivery takes at most one credit, so a table would
-- mean storing the broker and the amount a SECOND time — and two copies of one
-- fact is how they drift apart.
--
-- NO month-close job. A month is open until the 5th of the next month and
-- frozen after; that is a date comparison, so nothing has to run.
--
-- NO rebuild of assign_lead. See part (c).

begin;

-- ── 1. The price ────────────────────────────────────────────────────────
--
-- A singleton, the same shape as commission_settings (0084) and
-- lead_allocation_state (0018): a fixed id pinned by a CHECK, so a second row
-- is impossible and the service has a stable id to update against.
create table lead_cost_settings (
  id int primary key default 1 check (id = 1),

  -- numeric(10,2) rather than bare numeric: this is a price somebody types in
  -- dollars and cents, not a computed figure that needs open precision.
  cost_per_lead numeric(10,2) not null check (cost_per_lead >= 0),

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger trg_lead_cost_settings_updated_at
  before update on lead_cost_settings
  for each row execute function set_updated_at();

alter table lead_cost_settings enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) reaches this
-- table, the same rule as every other table in this schema.

-- SEEDED WITH A REAL NUMBER, not null — 0084's reasoning applies unchanged:
-- the calculation has to answer on its first day, and a null price would need
-- special-casing in every read for a state that exists only until an admin
-- first opens the screen.
insert into lead_cost_settings (cost_per_lead) values (20);


-- ── 2. The price, and any credit, on the DELIVERY ───────────────────────
--
-- WHY HERE AND NOT ON lead. A cost exists because a delivery happened, so the
-- delivery is where it belongs:
--
--   * price and broker are stamped together, in one row, in one insert —
--     they cannot disagree
--   * a manual lead has NO delivery row, so it is free by construction rather
--     than by a rule somebody has to remember to apply
--   * lead_delivery rows are never updated after insert (0018), so a
--     reassignment cannot rewrite what was paid
--   * no billing column is added to every lead in the system
alter table lead_delivery
  add column cost_at_delivery numeric(10,2) check (cost_at_delivery >= 0),

  -- THE CREDIT — three nullable columns, not a table. One delivery takes at
  -- most one credit and there are no partial credits: a lead is charged or it
  -- is not. Undoing a credit clears these three.
  add column credit_reason text check (credit_reason in
    ('Wrong number', 'Duplicate', 'Test lead', 'Other')),
  add column credited_by uuid references broker(id),
  add column credited_at timestamptz;

-- ALL THREE OR NONE. Three independent nullable columns can otherwise hold a
-- credit with no reason, or a reason with nobody attached — states nothing in
-- the code would produce, but nothing in the schema forbade either. A
-- half-written credit is money coming off a bill that nobody can account for.
alter table lead_delivery
  add constraint lead_delivery_credit_complete check (
    (credit_reason is null and credited_by is null and credited_at is null)
    or
    (credit_reason is not null and credited_by is not null and credited_at is not null)
  );

-- The month report reads deliveries by time. lead_delivery already has
-- (broker_id, delivered_at) for the daily cap; this covers the billing query,
-- which spans all brokers for a month rather than one broker for a day.
create index idx_lead_delivery_delivered_at on lead_delivery(delivered_at);


-- ── 3. Stamping the price ───────────────────────────────────────────────
--
-- A TRIGGER, NOT A REBUILD OF assign_lead. That function is 497 lines with two
-- identical `insert into lead_delivery` sites (group 1 and group 2), and
-- adding a column to each would mean copying the whole thing forward as the
-- seventh link in its chain (0019 -> 0024 -> 0030 -> 0071 -> 0074 -> 0082 ->
-- here). Every rebuild is a chance to drop a line from the code that decides
-- which broker gets a lead — a far worse failure than anything billing can
-- cause.
--
-- The trigger fires on both sites, adds nothing to the allocation path, and
-- cannot diverge from it. It also catches any future writer of lead_delivery
-- without that code having to know billing exists.
create or replace function set_lead_delivery_cost()
returns trigger as $$
begin
  -- `is null` rather than unconditional: a caller that supplies its own price
  -- — a test, a backfill — keeps it.
  if new.cost_at_delivery is null then
    -- A SCALAR SUBQUERY, so a missing settings row yields NULL rather than
    -- raising. Allocation must never fail because billing is misconfigured:
    -- a broker not receiving a customer is a real cost, an unpriced delivery
    -- is a reporting problem. The unpriced case is surfaced in the report as
    -- its own count, never as $0.
    new.cost_at_delivery := (
      select cost_per_lead from lead_cost_settings where id = 1
    );
  end if;
  return new;
end;
$$ language plpgsql;

create trigger trg_lead_delivery_cost
  before insert on lead_delivery
  for each row execute function set_lead_delivery_cost();

commit;
