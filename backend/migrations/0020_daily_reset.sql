-- Round-robin lead allocation — DAILY RESET JOB.
--
-- Full design: backend/docs/round-robin-allocation-design.md (§5, §7). Builds
-- on 0018 (schema) and 0019 (assign_lead / assign_pending_leads_for_group).
--
-- Runs once per Ontario calendar day, at the FIRST hourly cron tick after
-- Ontario midnight (~00:00–01:00 local) — the idempotency guard below compares
-- Ontario DATES, so the first tick of a new local date does the work and every
-- later tick that day is a no-op. Midnight-anchoring is deliberate: it matches
-- eligible_from (set to Ontario midnight at onboarding), and exact timing was
-- confirmed not to matter — brokers start working at 9 AM. Four steps, IN
-- ORDER — promotion must happen before the drain, so today's drain uses each
-- broker's up-to-date values, not stale pending ones.
--
-- Ontario does not observe a fixed UTC offset (EST/EDT, DST twice a year).
-- Rather than relying on server-level pg_cron timezone config (cron.timezone
-- requires `alter system` + a reload, which a managed Supabase instance does
-- not grant — this would silently fail to apply), the cron job itself fires
-- FREQUENTLY (hourly) in plain UTC, cheap, and the function's own idempotency
-- guard below — computed with 'America/Toronto' in ordinary SQL, no server
-- config needed — decides whether it's actually the first run of the new
-- Ontario day. This makes the correct-local-time behavior a property of the
-- function's logic, not of cron scheduling config, so it needs no privileged
-- setup and is immune to DST drift.

create extension if not exists pg_cron;

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
  -- STEP 4 — drain each real (non-recycle-bin) group's leftover unassigned
  -- leads now that step 1-3 have brought every broker's state up to date.
  -- ===================================================================
  for v_group_id in select id from broker_group where is_recycle_bin = false
  loop
    perform assign_pending_leads_for_group(v_group_id);
  end loop;
end;
$$ language plpgsql;

-- Registers the job: fires every hour, in plain UTC (pg_cron's default,
-- requiring no timezone config or server privileges). The function's own
-- idempotency guard (top of daily_lead_allocation_reset) is what actually
-- decides whether this is the first run of the new Ontario day — most of
-- these 24 firings per day are a no-op single date comparison. This trades a
-- small amount of wasted work for a scheduling mechanism that needs zero
-- special permissions and can't drift on DST.
select cron.schedule(
  'daily-lead-allocation-reset',
  '0 * * * *',
  $$select daily_lead_allocation_reset()$$
);
