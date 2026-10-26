-- One row per itemized loss/expense breakdown line on a claim — DASH's own
-- "KOL" cost-type codes (e.g. KOL16 = Other Property Damage to insured vehicle,
-- KOL18 = Loss of Use, KOL20 = Collision). A claim can have one or several; a
-- claim with none simply has zero rows here (same empty-is-default pattern as
-- the other DASH child tables).
--
-- Kept as its OWN child table — deliberately NOT folded into dash_claim and
-- NOT stored as JSONB — specifically so these amounts stay AGGREGATABLE across
-- every lead (e.g. "total loss-of-use payouts across all leads", "claims by
-- cost type"). This is the one place in the DASH domain where per-line
-- analyzability was explicitly prioritized over a flatter shape (confirmed with
-- the broker: this breakdown drives later decisions, so it must be queryable).
--
-- Dollar amounts kept AS PRINTED text (e.g. "$4,744.00"), consistent with
-- dash_claim's total_loss/total_expense — clean dollar strings, safe to cast
-- for aggregation later.
create table dash_claim_kol (
  id uuid primary key default gen_random_uuid(),
  dash_claim_id uuid not null references dash_claim(id),

  kol_code text,                    -- DASH's own code, e.g. "KOL16", "KOL18", "KOL20"
  kol_label text,                   -- human label as printed, e.g. "Loss of use"
  loss_amount text,                 -- e.g. "$4,744.00"
  expense_amount text,              -- e.g. "$0.00"

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_dash_claim_kol_dash_claim_id on dash_claim_kol(dash_claim_id);

alter table dash_claim_kol enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) can access
-- this table; direct anon/authenticated Supabase access is fully blocked.

create trigger trg_dash_claim_kol_updated_at
  before update on dash_claim_kol
  for each row
  execute function set_updated_at();
