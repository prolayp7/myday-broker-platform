-- A broker can stop their own leads, and start them again, both immediately.
--
-- Until now a broker had no way to opt OUT. They could opt IN to overtime
-- (ready_for_more); nothing turned the tap off. A broker who is ill or away
-- kept receiving leads that sat unworked — the exact outcome the round-robin
-- exists to prevent, because a delivered lead counts against the day's cap
-- whether or not anybody rings it.
--
-- The only existing route was admin-only and slow in both directions: pausing
-- meant an admin writing blocked_reason/blocked_at/blocked_by (a
-- disciplinary-shaped record for someone with a cold), and unpausing was NEXT
-- DAY BY DESIGN — is_blocked_by_admin stays true until the daily reset
-- promotes scheduled_unpause_at.
--
-- A SEPARATE COLUMN, not a reuse of is_blocked_by_admin. Two reasons, both
-- load-bearing:
--
--   * a broker able to clear is_blocked_by_admin could UNDO AN ADMIN'S BLOCK,
--     which is precisely the capability an admin block exists to have;
--   * "off sick" and "admin paused" must stay tellable apart when somebody
--     asks why a broker received nothing.
--
-- availability_status is deliberately untouched. It has existed since 0003,
-- nothing has ever written to it, and the round-robin design says outright
-- that it never gates assignment. Wiring it now would give one fact two homes.
--
-- THIS MIGRATION ALSO FIXES A BUG THAT PREDATES IT. See section 3.

-- ============================================================================
-- 1. The column
-- ============================================================================

alter table broker
  add column if not exists self_paused boolean not null default false;

comment on column broker.self_paused is
  'The broker has stopped their own leads. Set and cleared by the broker via '
  'PATCH /my-availability; cleared for everyone by the nightly reset. Distinct '
  'from is_blocked_by_admin, which only an admin may clear.';

-- ============================================================================
-- 2. assign_lead — the eligibility predicate, in FOUR places
--
-- Recreated from 0074's body, NOT from 0019's. Five migrations have redefined
-- this function; rebuilding from an older one would silently drop the
-- admin-exclusion guard that 0074 added. The ONLY change from 0074 is the
-- `and b.self_paused = false` line at each of the four eligibility sites.
--
-- FOUR SITES, and that is the whole risk here — the same shape of risk 0074
-- documented. The predicate appears in Group 1, in BOTH halves of the gate
-- query, and in Group 2. A half-applied rule fails silently in opposite
-- directions:
--
--   * gate excludes paused brokers, a tier does not -> a paused broker still
--     receives overtime, which is the single most likely way this feature
--     looks broken to the person using it;
--   * a tier excludes them, the gate does not -> the gate waits for a paused
--     broker to reach a cap they cannot approach, and overtime never opens
--     for anyone.
-- ============================================================================

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
    -- A BROKER WHO STOPPED THEIR OWN LEADS (0082) — site 1 of 4.
    and b.self_paused = false
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
  --
  -- A PAUSED BROKER MUST NOT HOLD THIS GATE SHUT. Excluded from both halves,
  -- so a group where everyone else is at cap can still open overtime rather
  -- than waiting on somebody who is not working today.
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
      -- A BROKER WHO STOPPED THEIR OWN LEADS (0082) — site 2 of 4.
      and b.self_paused = false
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
      -- A BROKER WHO STOPPED THEIR OWN LEADS (0082) — site 3 of 4.
      and b.self_paused = false
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
    -- A BROKER WHO STOPPED THEIR OWN LEADS (0082) — site 4 of 4, and the one
    -- easiest to miss. ready_for_more survives a pause (it is a separate
    -- daily opt-in), so without this line a paused broker who had opted into
    -- overtime this morning would keep receiving overtime leads all day.
    and b.self_paused = false
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

-- ============================================================================
-- 3. THE LOCK THE DRAIN NEVER TOOK
--
-- This is a pre-existing bug, not one this feature introduces — it is fixed
-- here because self-pause makes concurrent drains ordinary rather than rare.
--
-- assign_lead has taken the group's advisory lock since 0019. The wrapper that
-- calls it in a loop never did. So two drains running at once for the same
-- group both read the same backlog of unassigned leads: one assigns them, the
-- other collides with the unique index on lead_delivery(lead_id), aborts, and
-- LEAVES LEADS IT HAD NOT YET REACHED STILL WAITING.
--
-- set_ready_for_more has had this since it was written. Resume-from-pause
-- calls the same drain, so two brokers coming back at the same moment — or one
-- resuming while the nightly reset drains — would hit it far more often.
--
-- Recreated from 0019's body. The lock is the only change.
-- ============================================================================

create or replace function assign_pending_leads_for_group(p_group_id uuid)
returns void as $$
declare
  v_lead_id uuid;
begin
  -- THE LOCK, BEFORE THE LOOP — before the SELECT that reads the backlog, not
  -- merely before the assignment. A lock taken after the read would still let
  -- both callers act on the same stale list.
  --
  -- RE-ENTRANT, so nesting is safe: assign_lead takes this same lock inside
  -- this same transaction, and pg_advisory_xact_lock can be taken repeatedly
  -- by the holder. It cannot deadlock against itself, and every lock is
  -- released at transaction end whatever happens.
  perform pg_advisory_xact_lock(hashtext('broker_group_rotation'), hashtext(p_group_id::text));

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

-- ============================================================================
-- 4. Setting the flag, under the same lock
--
-- A plain `update broker set self_paused = true` does not queue behind an
-- assignment that is already in flight, so a lead can land AFTER the screen
-- has said leads are stopped:
--
--   1. a webhook takes the group lock and reads self_paused = false
--   2. the broker pauses; the request returns success
--   3. the webhook finishes and assigns them the lead
--
-- Taking the same lock the rotation already uses closes it. No new mechanism.
-- ============================================================================

create or replace function set_broker_self_paused(p_broker_id uuid, p_paused boolean)
returns uuid as $$
declare
  v_group_id uuid;
begin
  -- FOR UPDATE, and the group is RETURNED rather than read again from Python.
  -- A second round trip to fetch the group would open its own partial-failure
  -- window: the pause succeeds, the follow-up read fails, and the screen rolls
  -- back a change the database has already kept. It also pins the row while an
  -- admin might be moving this broker into a different group.
  select group_id into v_group_id from broker where id = p_broker_id for update;

  if v_group_id is null then
    -- No such broker, or a broker in no group. Null tells the caller to answer
    -- 404 rather than pretend something was changed.
    return null;
  end if;

  perform pg_advisory_xact_lock(hashtext('broker_group_rotation'), hashtext(v_group_id::text));

  update broker set self_paused = p_paused where id = p_broker_id;

  return v_group_id;
end;
$$ language plpgsql;

-- ============================================================================
-- 5. Cleared overnight, like ready_for_more
--
-- Self-pause is a decision about TODAY. A broker off sick on Monday should not
-- discover on Thursday that they have been receiving nothing all week because
-- of a switch they forgot — the failure is silent and costs them money.
--
-- Recreated from 0020's body. 0020 itself is NOT edited: historical migrations
-- are history. The only changes are step 3a below and its mention in step 4's
-- comment.
--
-- ORDER MATTERS: the clear happens in step 3, BEFORE the step 4 drain, so a
-- returning broker is eligible for the backlog being handed out this morning
-- rather than having to wait for the next lead to arrive.
-- ============================================================================

create or replace function daily_lead_allocation_reset()
returns void as $$
declare
  v_today_ontario date;
  v_last_reset_date date;
  v_group_id uuid;
begin
  -- ===================================================================
  -- IDEMPOTENCY GUARD — a double-fire of pg_cron (or a manual re-run) on
  -- the same Ontario calendar day is a no-op. Compare DATES in Ontario time,
  -- not raw timestamps, so this is correct across the DST transition too.
  -- ===================================================================
  v_today_ontario := (now() at time zone 'America/Toronto')::date;

  select (last_reset_at at time zone 'America/Toronto')::date
  into v_last_reset_date
  from lead_allocation_state;

  if v_last_reset_date >= v_today_ontario then
    return;
  end if;

  -- ===================================================================
  -- STEP 1 — promote scheduled changes whose effective date has arrived.
  -- Must run BEFORE step 4 (drain), so the drain sees each broker's real,
  -- current values rather than stale pending ones (§7).
  -- ===================================================================

  -- 1a. Deferred cap changes (§6): daily_cap is system-managed — this
  -- function is its only writer. Promote pending_daily_cap into daily_cap,
  -- clear the pending fields, and log it — all in one pass via a CTE, so the
  -- audit log captures exactly the rows this statement changed (a separate
  -- update-then-log-by-timestamp couldn't reliably tell which rows were just
  -- touched if two brokers' updates land in the same instant).
  with promoted_caps as (
    update broker
    set daily_cap = pending_daily_cap,
        pending_daily_cap = null,
        pending_daily_cap_effective_at = null
    where pending_daily_cap is not null
      and pending_daily_cap_effective_at <= now()
    returning id, daily_cap
  )
  insert into audit_log (actor_broker_id, action, entity_type, entity_id, detail)
  select null, 'daily_cap_promoted', 'broker', id::text,
         jsonb_build_object('new_daily_cap', daily_cap, 'actor', 'system')
  from promoted_caps;

  -- 1a-2. Deferred max_cap changes — identical pattern to pending_daily_cap
  -- immediately above, for the Group-2 ceiling.
  with promoted_max_caps as (
    update broker
    set max_cap = pending_max_cap,
        pending_max_cap = null,
        pending_max_cap_effective_at = null
    where pending_max_cap is not null
      and pending_max_cap_effective_at <= now()
    returning id, max_cap
  )
  insert into audit_log (actor_broker_id, action, entity_type, entity_id, detail)
  select null, 'max_cap_promoted', 'broker', id::text,
         jsonb_build_object('new_max_cap', max_cap, 'actor', 'system')
  from promoted_max_caps;

  -- 1b. Deferred unpause (§5): flip is_blocked_by_admin back to false, clear
  -- the schedule, log it. (Pausing is instant and handled elsewhere, not here
  -- — only the deferred UNPAUSE needs promotion.)
  with promoted_unpauses as (
    update broker
    set is_blocked_by_admin = false,
        scheduled_unpause_at = null
    where scheduled_unpause_at is not null
      and scheduled_unpause_at <= now()
    returning id
  )
  insert into audit_log (actor_broker_id, action, entity_type, entity_id, detail)
  select null, 'broker_unpaused', 'broker', id::text,
         jsonb_build_object('actor', 'system')
  from promoted_unpauses;

  -- 1c. New/deferred broker activation (§5): clear eligible_from once it has
  -- arrived — its only job was to hold the broker out of eligibility until
  -- today; from here on `eligible_from is null` and `eligible_from <= now()`
  -- behave identically, so clearing it is a cleanup, not a behavior change.
  with promoted_eligibility as (
    update broker
    set eligible_from = null
    where eligible_from is not null
      and eligible_from <= now()
    returning id
  )
  insert into audit_log (actor_broker_id, action, entity_type, entity_id, detail)
  select null, 'broker_activated', 'broker', id::text,
         jsonb_build_object('actor', 'system')
  from promoted_eligibility;

  -- ===================================================================
  -- STEP 2 — advance the day. The single allocation clock every cap-count
  -- query anchors to.
  -- ===================================================================
  update lead_allocation_state set last_reset_at = now();

  -- ===================================================================
  -- STEP 3 — clear the Group-2 daily opt-in. ready_for_more is a per-day
  -- choice (§8); every broker starts each day opted OUT of overtime and
  -- must choose in again if they want it.
  -- ===================================================================
  update broker set ready_for_more = false where ready_for_more = true;

  -- ===================================================================
  -- STEP 3a — clear self-pause (0082). Same per-day reasoning as
  -- ready_for_more directly above, and for a sharper reason: a broker who
  -- forgot to switch themselves back on would silently receive nothing for
  -- days, and lost leads are lost commission. Everyone starts the day ON.
  --
  -- Logged per broker, unlike ready_for_more — "why did I get no leads
  -- yesterday" is a question somebody will actually ask, and this is the row
  -- that answers it.
  -- ===================================================================
  with resumed as (
    update broker
    set self_paused = false
    where self_paused = true
    returning id
  )
  insert into audit_log (actor_broker_id, action, entity_type, entity_id, detail)
  select null, 'broker_self_pause_cleared', 'broker', id::text,
         jsonb_build_object('actor', 'system')
  from resumed;

  -- ===================================================================
  -- STEP 4 — drain each real (non-recycle-bin) group's leftover unassigned
  -- leads now that step 1-3 have brought every broker's state up to date —
  -- including the self-pause clear in 3a, so a broker who paused yesterday
  -- is eligible for this morning's backlog.
  -- ===================================================================
  for v_group_id in select id from broker_group where is_recycle_bin = false
  loop
    perform assign_pending_leads_for_group(v_group_id);
  end loop;
end;
$$ language plpgsql;
