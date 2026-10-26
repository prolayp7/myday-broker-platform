-- Self-pause PERSISTS. It is not a per-day choice.
--
-- 0082 cleared self_paused for every broker each night, reasoning that someone
-- who forgot to switch back on would silently lose days of leads. That
-- reasoning had the failure mode backwards. A broker sets this because they are
-- ON VACATION, ILL, or ALREADY OVERLOADED — states measured in days, not hours.
-- Clearing it overnight meant the broker who is away all week got leads on
-- Tuesday morning that nobody rang, and a delivered lead counts against the
-- day's cap whether or not anybody works it. The switch was unusable for the
-- exact situation it was built for: every morning it undid itself.
--
-- ready_for_more is UNCHANGED and still cleared nightly (STEP 3 below). That
-- one genuinely IS a per-day opt-in — "I'll take extra TODAY" — and carrying
-- it over would hand somebody overtime they never asked for on a day they never
-- chose. The two flags sit next to each other and look alike; they are not.
--
-- NO SCHEDULED RESUME. Off means off until a person turns it on. A "back on"
-- date would be another deferred field to promote, another thing to be wrong
-- about, and it re-introduces the same silent automatic resume this migration
-- exists to remove.
--
-- THE GUARD AGAINST FORGETTING IS NOW VISIBILITY, NOT A NIGHTLY UNDO:
-- self_paused_at below, surfaced as "Paused · 6d" on the group roster and as a
-- banner with a resume button on the broker's admin screen. An admin can see
-- who is off and for how long without opening anybody's profile, and can
-- switch them back on.
--
-- NOTHING ABOUT ALLOCATION CHANGES. assign_lead reads self_paused as a bare
-- boolean at its four eligibility sites (0082); a paused broker is skipped and
-- the rotation continues to the rest of the group, exactly as before. This
-- migration changes only WHAT CLEARS THE FLAG.

-- ============================================================================
-- 1. When the pause started
--
-- NOT broker.updated_at — the set_updated_at trigger (0003) fires on every
-- write to the row, so an unrelated cap edit or a password reset would silently
-- re-date the absence.
--
-- NOT derived from audit_log — that table is observational history (see
-- repositories/audit_log.py), never state, and nothing writes a row when a
-- broker pauses themselves in the first place.
--
-- NULL FOR ANYONE PAUSED RIGHT NOW, deliberately not backfilled to now(). A
-- backfill would assert "paused since today" about someone who may have been
-- paused for hours, on the one screen built to be believed. The UI renders null
-- as no duration, and the value corrects itself the next time the flag moves.
-- The population is at most one day's worth anyway: until this migration the
-- nightly clear ran every morning, so nobody can have been paused longer.
-- ============================================================================

alter table broker
  add column if not exists self_paused_at timestamptz;

comment on column broker.self_paused_at is
  'When self_paused was last switched ON. Null whenever self_paused is false. '
  'Set by set_broker_self_paused only; STICKY across a re-pause so an ongoing '
  'absence is never re-dated. Powers the admin "paused for N days" reading. '
  'Null is legitimate for a broker paused before 0105 added this column.';

-- Corrects 0082's comment, which said "cleared for everyone by the nightly
-- reset". This migration makes that false. The statement lives HERE rather than
-- being edited into 0082: rewriting an applied migration changes nothing in a
-- database that already ran it.
comment on column broker.self_paused is
  'The broker has stopped their own leads. PERSISTS until the broker (via '
  'PATCH /my-availability) or an admin (via PATCH /brokers/{id}/resume-leads) '
  'switches it back on — the nightly reset does NOT clear it (0105 removed '
  'that; 0082 had it). Distinct from is_blocked_by_admin, which only an admin '
  'may set or clear, and which records a reason.';

-- ============================================================================
-- 2. The single writer of self_paused, now stamping when the pause began
--
-- Rebuilt from 0082 section 4. The FOR UPDATE, the advisory lock and the
-- returned group_id are unchanged and all still load-bearing — see 0082 for
-- why a plain UPDATE races an assignment already in flight. The ONLY change is
-- self_paused_at.
--
-- ONE STATEMENT, not two. A separate `update ... set self_paused_at` could fail
-- on its own and leave the flag set with no date, which reads on screen as
-- "paused, since forever".
--
-- STICKY ACROSS A RE-PAUSE — the middle CASE branch, and the reason there is a
-- CASE here at all. Pausing while already paused (a double click, a retried
-- request, an admin and a broker acting at once) must NOT restart the clock:
-- otherwise a broker away for nine days reads as "paused today" the moment
-- anything touches the switch, and the one number the admin banner exists to
-- show is the one number that cannot be trusted. Its failure is silent — the
-- number just gets younger — so tests/test_lead_assignment.py exercises this
-- branch directly.
--
-- Inside the UPDATE, the bare `self_paused` in the CASE is the row's PRE-UPDATE
-- value, which is what the newly-paused branch needs.
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

  update broker
  set self_paused = p_paused,
      self_paused_at = case
        when p_paused and not self_paused then now()  -- newly paused: start the clock
        when p_paused then self_paused_at             -- already paused: do not re-date it
        else null                                     -- resumed: no pause in progress
      end
  where id = p_broker_id;

  return v_group_id;
end;
$$ language plpgsql;

-- ============================================================================
-- 3. The nightly reset — WITHOUT the self-pause clear
--
-- Rebuilt from 0082 section 5. The idempotency guard, STEP 1 (all four
-- promotions), STEP 2 and STEP 4's body are byte-identical to that version.
-- The only changes are the deletion of STEP 3a and the rewording of the two
-- comments that referred to it.
--
-- 3a is DELETED rather than left behind a flag or commented out: a disabled
-- clear is a clear waiting for somebody to re-enable it by accident.
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
  --
  -- self_paused IS NOT CLEARED HERE, and its absence is the whole point of
  -- 0105. The two flags look alike and are not: "I'll take extra work today"
  -- expires with the day; "I am away" does not. See 0105's header.
  -- ===================================================================
  update broker set ready_for_more = false where ready_for_more = true;

  -- ===================================================================
  -- STEP 4 — drain each real (non-recycle-bin) group's leftover unassigned
  -- leads now that steps 1-3 have brought every broker's state up to date:
  -- promoted caps, lifted admin blocks and newly-eligible brokers are all
  -- visible to the drain.
  --
  -- A SELF-PAUSED BROKER IS STILL PAUSED HERE and receives none of this
  -- backlog — correct, and the change 0105 made. They rejoin the rotation the
  -- moment they, or an admin, switch back on, and THAT path runs its own drain
  -- (services/broker.py), so the waiting leads reach them then rather than
  -- being pushed at them on a morning they are still away.
  -- ===================================================================
  for v_group_id in select id from broker_group where is_recycle_bin = false
  loop
    perform assign_pending_leads_for_group(v_group_id);
  end loop;
end;
$$ language plpgsql;
