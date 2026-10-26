-- Two assignment fixes, both found by an external code review of b57eafd.
--
-- ── 1. An admin could hand a live customer to a broker who no longer works
--       leads ────────────────────────────────────────────────────────────
--
-- admin_assign_lead checked ONE thing about the target: is_archived. The
-- SYSTEM path (assign_lead, migration 0019) excludes four:
--
--     bg.is_recycle_bin = false
--     b.is_active = true
--     b.is_blocked_by_admin = false
--     eligible_from arrived
--
-- so an admin could place a lead somewhere the round-robin would never send
-- one. Measured at the time of writing: NINE brokers sitting in a Recycle Bin
-- group were reachable this way. A Recycle Bin group is how a broker is
-- offboarded — the lead would land with someone who is gone, and nothing
-- downstream would flag it.
--
-- WHAT IS NOW REFUSED, and what deliberately is not:
--
--   is_archived        refused (already was)
--   is_active = false  REFUSED — deactivated; they are not working leads
--   recycle bin group  REFUSED — offboarded; the same thing structurally
--   is_blocked_by_admin  STILL ALLOWED, deliberately. 0025 records the
--                        reasoning: blocking gates SYSTEM delivery, and an
--                        admin explicitly choosing that broker is a considered
--                        override of it. Left intact rather than quietly
--                        reversed.
--   eligible_from       STILL ALLOWED. A broker onboarded today starts
--                        tomorrow; an admin handing them their first lead
--                        early is a reasonable thing to want.
--
-- ONLY THE TARGET IS CHECKED. Moving a lead AWAY from a Recycle Bin broker is
-- exactly what offboarding needs — guarding both directions would strand every
-- lead the departing broker was holding, which is the opposite of the point.
--
-- ── 2. A stranded lead with no reason recorded ──────────────────────────
--
-- assign_lead's step 5 writes a 'lead_unassigned' audit row explaining why a
-- lead could not be placed. But step 3 returns BEFORE reaching it when the
-- gate is closed — so the case where a group has NO eligible broker at all
-- (everyone blocked, deactivated, or not yet started) left the lead sitting
-- there with nothing written anywhere.
--
-- That is the worst of the two failure classes to lose: "everyone is at cap"
-- is self-correcting tomorrow, while "this group has nobody working" is a
-- configuration problem that needs a human, and silence is exactly how it goes
-- unnoticed. Both paths now record a reason, and the reasons are DISTINCT so
-- they can be told apart.

-- ─── 1. admin_assign_lead: match the system path's eligibility ───────────
create or replace function admin_assign_lead(
  p_lead_id uuid,
  p_broker_id uuid,
  p_admin_id uuid,
  p_batch_id uuid default null
)
returns uuid as $$
declare
  v_current_broker_id uuid;
  v_lead_exists boolean;
  v_target_archived boolean;
  v_target_active boolean;
  v_target_in_recycle_bin boolean;
  v_reason text;
begin
  select true, assigned_broker_id
  into v_lead_exists, v_current_broker_id
  from lead
  where id = p_lead_id;

  if not found then
    raise exception 'Lead not found';
  end if;

  -- One read for every fact about the target, joined to its group so the
  -- recycle-bin check needs no second query.
  select b.is_archived, b.is_active, bg.is_recycle_bin
  into v_target_archived, v_target_active, v_target_in_recycle_bin
  from broker b
  join broker_group bg on bg.id = b.group_id
  where b.id = p_broker_id;

  if not found then
    raise exception 'Target broker not found';
  end if;

  -- Each refusal names the actual state, because these messages are shown to
  -- the admin verbatim (services/lead.py turns P0001 into a 400 carrying the
  -- text). "That broker can't receive leads" would leave them guessing which
  -- of four things to fix.
  if v_target_archived then
    raise exception 'That broker is archived and can''t receive leads';
  end if;
  if not v_target_active then
    raise exception 'That broker is deactivated and can''t receive leads';
  end if;
  if v_target_in_recycle_bin then
    raise exception 'That broker is in the Recycle Bin and can''t receive leads';
  end if;

  if v_current_broker_id is not null and v_current_broker_id = p_broker_id then
    raise exception 'That lead is already assigned to that broker';
  end if;

  v_reason := case when v_current_broker_id is null then 'Admin Claim' else 'Admin Reassign' end;

  -- === All writes below are one transaction (a plpgsql function body). ===

  -- 1. Move the current holder.
  update lead
  set assigned_broker_id = p_broker_id,
      assigned_at = now(),
      assigned_by = 'admin'
  where id = p_lead_id;

  -- 2. Append the journey row — the permanent custody record of this move.
  insert into lead_assignment (lead_id, broker_id, reason, reassigned_by, batch_id)
  values (p_lead_id, p_broker_id, v_reason, p_admin_id, p_batch_id);

  -- 3. The receiving broker's personal "New" — their own clock, does NOT
  -- touch lead.stage (which keeps its real, shared value throughout).
  insert into lead_stage_history (lead_id, broker_id, from_stage, to_stage)
  values (p_lead_id, p_broker_id, null, 'New');

  -- 4. Observability. detail carries the from/to and the reason.
  insert into audit_log (actor_broker_id, action, entity_type, entity_id, detail)
  values (
    p_admin_id, 'lead_reassigned', 'lead', p_lead_id::text,
    jsonb_build_object(
      'old_broker_id', v_current_broker_id,
      'new_broker_id', p_broker_id,
      'reason', v_reason,
      'batch_id', p_batch_id,
      'actor', 'admin'
    )
  );

  return p_broker_id;
end;
$$ language plpgsql;


-- ─── 2. assign_lead: record a reason on EVERY unassigned path ────────────
-- Identical to 0030 in every other respect — the only change is the audit
-- insert added to the gate-closed branch, and the two reasons being distinct.
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
