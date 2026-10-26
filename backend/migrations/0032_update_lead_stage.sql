-- update_lead_stage — the BROKER-DRIVEN stage change, atomic and history-writing.
--
-- The one write path for PATCH /leads/{id}/stage. Free-form by design
-- decision (2026-07-16): no transition rules, no terminal-state lock — any
-- stage to any other stage is always allowed. The only guard is OWNERSHIP:
-- only the broker who currently holds the lead (lead.assigned_broker_id) may
-- change its stage. This is enforced HERE, not just in the route, so the
-- guarantee holds regardless of caller.
--
-- Two things happen atomically:
--   1. lead.stage is updated to the new value — this IS the real, single,
--      shared pipeline value the whole app reads (Admin Leads screen,
--      filters, round-robin eligibility is untouched by this, etc).
--   2. A PROGRESS row is appended to lead_stage_history (0029), tagged to the
--      calling broker — from_stage = the prior value, to_stage = the new
--      one. This is the broker's own personal timeline continuing forward
--      from their ARRIVAL row (written by assign_lead/admin_assign_lead,
--      0030/0031).
--
-- Returns the new stage on success. Raises a plain, human-readable error on:
--   * lead doesn't exist
--   * the calling broker does not currently hold this lead (ownership guard)
-- The service layer maps any raised error to a 400/403.

create or replace function update_lead_stage(
  p_lead_id uuid,
  p_broker_id uuid,
  p_new_stage text
)
returns text as $$
declare
  v_current_broker_id uuid;
  v_current_stage text;
begin
  select assigned_broker_id, stage
  into v_current_broker_id, v_current_stage
  from lead
  where id = p_lead_id;

  if not found then
    raise exception 'Lead not found';
  end if;

  if v_current_broker_id is null then
    raise exception 'This lead is unassigned — it has no current broker to change its stage';
  end if;

  if v_current_broker_id != p_broker_id then
    raise exception 'You do not currently hold this lead';
  end if;

  -- === Both writes below are one transaction. ===

  update lead set stage = p_new_stage where id = p_lead_id;

  insert into lead_stage_history (lead_id, broker_id, from_stage, to_stage)
  values (p_lead_id, p_broker_id, v_current_stage, p_new_stage);

  return p_new_stage;
end;
$$ language plpgsql;
