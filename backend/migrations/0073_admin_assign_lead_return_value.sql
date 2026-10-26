-- Restore admin_assign_lead's return value to the lead id.
--
-- A ONE-LINE UNINTENDED CONTRACT CHANGE. 0025 and 0031 both ended with
-- `return p_lead_id`. Migration 0071 — which was about eligibility guards and
-- nothing else — ended with `return p_broker_id` instead. That was not a
-- decision; it was a slip while rewriting the function body around the new
-- guards, and it went unnoticed until an external review diffed the two.
--
-- NOTHING BROKE, which is exactly why it is worth fixing now. The only caller
-- (repositories/lead.py::admin_assign, via services/lead.py::reassign_lead)
-- discards the result and re-reads the lead afterwards, so the change was
-- invisible. It would have surfaced later, in whatever first tried to use the
-- returned id — most likely a batch reassignment, where getting a broker id
-- back while expecting a lead id produces a wrong result rather than an error.
--
-- The lead id is also the right answer on its own terms: the function is named
-- for what it acts ON, the caller already knows which broker it passed in, and
-- the useful thing to hand back is the row that changed.
--
-- Identical to 0071 in every other respect — only the final RETURN differs.

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

  -- THE LINE THIS MIGRATION EXISTS FOR. Back to the lead id, as 0025 and 0031
  -- had it.
  return p_lead_id;
end;
$$ language plpgsql;
