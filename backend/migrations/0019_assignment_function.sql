-- Round-robin lead allocation — ASSIGNMENT FUNCTION.
--
-- Full design: backend/docs/round-robin-allocation-design.md (§1-4). Builds on
-- the schema from 0018. This is the one function that decides "who gets this
-- lead" — called both when a lead first arrives (lead_ingestion.py, one lead)
-- and by the daily reset job's drain step (0020, looping leftover leads). Same
-- function, two callers, per the "pull not push" design.
--
-- CONCURRENCY (§4): the group's advisory lock is taken FIRST, before anything
-- that reads a count. Two webhooks for the same group can run this function at
-- the same time — the second one simply waits for the lock, then sees the
-- TRUE post-assignment counts, never a stale snapshot. This is what makes the
-- cap and the gate race-free without any extra bookkeeping.

-- Assigns ONE lead. Returns the broker_id it was assigned to, or null if no
-- eligible broker exists right now (lead is left unassigned — assigned_broker_id
-- stays null, and it will be picked up again by the next trigger or the next
-- reset drain). Never raises for "no eligible broker" — that is an expected,
-- ordinary outcome, not an error.
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
    -- Lead doesn't exist, or (defensively) has no group — nothing to do.
    return null;
  end if;

  -- ===================================================================
  -- STEP 1 — take the group's lock FIRST. Everything below that reads a
  -- count (eligibility, cap, gate) must run AFTER this, inside the same
  -- transaction, so a concurrent call for the same group queues here
  -- instead of racing on stale numbers (§4).
  -- ===================================================================
  perform pg_advisory_xact_lock(hashtext('broker_group_rotation'), hashtext(v_group_id::text));

  -- Ensure a rotation row exists for this group (lazy-created on first use).
  insert into broker_group_rotation (group_id)
  values (v_group_id)
  on conflict (group_id) do nothing;

  select last_assigned_broker_id into v_last_assigned_broker_id
  from broker_group_rotation
  where group_id = v_group_id;

  select last_reset_at into v_last_reset_at from lead_allocation_state;

  -- ===================================================================
  -- STEP 2 — try Group 1: eligible brokers under their own daily_cap,
  -- starting from the broker AFTER the last one who received a lead (the
  -- round-robin order). "Eligible" per §2: active, not admin-blocked, not
  -- in the recycle bin, and started (eligible_from has arrived or is null).
  -- ===================================================================
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
    -- Rotation order: brokers "after" the last-assigned one come first
    -- (by id, a stable arbitrary tiebreaker within the group), then wrap
    -- around to the ones "before" it. A null last_assigned_broker_id (first
    -- ever assignment in this group) means everyone is "after" — natural id
    -- order.
    (b.id <= coalesce(v_last_assigned_broker_id, '00000000-0000-0000-0000-000000000000'::uuid)),
    b.id
  limit 1;

  if v_broker_id is not null then
    insert into lead_delivery (lead_id, broker_id) values (p_lead_id, v_broker_id);

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

  -- ===================================================================
  -- STEP 3 — Group 1 had nobody eligible/under-cap for this lead. Check
  -- the GATE: Group 2 opens only when every currently-eligible Group-1
  -- broker is AT their cap. If even one eligible broker is under cap, the
  -- gate stays closed and this lead simply waits (it does NOT fall through
  -- to Group 2 just because it happened to arrive when nobody had room this
  -- instant — that would break the "guaranteed delivery" promise of Group 1).
  --
  -- ZERO eligible Group-1 brokers (whole group paused/inactive) is treated
  -- as GATE CLOSED, not open — deliberately. Falling through to Group 2 here
  -- would silently paper over a broken group (no guaranteed coverage at all)
  -- by quietly routing to overtime volunteers, hiding a real problem. The
  -- real fix for this state is a guard at the routing/group level ("a group
  -- must have at least one active, unpaused broker to receive leads at all")
  -- — parked as a future enhancement (§10). Until that guard exists, this
  -- lead is deliberately left unassigned (surfaced via the unassigned-reason
  -- audit log below) rather than masked by an overtime assignment.
  -- ===================================================================
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
    -- Either at least one Group-1 broker still has room (this particular
    -- lead just couldn't find one above), OR the group has zero eligible
    -- Group-1 brokers at all — both cases leave the lead unassigned rather
    -- than opening Group 2.
    return null;
  end if;

  -- ===================================================================
  -- STEP 4 — Group 2: ready_for_more brokers, under their own max_cap,
  -- same rotation-order logic as Group 1 (continuing the same pointer keeps
  -- one shared rotation per group rather than a second independent one).
  -- ===================================================================
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

  -- ===================================================================
  -- STEP 5 — nobody eligible in either tier. Record WHY, so this isn't a
  -- silent gap (the "why is a lead unassigned" gap closed earlier in the
  -- design). Lead stays unassigned; a later trigger or the reset drain will
  -- retry it.
  -- ===================================================================
  insert into audit_log (actor_broker_id, action, entity_type, entity_id, detail)
  values (null, 'lead_unassigned', 'lead', p_lead_id::text,
          jsonb_build_object('group_id', v_group_id, 'reason', 'no_eligible_broker_in_either_tier'));

  return null;
end;
$$ language plpgsql;

-- Convenience wrapper for the reset job's drain step (0020): assign every
-- currently-unassigned lead in a group, oldest request first. Each lead still
-- goes through assign_lead individually, so the lock/count logic above applies
-- per-lead exactly as it does for a single live webhook — draining a backlog is
-- not a different code path, just a loop over the same function.
create or replace function assign_pending_leads_for_group(p_group_id uuid)
returns void as $$
declare
  v_lead_id uuid;
begin
  for v_lead_id in
    select id from lead
    where group_id = p_group_id
      and assigned_broker_id is null
    order by coalesce(requested_at, created_at) asc
  loop
    perform assign_lead(v_lead_id);
  end loop;
end;
$$ language plpgsql;
