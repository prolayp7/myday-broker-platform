-- Correcting an issued date must not look like a stage change.
--
-- WHAT WAS WRONG. 0085 made the stage and the date one transaction, which was
-- the point — but it wrote the history row unconditionally. So correcting a
-- wrong date, which is Issued -> Issued, appended:
--
--     lead_stage_history: from_stage = 'Issued', to_stage = 'Issued'
--
-- The lead did not move. lead_stage_history is the broker's own timeline of a
-- lead's journey (0029), and a row saying it moved from Issued to Issued is a
-- fact that never happened. Fixing a typo would age the lead's history with
-- entries indistinguishable from real progress.
--
-- Not hypothetical: the test suite for 0085 wrote four of these before anyone
-- read the table.
--
-- HOW IT IS FIXED. Re-entering the stage a lead is already on is a DATE
-- CORRECTION, not a move: write the date, return, touch nothing else.
--
-- ONLY FOR ISSUED. Every other stage re-picked is genuinely nothing to do, and
-- the screen does not offer it. Issued is the exception because it is the one
-- stage carrying a value that can be wrong on its own.
--
-- REBUILT FROM THE CURRENT DEFINITION (0085). Everything is unchanged except
-- the early-return block marked below.

create or replace function update_lead_stage(
  p_lead_id uuid,
  p_broker_id uuid,
  p_new_stage text,
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

  -- === THE CORRECTION PATH. Issued -> Issued changes only the date. ===
  --
  -- Before the ownership guard would be wrong (a stranger could rewrite the
  -- date); after it is right, because correcting a date is still something
  -- only the holder may do.
  --
  -- The stage update is skipped as well as the history row: it would set
  -- stage to the value it already holds, which is a no-op write that only
  -- serves to bump the row.
  if v_current_stage = 'Issued' and p_new_stage = 'Issued' then
    update premium_history
    set issued_at = p_issued_at
    where lead_id = p_lead_id;

    return p_new_stage;
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

-- The four rows 0085's own test suite wrote before this was caught. Narrow on
-- purpose: from_stage = to_stage = 'Issued' is only ever produced by the bug
-- this migration fixes, so there is nothing real to lose.
delete from lead_stage_history
where from_stage = 'Issued' and to_stage = 'Issued';
