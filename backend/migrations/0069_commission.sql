-- Commission: who earns what on an issued policy.
--
-- THE MODEL. The carrier pays the brokerage a percentage of the annual
-- premium (insurer.*_rate_pct, migration 0067). THAT payment is the pot — the
-- 100% — and three parties divide it: the broker who wrote it, PolicyScanner,
-- and the principal broker.
--
--   annual premium   $2,400
--   x carrier 12.5%  = $300        <- the pot
--       broker    50%  = $150
--       brokerage 25%  = $75
--       principal 25%  = $75
--                       ----
--                       $300
--
-- WHAT IS PER PRODUCT, AND WHAT IS NOT. The CARRIER rate differs by product
-- (12.5% auto, 20% property) — so a combined policy earns two different pots
-- and needs two commission rows. The SPLIT does not: a broker on 50% takes
-- half of each pot, whatever was sold. The broker's margin is his margin.
--
--   Auto:  $2,400 x 12.5% = $300 -> broker 50% = $150
--   Home:  $1,200 x 20%   = $240 -> broker 50% = $120
--
-- A DEFAULT PLUS OVERRIDES. brokerage_settings holds the house split every
-- broker gets. An experienced broker can be moved to 60/20/20 by setting the
-- three columns on their own row; null there means "use the default", which
-- is the ordinary case and stays true as the default changes.
--
-- ── the decision that shapes this whole table ───────────────────────────
--
-- COMMISSION STORES THE RATES IT USED. Every percentage that went into a
-- calculated commission is copied onto the row.
--
-- Without that, commission would have to join back to insurer, broker and
-- settings to explain itself — and all three are editable. A carrier
-- renegotiating in July, or the house split changing, would silently rewrite
-- what a policy sold in March had "earned": no edit to the policy, no audit
-- entry, the number just moves. Copying the rates makes a commission row a
-- permanent record of a calculation that already happened, and lets every
-- rate be edited freely as what it is: the terms for NEW business.
--
-- This is also why there is no rate-history table anywhere (decision
-- 2026-08-03): once a calculation keeps its own inputs, nothing needs to ask
-- what a rate USED to be.
--
-- CALCULATED AT ISSUED, never before. Sold means the customer agreed; Issued
-- means the carrier wrote it. Only the second is money.

begin;

-- ── 1. the house split ─────────────────────────────────────────────────
--
-- A single-row table, not a key/value store: these three numbers are one
-- fact — a split — and must be read and written together. Separate rows
-- would allow a half-applied change where the shares briefly do not total 100.
create table brokerage_settings (
  -- Fixed primary key, not a generated uuid: this table holds exactly one
  -- row, and a CHECK pinning the key to a known value is what makes a second
  -- insert impossible. Simpler than a unique index on a constant expression,
  -- and it gives the service a stable id to update without a lookup.
  id int primary key default 1 check (id = 1),

  broker_share_pct numeric not null,
  brokerage_share_pct numeric not null,
  principal_share_pct numeric not null,

  -- The three shares divide the pot exactly. Enforced in the database as well
  -- as the schema layer because this row is the default EVERY commission
  -- falls back to — a bad value here is wrong for every broker at once.
  constraint brokerage_settings_shares_total_100
    check (broker_share_pct + brokerage_share_pct + principal_share_pct = 100),

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger trg_brokerage_settings_updated_at
  before update on brokerage_settings
  for each row execute function set_updated_at();

alter table brokerage_settings enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) reaches this
-- table, same rule as every other table in this schema.

insert into brokerage_settings (broker_share_pct, brokerage_share_pct, principal_share_pct)
values (50, 25, 25);

-- ── 2. per-broker override ─────────────────────────────────────────────
--
-- Null means "use the house split" — the ordinary case, and it keeps
-- following the default if that changes. Only a broker on genuinely different
-- terms carries their own.
--
-- All three or none: a partial override would mix one custom share with two
-- defaults and could not total 100. Enforced in schemas/broker.py, where the
-- admin can be told which one is missing.
alter table broker
  add column broker_share_pct numeric,
  add column brokerage_share_pct numeric,
  add column principal_share_pct numeric;

comment on column broker.broker_share_pct is
  'Override of the house split in brokerage_settings. Null = use the default. A share of the CARRIER commission, not of the premium.';

-- ── 3. the commission itself ───────────────────────────────────────────
create table commission (
  id uuid primary key default gen_random_uuid(),

  lead_id uuid not null references lead(id) on delete cascade,
  broker_id uuid not null references broker(id),
  insurer_id uuid references insurer(id),

  -- One row PER PRODUCT, because the carrier rate is per product. A combined
  -- policy produces two rows, and they stay separately answerable ("what did
  -- we make on property this quarter").
  --
  -- Condo uses 'homeowner', matching the rate it earns — see REQUIRED_RATES
  -- in schemas/insurer.py, the source of truth for that mapping.
  product text not null check (product in ('auto', 'homeowner', 'tenant')),

  -- WHAT THE CARRIER PAID on this product line — the pot the three shares
  -- divide. Written by the service, which reads the premium and the carrier's
  -- rate at Issued; neither of those is copied here, because premium_history
  -- already holds them and this row's job is the SPLIT, not a second record
  -- of the sale.
  carrier_commission numeric not null check (carrier_commission >= 0),

  -- THE SHARES AS THEY WERE. These are the reason the row can still explain
  -- itself years later: move a broker from 50% to 60% and every policy
  -- written from that day earns 60, while everything before it keeps saying
  -- 50. Without them the figures would silently restate themselves against
  -- whatever broker.broker_share_pct happens to say today.
  broker_share_pct numeric not null check (broker_share_pct >= 0),
  brokerage_share_pct numeric not null check (brokerage_share_pct >= 0),
  principal_share_pct numeric not null check (principal_share_pct >= 0),

  -- The three cuts. GENERATED, not written by the caller: a figure that is
  -- stored rather than computed is one bad write away from disagreeing with
  -- the share it claims to come from, and this is money.
  broker_commission numeric
    generated always as (carrier_commission * broker_share_pct / 100) stored,
  brokerage_commission numeric
    generated always as (carrier_commission * brokerage_share_pct / 100) stored,
  principal_commission numeric
    generated always as (carrier_commission * principal_share_pct / 100) stored,

  -- A book transferred in from another brokerage. Just a label — it changes
  -- no rate (decision 2026-08-03); it marks where the business came from so
  -- transferred volume reports separately from new business.
  is_transferred boolean not null default false,

  -- When the policy was issued, which is when this was calculated.
  issued_at date not null,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- One commission per lead per product. Re-issuing UPDATES this row, never
-- adds a second — "always take the most recent, never double" (decision
-- 2026-08-03). The unique index makes that a guarantee rather than a rule the
-- calling code is trusted to follow.
create unique index idx_commission_lead_product
  on commission (lead_id, product);

-- The two reads this table exists for: one broker's earnings over a period,
-- and everything earned in a period.
create index idx_commission_broker_issued on commission (broker_id, issued_at);
create index idx_commission_issued on commission (issued_at);

create trigger trg_commission_updated_at
  before update on commission
  for each row execute function set_updated_at();

alter table commission enable row level security;

commit;
