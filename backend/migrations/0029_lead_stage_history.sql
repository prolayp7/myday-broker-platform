-- lead_stage_history — PER-BROKER pipeline journey.
--
-- Answers "at what time did this lead reach each stage, UNDER A GIVEN BROKER."
-- Deliberately NOT a single shared timeline for the lead — it is scoped to
-- (lead_id, broker_id). Design decision (2026-07-16): once a lead is assigned
-- to a broker, it becomes THAT BROKER'S lead. The moment it lands in their
-- inbox is THEIR personal "New" — their own clock starting at zero — even if
-- the lead's real, shared pipeline position (lead.stage) is already further
-- along (e.g. a mid-pipeline reassignment). If the lead later moves to a
-- different broker, that broker gets their OWN fresh "New" row; the previous
-- broker's rows are untouched, permanent history of what happened while it
-- was genuinely their lead. A lead bouncing back to a broker who held it
-- before writes a SECOND "New" for them — each stretch of ownership is its
-- own honest record, not merged.
--
-- CRITICAL: this table NEVER drives lead.stage, and lead.stage NEVER drives
-- this table's to_stage value on arrival. lead.stage is the single shared
-- truth for the whole app (Admin Leads screen, filters, round-robin
-- eligibility, everything) — reassignment must never reset or fake it. This
-- table is a PARALLEL, per-broker record of personal progress, allowed to
-- say 'New' for broker B while lead.stage genuinely says 'Under Review'.
--
-- Two kinds of rows:
--   1. ARRIVAL — written by assign_lead (system) or admin_assign_lead (admin)
--      the instant a broker receives the lead, alongside the lead_assignment
--      row for the same event. from_stage = null, to_stage = 'New'.
--   2. PROGRESS — written by PATCH /leads/{id}/stage when the CURRENT holder
--      moves the lead forward (or anywhere — free-form, no transition rules
--      per 2026-07-16 decision). from_stage = the prior value, to_stage = the
--      new one. Also updates the real lead.stage in the same request.
--
-- APPEND-ONLY, same as lead_assignment/audit_log — no updated_at, a row is
-- never edited. Answers, permanently, for any lead a year from now: exactly
-- when each broker who ever held it received it, and every stage they
-- personally moved it through while it was theirs.

create table lead_stage_history (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references lead(id),
  -- Whose personal timeline this row belongs to — the broker who held the
  -- lead at the moment this stage fact happened. Never the "wrong" broker:
  -- PATCH /leads/{id}/stage only ever writes rows for lead.assigned_broker_id
  -- at request time (enforced by the route, an ownership guard).
  broker_id uuid not null references broker(id),
  -- Null only on an ARRIVAL row (to_stage = 'New') — there is no "before" the
  -- moment this broker first has it. Every PROGRESS row has a real from_stage.
  from_stage text,
  to_stage text not null,
  changed_at timestamptz not null default now(),
  created_at timestamptz not null default now()
);

-- "This broker's full journey across every lead they've held, in order" — the
-- shape Sarah's report reads: filter by broker, order by time.
create index idx_lead_stage_history_broker on lead_stage_history(broker_id, changed_at);
-- "This lead's per-broker history" — used when displaying one lead's full
-- story (every broker who's held it, and what each did).
create index idx_lead_stage_history_lead on lead_stage_history(lead_id, changed_at);

alter table lead_stage_history enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) can access
-- this table, consistent with lead / lead_assignment.
