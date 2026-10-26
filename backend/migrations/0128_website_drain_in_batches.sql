-- The website drain takes at most p_limit submissions per call.
--
-- WHY. 0127's drain walked the WHOLE waiting queue in one call, and one call is
-- one transaction: a temporary error rolls all of it back, by design, so every
-- submission stays 'waiting'. That design has one way to wedge. A statement
-- timeout is itself a temporary error — so a backlog large enough to outlast
-- the timeout (a weekend with nobody switched on, then everyone switched on
-- at once) would time out, roll back, and time out again on exactly the same
-- work at every run. The queue would never move, and nothing would look wrong
-- except that it never moved.
--
-- Found in review before anything went live; no environment has had a website
-- submission yet.
--
-- THE FIX is a batch. services/website_lead.py calls again while a batch comes
-- back full, so the queue still drains completely — just in bounded pieces,
-- each far inside any timeout. Oldest-first and stop-at-the-first-blocked are
-- unchanged: a batch is simply the oldest p_limit rows.
--
-- DROP, NOT create-or-replace. Adding a parameter makes a NEW function beside
-- the old one; PostgREST then cannot choose between assign_waiting_website_leads()
-- and assign_waiting_website_leads(p_limit default 100) for a call with no
-- arguments, and refuses both. The old one goes in the same transaction.
--
-- Everything else in the body is 0127's, unchanged.
--
-- ORDER: apply this BEFORE deploying the backend that calls the drain — that
-- code sends p_limit, which 0127's no-argument version would refuse.
--
-- APPLY BY HAND in the Supabase SQL editor. Staging, then production.

begin;

drop function if exists assign_waiting_website_leads();

create or replace function assign_waiting_website_leads(p_limit int default 100)
returns jsonb as $$
declare
  v_event record;
  v_broker_id uuid;
  v_group_id uuid;
  v_last_broker_id uuid;
  v_last_reset_at timestamptz;
  v_lead_id uuid;
  v_payload jsonb;
  v_assigned int := 0;
  v_failed int := 0;
begin
  perform pg_advisory_xact_lock(hashtext('website_rotation'));

  select last_reset_at into v_last_reset_at from lead_allocation_state;

  for v_event in
    select id, raw_payload
    from webhook_event
    where source = 'website' and status = 'waiting'
    order by created_at, id
    limit greatest(p_limit, 1)
  loop
    select last_broker_id into v_last_broker_id
    from website_rotation_state where id = 1;

    -- WHO IS AVAILABLE. The same availability rules as assign_lead (0082) —
    -- active, not blocked by an admin, a broker not an admin, not self-paused,
    -- started, not in the Recycle Bin — plus this system's own two: switched
    -- on, and under the WEBSITE limit. A rule added to one copy must be added
    -- to the other.
    select b.id, b.group_id into v_broker_id, v_group_id
    from broker b
    join broker_group bg on bg.id = b.group_id
    join website_broker_setting ws on ws.broker_id = b.id
    where ws.is_on = true
      and bg.is_recycle_bin = false
      and b.is_active = true
      and b.is_blocked_by_admin = false
      and b.role = 'broker'
      and b.self_paused = false
      and (b.eligible_from is null or b.eligible_from <= now())
      and (
        select count(*) from website_lead_delivery d
        where d.broker_id = b.id and d.delivered_at >= v_last_reset_at
      ) < ws.daily_cap
    order by
      (b.id <= coalesce(v_last_broker_id, '00000000-0000-0000-0000-000000000000'::uuid)),
      b.id
    limit 1;

    if not found then
      exit;
    end if;

    v_payload := v_event.raw_payload;

    begin
      -- assigned_broker_id IN THE INSERT ITSELF. That is what fires the
      -- WhatsApp new-lead alert (trg_lead_whatsapp_assign, 0109/0111), which
      -- watches inserts and updates of that column. source is set in the same
      -- statement, so the alert's manual-lead check already sees 'website'.
      insert into lead (
        group_id, source, name, phone, email, extra_data,
        requested_at, assigned_broker_id, assigned_at, assigned_by
      ) values (
        v_group_id, 'website',
        v_payload->>'name', v_payload->>'phone', v_payload->>'email',
        website_lead_answers(v_payload),
        (v_payload->>'submitted_at')::timestamptz,
        v_broker_id, now(), 'system'
      )
      returning id into v_lead_id;

      -- The same trail assign_lead leaves, so the lead's history, custody and
      -- audit read identically whichever route brought it in.
      insert into website_lead_delivery (lead_id, broker_id)
      values (v_lead_id, v_broker_id);

      insert into lead_assignment (lead_id, broker_id, reason, reassigned_by)
      values (v_lead_id, v_broker_id, 'Initial Claim', null);

      insert into lead_stage_history (lead_id, broker_id, from_stage, to_stage)
      values (v_lead_id, v_broker_id, null, 'New');

      insert into audit_log (actor_broker_id, action, entity_type, entity_id, detail)
      values (null, 'lead_assigned', 'lead', v_lead_id::text,
              jsonb_build_object('broker_id', v_broker_id, 'tier', 'website',
                                 'actor', 'system', 'webhook_event_id', v_event.id));

      update website_rotation_state set last_broker_id = v_broker_id where id = 1;

      update webhook_event
      set status = 'processed', failure_reason = null, next_retry_at = null
      where id = v_event.id;

      v_assigned := v_assigned + 1;
    exception
      when data_exception or integrity_constraint_violation then
        -- Everything the block wrote is already undone, pointer included, so
        -- this broker keeps their turn for the next submission.
        update webhook_event
        set status = 'failed',
            failure_reason = sqlerrm,
            attempt_count = attempt_count + 1,
            next_retry_at = null
        where id = v_event.id;

        v_failed := v_failed + 1;
    end;
  end loop;

  return jsonb_build_object(
    'assigned', v_assigned,
    'failed', v_failed,
    'waiting', (select count(*) from webhook_event
                where source = 'website' and status = 'waiting')
  );
end;
$$ language plpgsql;

commit;


-- ── Check after applying ──────────────────────────────────────────────────
--
--   select pg_get_function_identity_arguments(oid) from pg_proc
--   where proname = 'assign_waiting_website_leads';
--       -> exactly one row: "p_limit integer"
--
--   select assign_waiting_website_leads();       -- default batch, still works
--   select assign_waiting_website_leads(1);
