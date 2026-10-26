-- Open follow-ups move with a reassigned lead.
--
-- migrations/0061 already stated the rule, in the reminder table's own comment
-- on broker_id:
--
--   "and MOVED when a lead is reassigned — an open promise belongs to whoever
--    holds the lead now, not to whoever made it. (contact_log does the
--    opposite: a call Sarah made stays Sarah's forever, because it is a record
--    of the past. A reminder is a debt against the future.)"
--
-- admin_assign_lead never did it. It wrote lead, lead_assignment,
-- lead_stage_history and audit_log — and left reminder.broker_id alone. So
-- broker A kept seeing a follow-up for a customer they no longer had, and
-- broker B never learned one existed. Nothing errored; the promise just
-- stopped belonging to anybody who could act on it.
--
-- The design was written down and the implementation never caught up, which is
-- the quietest kind of gap: reading the schema tells you it works.
--
-- OPEN ONLY, deliberately. A Done or Dismissed reminder is history rather than
-- an outstanding promise — broker A really did make that call, and rewriting
-- the row to broker B would falsify the record.
--
-- BUILT FROM 0073, the current definition of this function — not from 0025 or
-- 0031. Copying the original body is exactly the mistake that broke Sold in
-- the first draft of 0075, and that 0073 itself exists to undo. Diffed before
-- applying: the only difference from 0073 is the update statement below.

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
  -- of three things to fix.
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

  -- 4. OPEN follow-ups move with the lead.
  --
  -- migrations/0061 states this rule in the reminder table's own comment on
  -- broker_id — "an open promise belongs to whoever holds the lead now, not to
  -- whoever made it" — and it was never implemented. So a reassignment left
  -- broker A seeing a follow-up for a customer they no longer had, while
  -- broker B never learned one existed. The promise to ring someone back
  -- simply evaporated at the moment of handover.
  --
  -- OPEN ONLY. A Done or Dismissed reminder is history: broker A really did
  -- make that call, and rewriting it to B would falsify the record. The same
  -- distinction contact_log draws — a call Sarah made stays Sarah's forever,
  -- because it is a record of the past, while a reminder is a debt against
  -- the future.
  --
  -- Inside this function, so it shares the transaction with the reassignment
  -- itself: the lead and its outstanding work move together or not at all.
  update reminder
  set broker_id = p_broker_id
  where lead_id = p_lead_id
    and status = 'Open';

  -- 5. Observability. detail carries the from/to and the reason.
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

  -- The lead id, as 0025 and 0031 had it and 0073 restored. (0071 briefly
  -- returned the broker id by accident; that comment lived here until this
  -- migration, where it would have read as though THIS change were about the
  -- return value.)
  return p_lead_id;
end;
$$ language plpgsql;
