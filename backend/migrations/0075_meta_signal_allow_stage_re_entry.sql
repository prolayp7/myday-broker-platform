-- A lead could not return to a stage it had already been.
--
-- REPRODUCED AGAINST THE LIVE DATABASE before writing this:
--
--   1. -> Quote Done : ok
--   2. -> Pending    : ok
--   3. -> Quote Done : FAILED
--        duplicate key value violates unique constraint
--        "idx_meta_signal_lead_event_active"
--
-- The conversion triggers (0057) did a plain INSERT into meta_signal, which
-- carries a unique index on (lead_id, event_name) where status <> 'Failed'
-- (0056). Once the first signal had been SENT by the drain, re-entering the
-- stage violated that index — and because the trigger runs INSIDE the stage
-- change, the violation rolled the whole stage change back. The broker saw a
-- raw database error, and the lead was stuck out of that stage for good.
--
-- It contradicted the rule the pipeline is built on: any stage to any stage,
-- because real conversations do not follow a fixed order. A customer who goes
-- quiet after a quote and comes back a month later is exactly the case it
-- broke on — and Sold was worse, since a lead corrected off Sold could never
-- be marked sold again.
--
-- WHAT ON CONFLICT DO NOTHING ACTUALLY MEANS HERE, per case:
--
--   existing signal   result                  why
--   ---------------   ---------------------   ------------------------------
--   Sent              no new row              Meta already recorded that
--                                             conversion; a second one counts
--                                             the same customer twice and
--                                             corrupts the optimisation.
--   Pending           no new row              it has not gone yet, but it
--                                             will — a second row is the same
--                                             event twice.
--   Failed            A NEW ROW IS CREATED    the index excludes Failed, so
--                                             there is no conflict to skip and
--                                             a genuine failure stays
--                                             retryable.
--
-- So the guarantee the index exists for is untouched: Meta is told exactly
-- once per lead per event. The only thing that changes is the CONSEQUENCE of
-- the collision. "Meta already knows" was never a reason to refuse a broker
-- moving their own lead.
--
-- Belt and braces, incidentally: send_event passes meta_signal.id as
-- event_id, and Meta deduplicates on that itself. The index is ours; that is
-- theirs.
--
-- ALL THREE INSERTS, not just the two on stage history. The Lead event fires
-- from lead_delivery, and a lead delivered twice — a drain re-run, a manual
-- re-assignment path — would hit the same wall for the same reason.
--
-- BUILT FROM THE CURRENT DEFINITION, not the original. fn_meta_signal_on_stage_history
-- was introduced in 0057 and REWRITTEN in 0067 (the premium column rename), so
-- 0067 is the version this replaces. The Lead trigger below has only ever been
-- defined in 0057, so that one does come from there.
--
-- Diffed both bodies before applying: the only difference from the current
-- definitions is the two `on conflict do nothing` clauses.

-- ─── Lead: fires when a lead is delivered to a broker ────────────────────
create or replace function fn_meta_signal_on_lead_delivery()
returns trigger as $$
declare
  v_platform text;
begin
  select source into v_platform from lead where id = new.lead_id;

  -- Only Meta is built today (see meta_signal.platform's own check
  -- constraint) — a TikTok-sourced lead has nowhere to send a signal yet, so
  -- skip it rather than insert a row nothing will ever process correctly.
  if v_platform = 'meta' then
    insert into meta_signal (lead_id, platform, event_name, triggered_by)
    values (new.lead_id, 'Meta', 'Lead', null)
    on conflict do nothing;
  end if;

  return new;
end;
$$ language plpgsql;


-- ─── CompleteRegistration / Purchase: fire on stage movement ─────────────
create or replace function fn_meta_signal_on_stage_history()
returns trigger as $$
declare
  v_platform text;
  v_total_new_premium numeric;
begin
  if new.to_stage not in ('Quote Done', 'Sold') then
    return new;
  end if;

  select source into v_platform from lead where id = new.lead_id;
  if v_platform <> 'meta' then
    return new;
  end if;

  if new.to_stage = 'Quote Done' then
    insert into meta_signal (lead_id, platform, event_name, triggered_by)
    values (new.lead_id, 'Meta', 'CompleteRegistration', new.broker_id)
    on conflict do nothing;

  elsif new.to_stage = 'Sold' then
    -- ANNUAL, and no ordering — both per 0067, which renamed the column and
    -- collapsed premium_history to one row per lead. Reporting a twelfth of
    -- the policy's worth would train Meta's model on a figure wrong by an
    -- order of magnitude, and there is nothing left to order by.
    --
    -- The first draft of THIS migration copied 0057's body verbatim and
    -- silently reverted both, because 0057 is where the function was
    -- introduced and 0067 rewrote it in passing. The Sold test caught it:
    -- 'column "total_new_premium" does not exist'. Worth stating plainly —
    -- create-or-replace migrations must be built from the CURRENT definition,
    -- not the original one.
    select total_new_premium_annual into v_total_new_premium
    from premium_history
    where lead_id = new.lead_id
    limit 1;

    insert into meta_signal (lead_id, platform, event_name, triggered_by, payload)
    values (
      new.lead_id, 'Meta', 'Purchase', new.broker_id,
      -- Not the final POST payload (that's built and hashed by the sender) —
      -- a small note-to-self so a 'Sold' lead with no premium row is visible
      -- immediately in this table, rather than only discovered when the
      -- sender tries to build a Purchase event with nothing to put in value.
      jsonb_build_object('total_new_premium_at_sale', v_total_new_premium)
    )
    on conflict do nothing;
  end if;

  return new;
end;
$$ language plpgsql;

-- The triggers themselves are unchanged and still bound to these functions;
-- create or replace rebinds them automatically.
