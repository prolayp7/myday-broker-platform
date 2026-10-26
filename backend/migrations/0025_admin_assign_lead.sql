-- admin_assign_lead — the ADMIN assignment path, atomic and history-writing.
--
-- Replaces the old two-call Python reassign (repositories/lead.py::reassign +
-- a separate audit_log insert), which had two production gaps:
--   1. The lead update and the audit write were separate Supabase REST calls —
--      a crash between them left the lead moved with no history record.
--   2. It never distinguished handing out a POOLED lead from MOVING a held one,
--      and never blocked the same-broker no-op.
-- Doing it as ONE plpgsql function called via a single RPC closes both: every
-- write below commits together or not at all, and the reason is resolved from
-- the lead's real state at the instant of the move.
--
-- This is the ADMIN writer. assign_lead (0024) is the SYSTEM writer. They are
-- kept separate on purpose: assign_lead must only ever write 'Initial Claim'
-- (it's also the daily-drain function), so the admin reasons live only here.
--
-- What it deliberately does NOT do:
--   * It does NOT touch lead_delivery. Reassignment must stay invisible to the
--     daily cap (design §3) — the original recipient's delivery slot stays
--     spent, the new broker's cap is untouched. Only lead_assignment (history)
--     and lead (current holder) move.
--   * It does NOT enforce same-group / same-community. That flexibility is
--     intended (design decision 2026-07-14): an admin CAN move a lead across
--     groups/communities; the frontend warns first (a conscious override), but
--     the backend stays permissive. It still RECORDS the move fully, so a
--     cross-community reassignment is visible in the journey.
--
-- p_batch_id: null for a single admin action; set (shared) for a bulk move so
-- the whole batch is one auditable event. See lead_assignment.batch_id (0023).
--
-- Returns the updated lead row's id. On an invalid request it raises a plain
-- error with a human-readable message; the service layer (services/lead.py)
-- catches any error from this function and returns it as a 400. The guarded
-- cases: lead doesn't exist, target broker doesn't exist, target archived, or
-- the lead is already held by that broker (a pointless no-op).

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
  -- Lead must exist. Capture its CURRENT holder in the same read — this is what
  -- decides Admin Claim (was null / pooled) vs Admin Reassign (was held), and
  -- what the no-op guard compares against.
  select true, assigned_broker_id
  into v_lead_exists, v_current_broker_id
  from lead
  where id = p_lead_id;

  if not found then
    raise exception 'Lead not found';
  end if;

  -- Target broker must exist and must not be archived. An archived broker is a
  -- permanently-sealed ex-employee — handing them a live customer would bypass
  -- the exact outcome the eligibility guards exist to prevent. (A merely
  -- BLOCKED broker IS allowed: blocking gates SYSTEM delivery; an admin
  -- explicitly choosing them is a deliberate override of that.)
  select is_archived into v_target_archived from broker where id = p_broker_id;
  if not found then
    raise exception 'Target broker not found';
  end if;
  if v_target_archived then
    raise exception 'That broker is archived and can''t receive leads';
  end if;

  -- No-op guard: reassigning a lead to the broker who already holds it would
  -- forge a phantom journey hop (and bump assigned_at for nothing). Only ever a
  -- fat-finger — block it. (Pooled leads have v_current_broker_id = null, so
  -- this never trips for an Admin Claim.)
  if v_current_broker_id is not null and v_current_broker_id = p_broker_id then
    raise exception 'That lead is already assigned to that broker';
  end if;

  -- Reason resolved from the lead's real state right now:
  --   pooled (no current holder) -> Admin Claim  (manual pool-drain)
  --   held   (has a holder)      -> Admin Reassign (moved off someone)
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

  -- 3. Observability (unchanged shape from the old Python path, so existing
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
