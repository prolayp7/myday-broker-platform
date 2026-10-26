-- Lead assignment — CUSTODY JOURNEY.
--
-- The permanent, append-only trail of who a lead was assigned to and when.
-- One row per assignment EVENT: each system round-robin allocation, each
-- admin hand-out of a pooled lead, each admin move of a held lead. Ordered by
-- assigned_at, a lead's rows ARE its full journey top to bottom.
--
-- Why this exists (the gap it closes):
--   * lead.assigned_broker_id only ever holds the CURRENT holder — every prior
--     holder is lost the moment a reassignment overwrites it.
--   * lead_delivery (0018) records only SYSTEM deliveries and is load-bearing
--     for the daily cap — it must never be repurposed as general history.
--   * audit_log records admin reassignments only as loose JSON with no
--     old_broker_id and no index — unfit as a queryable custody source.
-- This table is the one real answer to "who has held this lead, in what order,
-- and why" — permanently, survivng any later reassignment.
--
-- APPEND-ONLY: a row is never updated or deleted (no updated_at, same rule as
-- audit_log). "Current holder" stays denormalized on lead.assigned_broker_id;
-- this table is the trail behind that snapshot, not a replacement for it.
--
-- NO unassigned_at / holding-windows: leads change hands rarely, so an ordered
-- event log is enough. We record the SEQUENCE of who-got-it-when, not the exact
-- duration each broker held it. If duration reporting is ever needed, it can be
-- derived from consecutive rows' assigned_at — no schema change required.
--
-- CUSTODY ONLY — no money here. Moving who WORKS a lead is a different fact from
-- moving who OWES for it (the charge lives once on lead itself, per the future
-- 'one charge per lead, ever' billing rule). Keeping this table charge-free is
-- deliberate.

create table lead_assignment (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references lead(id),
  -- The broker the lead was assigned to at THIS event. Never updated — a later
  -- move writes a NEW row, it does not rewrite this one.
  broker_id uuid not null references broker(id),
  -- When this assignment happened. Ordering a lead's rows by this column
  -- reconstructs its journey. Defaults to now() for live writes; the backfill
  -- below sets it explicitly from the original delivery time.
  assigned_at timestamptz not null default now(),
  -- Why this assignment happened, resolved PER-LEAD at the moment it occurs:
  --   'Initial Claim'  — the round-robin engine assigned it automatically
  --                      (system, no human). Written by assign_lead (0019).
  --   'Admin Claim'    — an admin handed out a POOLED (previously unassigned)
  --                      lead: a manual pool-drain, not a move off anyone.
  --   'Admin Reassign' — an admin moved an ALREADY-HELD lead to a different
  --                      broker.
  -- 'Admin Claim' vs 'Admin Reassign' is decided by whether the lead was
  -- unassigned or held at the instant of the admin action (see
  -- admin_assign_lead, migrations/0024). Future workflows (offboarding,
  -- stale-reclaim) will extend this enum when they exist to write them —
  -- deliberately NOT pre-modeled here so no value is unreachable.
  reason text not null check (
    reason in ('Initial Claim', 'Admin Claim', 'Admin Reassign')
  ),
  -- Which admin performed this. NULL for 'Initial Claim' — that's the system
  -- round-robin engine, which has no human actor. Set for both admin reasons.
  reassigned_by uuid references broker(id),
  -- Shared id stamped on every row produced by a single BULK admin action
  -- (e.g. "give all 30 of this broker's leads to Sarah" during offboarding).
  -- NULL for an ordinary single assignment. Lets one mass-move be viewed as one
  -- event later, while each lead's journey row stays independently true. No FK:
  -- it groups rows within this table, it doesn't reference another table.
  batch_id uuid,
  -- Append-only: rows are inserted, never edited. No updated_at, on purpose.
  created_at timestamptz not null default now()
);

-- "This broker's whole history, newest first" — the broker_lead_history read
-- pattern (0024). broker_id filtered, assigned_at ordered.
create index idx_lead_assignment_broker on lead_assignment(broker_id, assigned_at desc);
-- "This lead's whole journey, oldest first" — the per-lead timeline view.
create index idx_lead_assignment_lead on lead_assignment(lead_id, assigned_at);
-- "Show me that one bulk move" — sparse (most rows are null), so a partial
-- index keeps it tiny and only covers real batches.
create index idx_lead_assignment_batch on lead_assignment(batch_id) where batch_id is not null;

alter table lead_assignment enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) can access
-- this table; direct anon/authenticated Supabase access is fully blocked,
-- consistent with lead / lead_delivery.

-- =====================================================================
-- BACKFILL — give every already-delivered lead its first journey row.
-- =====================================================================
-- Without this, every lead assigned BEFORE this migration would show an empty
-- journey (the table starts empty), even though it clearly has a holder. The
-- only trustworthy record of a pre-existing SYSTEM assignment is lead_delivery
-- (immutable, one row per delivered lead) — so seed one 'Initial Claim' row per
-- delivery, preserving the original delivered_at as assigned_at.
--
-- We deliberately backfill ONLY from lead_delivery (system deliveries), not
-- from audit_log admin-reassignment events: those are pre-this-feature, loosely
-- shaped, and reconstructing a precise ordered custody chain from them is not
-- reliably possible. The current holder is still correct on lead itself; from
-- this migration forward, every new assignment (system or admin) writes a real
-- row, so the journey is complete going forward and best-effort (system-only)
-- for pre-existing rows. This is a one-time seed, not re-runnable — running this
-- migration twice would double-seed; migrations are applied once.
insert into lead_assignment (lead_id, broker_id, assigned_at, reason, reassigned_by, created_at)
select ld.lead_id, ld.broker_id, ld.delivered_at, 'Initial Claim', null, ld.delivered_at
from lead_delivery ld;
