-- Admins must never receive leads from the round-robin.
--
-- assign_lead checked active, admin-blocked, eligible-date, recycle-bin and
-- caps — but never ROLE. So an active admin sitting in a working group with a
-- daily cap was just another broker as far as the rotation was concerned.
--
-- Nothing had been absorbed yet: lead_delivery held zero rows against an
-- admin, because the one admin sits in a group no Meta form routes to. It
-- would have become real the moment a form was mapped to that group, or an
-- admin was moved into a working group — neither of which is guarded against,
-- and groups are single-role today by convention rather than enforcement.
--
-- It had already happened once in practice. An admin created inside a
-- worker's group for a live check quietly absorbed the lead that check was
-- about, and the check passed for the wrong reason.
--
-- FOUR PLACES INSIDE THIS FUNCTION, and that is the whole risk of this
-- change. The eligibility predicate appears in level 1, in level 2, and TWICE
-- inside the gate query (its "is anyone eligible" and "is anyone under cap"
-- halves). A half-applied rule fails silently in opposite directions:
--
--   * gate excludes admins, a tier does not  -> overtime opens while an admin
--     still has room, so leads skip a broker who was entitled to them;
--   * a tier excludes admins, the gate does not -> the gate waits for an admin
--     to reach a cap they will never approach, and overtime never opens.
--
-- DELIBERATE ASSIGNMENT IS UNAFFECTED. admin_assign_lead is not touched here
-- (0073 owns its current definition): an admin can still be handed a lead by a
-- human, and the leads one holds today stay where they are. Only the automatic
-- rotation excludes them.
--
-- NO NEW COLUMN. role already carries this fact. A separate receives_leads
-- boolean would put the same rule in two places, with only one possible value
-- given that admins never receive leads.
--
-- Identical to 0071's assign_lead in every other respect.

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
    -- ADMINS ARE NOT IN THE ROTATION (0074).
    and b.role = 'broker'
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

  -- STEP 3 — the GATE. Group 2 opens only when every eligible Group-1 broker
  -- is at cap. Zero eligible brokers counts as CLOSED, not open: falling
  -- through would quietly route a broken group's leads to overtime volunteers
  -- and hide the real problem.
  select exists (
    select 1
    from broker b
    join broker_group bg on bg.id = b.group_id
    where b.group_id = v_group_id
      and bg.is_recycle_bin = false
      and b.is_active = true
      and b.is_blocked_by_admin = false
      -- ADMINS ARE NOT IN THE ROTATION (0074).
      and b.role = 'broker'
      and (b.eligible_from is null or b.eligible_from <= now())
  ) and not exists (
    select 1
    from broker b
    join broker_group bg on bg.id = b.group_id
    where b.group_id = v_group_id
      and bg.is_recycle_bin = false
      and b.is_active = true
      and b.is_blocked_by_admin = false
      -- ADMINS ARE NOT IN THE ROTATION (0074).
      and b.role = 'broker'
      and (b.eligible_from is null or b.eligible_from <= now())
      and (
        select count(*) from lead_delivery ld
        where ld.broker_id = b.id and ld.delivered_at >= v_last_reset_at
      ) < b.daily_cap
  ) into v_gate_open;

  if not v_gate_open then
    -- THE FIX. This branch used to `return null` silently, so a group with no
    -- eligible broker at all stranded its leads with nothing recorded
    -- anywhere. That is the failure class most worth hearing about: "everyone
    -- is at cap" fixes itself tomorrow, but "this group has nobody working"
    -- needs a person, and it was invisible.
    --
    -- A DISTINCT reason from step 5's, so the two are tellable apart when
    -- something finally reads this table.
    insert into audit_log (actor_broker_id, action, entity_type, entity_id, detail)
    values (null, 'lead_unassigned', 'lead', p_lead_id::text,
            jsonb_build_object('group_id', v_group_id,
                               'reason', 'no_eligible_broker_in_group'));
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
    -- ADMINS ARE NOT IN THE ROTATION (0074).
    and b.role = 'broker'
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

  -- STEP 5 — everyone is at cap and nobody could take overtime. Distinct from
  -- the gate-closed reason above: this group is working, it is simply full.
  insert into audit_log (actor_broker_id, action, entity_type, entity_id, detail)
  values (null, 'lead_unassigned', 'lead', p_lead_id::text,
          jsonb_build_object('group_id', v_group_id,
                             'reason', 'all_brokers_at_cap'));

  return null;
end;
$$ language plpgsql;
