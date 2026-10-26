-- assign_lead — now also writes the receiving broker's personal "New" row.
--
-- Replaces the function from 0024 (create or replace — same signature, same
-- behavior) with ONE further addition: each time it assigns a lead, it also
-- inserts an ARRIVAL row into lead_stage_history (0029) — from_stage = null,
-- to_stage = 'New', broker_id = the broker who just received it. Same
-- transaction as the lead_delivery and lead_assignment inserts, so this can
-- never be orphaned from the assignment it belongs to.
--
-- CRITICAL: this does NOT touch lead.stage. lead.stage is untouched by
-- assignment — it's whatever it already was (normally 'New' for a fresh
-- lead's first assignment, since nothing has changed it yet). This insert is
-- purely the BROKER'S personal record of "the lead landed in my inbox now" —
-- see 0029's header for the full reasoning on why this is a parallel,
-- per-broker timeline and never the shared pipeline value.
--
-- Everything else below is IDENTICAL to 0024 — only the two lead_stage_history
-- inserts (marked NEW) are added.

create or replace function assign_lead(p_lead_id uuid)
returns uuid as $$
declare
  v_group_id uuid;
  v_last_reset_at timestamptz;
  v_last_assigned_broker_id uuid;
  v_broker_id uuid;
  v_gate_open boolean;
begin
  select group_id into v_group_id from lead where id = p_lead_id;
  if v_group_id is null then
    return null;
  end if;

  perform pg_advisory_xact_lock(hashtext('broker_group_rotation'), hashtext(v_group_id::text));

  insert into broker_group_rotation (group_id)
  values (v_group_id)
  on conflict (group_id) do nothing;

  select last_assigned_broker_id into v_last_assigned_broker_id
  from broker_group_rotation
  where group_id = v_group_id;

  select last_reset_at into v_last_reset_at from lead_allocation_state;

  -- STEP 2 — Group 1: eligible brokers under their own daily_cap.
  select b.id into v_broker_id
  from broker b
  join broker_group bg on bg.id = b.group_id
  where b.group_id = v_group_id
    and bg.is_recycle_bin = false
    and b.is_active = true
    and b.is_blocked_by_admin = false
    and (b.eligible_from is null or b.eligible_from <= now())
    and (
      select count(*) from lead_delivery ld
      where ld.broker_id = b.id and ld.delivered_at >= v_last_reset_at
    ) < b.daily_cap
  order by
    (b.id <= coalesce(v_last_assigned_broker_id, '00000000-0000-0000-0000-000000000000'::uuid)),
    b.id
  limit 1;

  if v_broker_id is not null then
    insert into lead_delivery (lead_id, broker_id) values (p_lead_id, v_broker_id);

    insert into lead_assignment (lead_id, broker_id, reason, reassigned_by)
    values (p_lead_id, v_broker_id, 'Initial Claim', null);

    -- NEW: this broker's personal "New" — their own clock, independent of
    -- lead.stage.
    insert into lead_stage_history (lead_id, broker_id, from_stage, to_stage)
    values (p_lead_id, v_broker_id, null, 'New');

    update lead
    set assigned_broker_id = v_broker_id,
        assigned_at = now(),
        assigned_by = 'system'
    where id = p_lead_id;

    update broker_group_rotation
    set last_assigned_broker_id = v_broker_id
    where group_id = v_group_id;

    insert into audit_log (actor_broker_id, action, entity_type, entity_id, detail)
    values (null, 'lead_assigned', 'lead', p_lead_id::text,
            jsonb_build_object('broker_id', v_broker_id, 'tier', 'group_1', 'actor', 'system'));

    return v_broker_id;
  end if;

  -- STEP 3 — the GATE.
  select exists (
    select 1
    from broker b
    join broker_group bg on bg.id = b.group_id
    where b.group_id = v_group_id
      and bg.is_recycle_bin = false
      and b.is_active = true
      and b.is_blocked_by_admin = false
      and (b.eligible_from is null or b.eligible_from <= now())
  ) and not exists (
    select 1
    from broker b
    join broker_group bg on bg.id = b.group_id
    where b.group_id = v_group_id
      and bg.is_recycle_bin = false
      and b.is_active = true
      and b.is_blocked_by_admin = false
      and (b.eligible_from is null or b.eligible_from <= now())
      and (
        select count(*) from lead_delivery ld
        where ld.broker_id = b.id and ld.delivered_at >= v_last_reset_at
      ) < b.daily_cap
  ) into v_gate_open;

  if not v_gate_open then
    return null;
  end if;

  -- STEP 4 — Group 2: ready_for_more brokers under their own max_cap.
  select b.id into v_broker_id
  from broker b
  join broker_group bg on bg.id = b.group_id
  where b.group_id = v_group_id
    and bg.is_recycle_bin = false
    and b.is_active = true
    and b.is_blocked_by_admin = false
    and (b.eligible_from is null or b.eligible_from <= now())
    and b.ready_for_more = true
    and (
      select count(*) from lead_delivery ld
      where ld.broker_id = b.id and ld.delivered_at >= v_last_reset_at
    ) < b.max_cap
  order by
    (b.id <= coalesce(v_last_assigned_broker_id, '00000000-0000-0000-0000-000000000000'::uuid)),
    b.id
  limit 1;

  if v_broker_id is not null then
    insert into lead_delivery (lead_id, broker_id) values (p_lead_id, v_broker_id);

    insert into lead_assignment (lead_id, broker_id, reason, reassigned_by)
    values (p_lead_id, v_broker_id, 'Initial Claim', null);

    -- NEW: this broker's personal "New" — their own clock, independent of
    -- lead.stage.
    insert into lead_stage_history (lead_id, broker_id, from_stage, to_stage)
    values (p_lead_id, v_broker_id, null, 'New');

    update lead
    set assigned_broker_id = v_broker_id,
        assigned_at = now(),
        assigned_by = 'system'
    where id = p_lead_id;

    update broker_group_rotation
    set last_assigned_broker_id = v_broker_id
    where group_id = v_group_id;

    insert into audit_log (actor_broker_id, action, entity_type, entity_id, detail)
    values (null, 'lead_assigned', 'lead', p_lead_id::text,
            jsonb_build_object('broker_id', v_broker_id, 'tier', 'group_2', 'actor', 'system'));

    return v_broker_id;
  end if;

  -- STEP 5 — nobody eligible in either tier. Record WHY; lead stays unassigned.
  insert into audit_log (actor_broker_id, action, entity_type, entity_id, detail)
  values (null, 'lead_unassigned', 'lead', p_lead_id::text,
          jsonb_build_object('group_id', v_group_id, 'reason', 'no_eligible_broker_in_either_tier'));

  return null;
end;
$$ language plpgsql;
