-- update_lead_stage — now writes the issued date in the SAME transaction.
--
-- WHAT WAS WRONG. The stage change was atomic (lead.stage + a
-- lead_stage_history row, 0032) and the issued date was written AFTERWARDS, by
-- a separate PostgREST call from services/lead.py::_stamp_issued_at. That
-- second write was explicitly "best effort" — it caught its own exception and
-- logged it — so a database blip between the two left:
--
--     lead.stage           = 'Issued'
--     premium_history.issued_at = null
--
-- The screen says the policy was issued. Every issued-premium figure counts by
-- issued_at (repositories/premium_history.py::issued_since_for_broker), so the
-- policy is simply absent from the month's business, and nothing anywhere
-- reports a problem.
--
-- The inverse is worse. Moving OUT of Issued clears the date; if that clear
-- failed, a policy that was not written stays counted as business that was.
--
-- The old code's own justification was that a missing date "is fixed by
-- re-entering the stage" — but the stage dropdown does not offer the stage the
-- lead is already on, so re-entering Issued was not possible. The recovery
-- path it named did not exist.
--
-- HOW IT IS FIXED. p_issued_at moves into this function, so one transaction
-- either does all of it or none of it. There is no window between the two
-- writes any more, because there are no longer two writes.
--
-- REBUILT FROM THE CURRENT DEFINITION (0032, the only migration that defines
-- this function — 0057 mentions it in comments only). Everything below the
-- signature is 0032's body unchanged except the new block at the end.

create or replace function update_lead_stage(
  p_lead_id uuid,
  p_broker_id uuid,
  p_new_stage text,
  -- The date the carrier wrote the policy, from the broker. Defaulted so the
  -- three-argument call still resolves: PostgREST sends only the keys given,
  -- and an older caller (or a stage change that is not Issued) omits it.
  p_issued_at date default null
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

  -- === Every write below is one transaction. ===

  update lead set stage = p_new_stage where id = p_lead_id;

  insert into lead_stage_history (lead_id, broker_id, from_stage, to_stage)
  values (p_lead_id, p_broker_id, v_current_stage, p_new_stage);

  -- THE ISSUED DATE, in the same transaction as the stage that implies it.
  --
  -- Only two cases touch it, and both are unconditional updates rather than
  -- reads-then-writes — a lead with no premium row updates zero rows, which is
  -- correct and not an error. The premium guard in services/lead.py refuses
  -- Issued without a premium, so the row exists on the path that matters.
  if p_new_stage = 'Issued' then
    update premium_history
    set issued_at = p_issued_at
    where lead_id = p_lead_id;

  elsif v_current_stage = 'Issued' then
    -- Leaving Issued: the policy was not written after all. A date left behind
    -- would keep reporting a sale that did not happen.
    update premium_history
    set issued_at = null
    where lead_id = p_lead_id;
  end if;

  return p_new_stage;
end;
$$ language plpgsql;

-- The three-argument version is now ambiguous with the four-argument one when
-- called with three arguments, because the fourth has a default. PostgREST
-- resolves an RPC by the exact key set it is given, so both would match and it
-- errors with PGRST203 rather than choosing. Dropping the old signature leaves
-- exactly one candidate.
--
-- Safe to drop: nothing but repositories/lead.py::update_stage calls this, and
-- that call ships with this migration.
drop function if exists update_lead_stage(uuid, uuid, text);
