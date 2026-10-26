-- Group capacity read-model for the admin allocation screens.
--
-- Powers two UI needs (docs/round-robin-allocation-design.md screens):
--   1. The reassign/assign dialog's per-broker "6 / 10 today" load bars.
--   2. The group capacity view (upgrade to the group detail screen): the
--      same per-broker load, plus the group's leads-today / unassigned totals.
--
-- Read-only. Counts real rows anchored to the single allocation clock
-- (lead_allocation_state.last_reset_at), exactly like assign_lead's own cap
-- check — so "today" here means the same window the assignment engine uses,
-- never drifting from it. Done as ONE function returning a JSON blob rather
-- than N per-broker PostgREST count queries (which would be N+1 and couldn't
-- share the reset anchor cleanly).

create or replace function group_capacity(p_group_id uuid)
returns jsonb as $$
declare
  v_last_reset_at timestamptz;
  v_brokers jsonb;
  v_leads_today int;
  v_unassigned int;
begin
  select last_reset_at into v_last_reset_at from lead_allocation_state;

  -- Per-broker: their delivered-today count (system deliveries only, from
  -- lead_delivery — the immutable cap source, §3) alongside the settings the
  -- capacity view shows. Excludes recycle-bin/archived brokers — they're not
  -- part of a working group's live capacity. One row per broker as a JSON
  -- array, ordered by name for stable display.
  select coalesce(jsonb_agg(b_row order by b_row->>'name'), '[]'::jsonb)
  into v_brokers
  from (
    select jsonb_build_object(
      'id', b.id,
      'name', b.name,
      'delivered_today', (
        select count(*) from lead_delivery ld
        where ld.broker_id = b.id and ld.delivered_at >= v_last_reset_at
      )
    ) as b_row
    from broker b
    where b.group_id = p_group_id
      and b.is_archived = false
  ) s;

  -- Group totals for "today" (since the last reset). leads_today counts every
  -- lead in the group created since the reset; unassigned is the live pool
  -- (assigned_broker_id is null) regardless of when created — a lead stuck
  -- from an earlier day is still waiting now, so it belongs in the count the
  -- admin acts on.
  select count(*) into v_leads_today
  from lead
  where group_id = p_group_id and created_at >= v_last_reset_at;

  select count(*) into v_unassigned
  from lead
  where group_id = p_group_id and assigned_broker_id is null;

  return jsonb_build_object(
    'group_id', p_group_id,
    'last_reset_at', v_last_reset_at,
    'leads_today', v_leads_today,
    'unassigned', v_unassigned,
    'brokers', v_brokers
  );
end;
$$ language plpgsql;
