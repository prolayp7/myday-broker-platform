-- broker_lead_history — now reads purely from lead_assignment.
--
-- Replaces the 0022 version (create or replace, same name + same return shape,
-- so the route/service/repo calling it are UNCHANGED) which had to merge two
-- sources — lead_delivery plus a JSON-scan of audit_log's lead_reassigned
-- events — because at the time there was no single record of "every lead this
-- broker was assigned." Now there is: lead_assignment (0023) captures every
-- assignment event, system AND admin, in one indexed table. So this collapses
-- to a single straightforward read.
--
-- Why this is strictly better than 0022:
--   * One source, no union — a lead assigned to this broker appears exactly
--     because there's a lead_assignment row for it, full stop.
--   * Uses idx_lead_assignment_broker (broker_id, assigned_at desc) — no
--     full-scan of audit_log filtering on detail->>'new_broker_id' (which had
--     no supporting index).
--   * Carries the real reason ('Initial Claim' / 'Admin Claim' /
--     'Admin Reassign') instead of a coarse 'system' / 'admin' guess.
--
-- Return shape is IDENTICAL to 0022 (lead_id, name, phone, email, source,
-- stage, received_at, delivered_by) so nothing downstream changes — with one
-- honest upgrade: delivered_by now carries the precise reason string rather
-- than just 'system'/'admin'. The route already returns these rows as-is, and
-- no live frontend consumer exists yet (BrokerLeadHistory.tsx is still mock),
-- so this is a safe shape to firm up now.
--
-- NOTE ON HISTORY DEPTH: because lead_assignment was backfilled ONLY from
-- lead_delivery (0023), pre-migration admin reassignments are not retroactively
-- present — same best-effort-for-old-data / complete-going-forward tradeoff
-- documented in 0023. Every assignment from 0024/0025 onward is fully recorded.

create or replace function broker_lead_history(p_broker_id uuid)
returns table (
  lead_id uuid,
  name text,
  phone text,
  email text,
  source text,
  stage text,
  received_at timestamptz,
  delivered_by text
) as $$
  select
    l.id, l.name, l.phone, l.email, l.source, l.stage,
    la.assigned_at as received_at,
    la.reason as delivered_by
  from lead_assignment la
  join lead l on l.id = la.lead_id
  where la.broker_id = p_broker_id
  order by la.assigned_at desc;
$$ language sql stable;
