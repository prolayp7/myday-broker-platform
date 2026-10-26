-- CompleteRegistration moves off Quote Done and fires as soon as a lead is
-- qualified.
--
-- THE POINT OF EVERYTHING THAT LED HERE. Quote Done is days late: a quote is
-- real work, and Meta's model waited that long to learn anything. Measured
-- before this was written, 50 leads sat at In Progress against 19 at Quote
-- Done — so the old firing point ignored most of the qualified book.
--
-- WHAT CHANGES. Exactly one line of the function body:
--
--     if new.to_stage <> 'Quote Done' then        ->  if new.to_stage in ('New', 'Lost') then
--
-- Everything else is carried over BYTE-FOR-BYTE from 0100: the source gate, the
-- `is true` qualification gate, the premium select, the payload key, and
-- `on conflict do nothing`. Nothing about what is sent changes — only when.
--
-- WHY A DENY-LIST, NOT AN ALLOW-LIST. Stages are free-form: a broker may go
-- New -> Quote Done, or New -> Sold, skipping In Progress entirely. An
-- allow-list of ('In Progress') would fire nothing for those leads AND stop
-- Quote Done firing, which is a silent loss of every conversion from a broker
-- who works that way. Naming the two stages that must NEVER signal is the
-- statement that cannot go stale when a stage is added later.
--
--   New   — the lead has only arrived. fn_meta_signal_on_lead_created (0096)
--           already fires the `Lead` event there; a CompleteRegistration would
--           report a conversion nobody has looked at.
--   Lost  — a dead lead, including 'Lost' carrying the 'Junk' label. Reporting
--           it as a conversion is the exact opposite of the truth.
--
-- FIRES ONCE, WHICHEVER STAGE COMES FIRST:
--
--   New -> In Progress                        fires at In Progress
--   New -> In Progress -> Quote Done          fires at In Progress, NOT again
--   New -> Quote Done  (skips it)             fires at Quote Done
--   New -> Sold        (skips both)           fires at Sold
--   New -> Lost                               fires nothing
--
-- The "not again" is guaranteed by 0112's
-- meta_signal_lead_event_dataset_key — unique (lead_id, event_name,
-- dataset_target) across EVERY status, so the second insert hits
-- `on conflict do nothing`. That hole was closed first, deliberately: before
-- 0112 the unique index excluded Failed rows, so a second transition could
-- insert a second row with a new id, and the id IS the event_id Meta
-- deduplicates on. Widening this trigger on top of that would have double
-- counted conversions.
--
-- THE FOUR-YEAR RULE IS ALREADY IN FRONT OF THIS, and this function needs no
-- knowledge of it. services/lead.py refuses the stage move when the applicant
-- has no Continuous Insurance date, and writes is_qualified = false when the
-- customer was first insured under MIN_INSURED_YEARS ago. So a recent customer
-- arrives here with is_qualified false and is stopped by the gate that already
-- exists. That is exactly why the rule records `false` rather than inventing a
-- new state.
--
-- THIS IS THE IRREVERSIBLE ONE. Meta deduplicates on event_id, so a conversion
-- once sent cannot be recalled, corrected or withdrawn. It is applied LAST, and
-- only with the four-year guard already live and green.
--
-- THE 50 LEADS ALREADY AT IN PROGRESS WILL NOT FIRE. This is an after-insert
-- trigger on lead_stage_history, and nothing re-triggers a past transition —
-- they signal only if they move again. That is deliberate: back-filling history
-- rows to force them would queue 50 conversions for leads nobody re-judged
-- under the new rule.
--
-- fn_meta_signal_on_lead_created (0096) is NOT touched. Lead still fires on
-- arrival — the negative signal Meta weighs the positive ones against.
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.

create or replace function fn_meta_signal_on_stage_history()
returns trigger as $$
declare
  v_platform text;
  v_is_qualified boolean;
  v_total_new_premium numeric;
begin
  -- EVERY STAGE EXCEPT THESE TWO SIGNALS. See the header for why this is a
  -- deny-list: a broker who skips In Progress must still be reported, and an
  -- allow-list would silently drop them.
  --
  -- 'Junk' needs no mention — it is a LABEL on Lost (lead.sub_stage), not a
  -- stage, so excluding Lost excludes it.
  if new.to_stage in ('New', 'Lost') then
    return new;
  end if;

  select source, is_qualified
  into v_platform, v_is_qualified
  from lead where id = new.lead_id;

  if v_platform <> 'meta' then
    return new;
  end if;

  -- THE GATE THE FOUR-YEAR RULE WRITES THROUGH. A customer insured less than
  -- MIN_INSURED_YEARS ago reaches here with is_qualified = false, set by
  -- services/lead.py before the stage was written, and stops at this line.
  if v_is_qualified is not true then
    return new;
  end if;

  -- One premium row per lead (idx_premium_history_lead_id_unique), which is
  -- why there is no order by — 0067 removed it for that reason.
  select total_new_premium_annual into v_total_new_premium
  from premium_history
  where lead_id = new.lead_id
  limit 1;

  -- OFTEN NULL NOW, AND THAT IS CORRECT. At In Progress there is usually no
  -- quote yet — the whole reason for firing earlier. services/meta_signal.py
  -- no longer treats a premium-less CompleteRegistration as a failure: it
  -- sends the event without value/currency, which Meta accepts. The premium
  -- still rides along whenever one exists.
  --
  -- Snapshotted AT THIS MOMENT and never revisited: event_id is what Meta
  -- deduplicates on, so a premium corrected after this row exists sends
  -- nothing and changes nothing. What was true at the transition is what Meta
  -- is told.
  insert into meta_signal (lead_id, platform, event_name, triggered_by, payload)
  values (
    new.lead_id, 'Meta', 'CompleteRegistration', new.broker_id,
    jsonb_build_object('total_new_premium_at_registration', v_total_new_premium)
  )
  on conflict do nothing;

  return new;
end;
$$ language plpgsql;

-- The trigger itself is unchanged and NOT recreated: create or replace function
-- swaps the body underneath the existing trg_meta_signal_on_stage_history, so
-- there is no window in which it is missing.


-- ── After applying, this should hold ────────────────────────────────────
--
-- Move a qualified Meta lead New -> In Progress, then:
--
--   select event_name, status, count(*)
--   from meta_signal
--   where lead_id = '<that lead>'
--   group by 1, 2;
--
--     Lead                  Sent      1     (fired on arrival, 0096)
--     CompleteRegistration  Pending   1     (fired just now)
--
-- Move the same lead on to Quote Done and re-run it: the counts must not
-- change. A second CompleteRegistration row means 0112's constraint is not
-- doing its job and Meta is about to be told twice.
