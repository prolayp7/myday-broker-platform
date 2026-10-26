-- admin_assign_lead — now also writes the receiving broker's personal "New".
--
-- Replaces the function from 0025 (create or replace — same signature, same
-- behavior) with ONE addition: writes an ARRIVAL row into lead_stage_history
-- (0029) — from_stage = null, to_stage = 'New', broker_id = p_broker_id.
-- Applies to BOTH cases this function handles (Admin Claim: pooled -> broker,
-- and Admin Reassign: held -> different broker) — either way the lead just
-- landed in a broker's inbox, which is exactly what this table records.
--
-- CRITICAL (see 0029's header for the full reasoning): this does NOT touch
-- lead.stage. The broker's "New" here is a PERSONAL clock-start, independent
-- of the lead's real, shared pipeline position — an admin reassigning a lead
-- that's already at 'Under Review' does not reset it to 'New'; lead.stage
-- stays exactly 'Under Review'. Only this parallel per-broker table says
-- 'New' for the newly-receiving broker.
--
-- Same transaction as every other write in this function — cannot be
-- orphaned from the assignment it belongs to.
--
-- Everything else below is IDENTICAL to 0025 — only the lead_stage_history
-- insert (marked NEW) is added.

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
  v_reason text;
begin
  select true, assigned_broker_id
  into v_lead_exists, v_current_broker_id
  from lead
  where id = p_lead_id;

  if not found then
    raise exception 'Lead not found';
  end if;

  select is_archived into v_target_archived from broker where id = p_broker_id;
  if not found then
    raise exception 'Target broker not found';
  end if;
  if v_target_archived then
    raise exception 'That broker is archived and can''t receive leads';
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

  -- NEW: 3. The receiving broker's personal "New" — their own clock, does
  -- NOT touch lead.stage (which keeps its real, shared value throughout).
  insert into lead_stage_history (lead_id, broker_id, from_stage, to_stage)
  values (p_lead_id, p_broker_id, null, 'New');

  -- 4. Observability (unchanged shape from the old Python path, so existing
  -- audit tooling keeps working). detail carries the from/to and the reason.
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

  return p_lead_id;
end;
$$ language plpgsql;
