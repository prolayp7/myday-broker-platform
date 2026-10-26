-- Broker lead-history read-model — "every lead this broker has ever taken."
--
-- Powers the Admin > Brokers > [broker] leads-taken screen. Deliberately
-- reads the PERMANENT record, not "leads currently assigned to me"
-- (lead.assigned_broker_id only holds the CURRENT holder) — a lead later
-- reassigned away from this broker must still show here, since they really
-- worked it that day.
--
-- Two sources must be merged, or this silently misses real deliveries:
--   1. lead_delivery — every lead the SYSTEM delivered to this broker
--      (migrations/0018/0019). Immutable, never rewritten.
--   2. audit_log where action='lead_reassigned' and detail->>'new_broker_id'
--      = this broker — every lead an ADMIN moved TO this broker
--      (services/lead.py::reassign_lead). Reassignment deliberately does NOT
--      write to lead_delivery (design §3: it must stay invisible to the daily
--      cap), so this is the ONLY record of an admin-driven delivery. Skipping
--      this source would silently drop every reassigned-in lead from a
--      broker's history.
--
-- Each source contributes (lead_id, received_at, delivered_by). If the same
-- lead appears in both (rare: system delivered it, then later an admin moved
-- it away and back), both events are real and both are kept — a broker who
-- received a lead twice, at different times, genuinely worked it twice.

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
    combined.received_at,
    combined.delivered_by
  from (
    -- System deliveries — the immutable cap-source record.
    select lead_id, delivered_at as received_at, 'system' as delivered_by
    from lead_delivery
    where broker_id = p_broker_id

    union all

    -- Admin reassignments TO this broker — audit_log is the only record of
    -- these (design §3: reassignment never touches lead_delivery).
    select
      (entity_id)::uuid as lead_id,
      created_at as received_at,
      'admin' as delivered_by
    from audit_log
    where action = 'lead_reassigned'
      and entity_type = 'lead'
      and (detail->>'new_broker_id')::uuid = p_broker_id
  ) combined
  join lead l on l.id = combined.lead_id
  order by combined.received_at desc;
$$ language sql stable;
