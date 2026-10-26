-- A sale queues Purchase, in the same transaction as the stage that records it.
--
-- WHY THIS EXISTS. 0123 restored Purchase as a legal event and taught
-- ensure_conversion_signals when one is owed, but nothing calls the function on
-- a sale. So a lead reaching Sold today writes its stage and its history row and
-- tells Meta nothing. This is the last of the four inputs.
--
-- ── WHY INSIDE THIS FUNCTION, NOT A TRIGGER ─────────────────────────────
--
-- 0123 deliberately dropped trg_meta_signal_on_stage_history rather than
-- swapping its body, because it fired on EVERY lead_stage_history insert: 507
-- non-sale rows against 6 sales in production, and assign_lead writes one too
-- (0030). Since reconciliation propagates failure by design, a trigger there
-- would let a Meta outage block ordinary stage moves and lead assignment.
--
-- update_lead_stage knows the DESTINATION stage, so it can call the function for
-- the three stages that matter and no others. That is the whole reason the wiring
-- lives here: the blast radius of a Meta failure becomes exactly "a broker could
-- not record a sale", which is a failure worth surfacing — the alternative is a
-- sale Meta is never told about, and Purchase carries the money.
--
-- ── WHY THREE STAGES ────────────────────────────────────────────────────
--
-- Stages are free-form: there is no transition table and brokers demonstrably
-- skip (New -> Quote Done 57 times, New -> Sold once, New -> Pending 4 times).
-- A lead can go Pending -> Issued and never touch Sold, and under a Sold-only
-- rule that sale would report nothing though an issued policy is unquestionably
-- downstream of one.
--
-- 0051 records Submitted as "Policy Submitted" — the application has gone to the
-- carrier — and Issued as when the carrier actually wrote it. Both sit past the
-- customer committing.
--
-- THE REPEATS ARE FREE. ensure_conversion_signals asks what stage the lead is AT
-- rather than which transition happened, and the uniqueness constraint makes the
-- second and third calls no-ops. Sold -> Submitted -> Issued queues exactly one
-- Purchase per dataset.
--
-- ── BUILT FROM 0086, WHICH IS THE CURRENT DEFINITION ────────────────────
--
-- The chain is 0032 -> 0085 -> 0086. 0075's header records what happens if this
-- is got wrong: its first draft copied 0057 verbatim and silently reverted an
-- earlier rename, breaking Sold. Diffed against 0086 before writing. The ONLY
-- differences are the two `perform` calls below; every guard, the correction
-- path, the history insert and the issued-date handling are byte-identical.
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.

begin;


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

    -- RECONCILED HERE TOO, even though this path changes no stage and no
    -- premium. The lead is already at Issued, so its Purchase should already
    -- exist — this is a cheap second chance at a row that failed to queue
    -- earlier, and costs one indexed read when there is nothing to do.
    perform ensure_conversion_signals(p_lead_id);

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

  -- === THE SALE REACHES META, OR THE SALE DOES NOT HAPPEN. ===
  --
  -- ONLY the three sale stages. A move to New, In Progress, Quote Done, Pending
  -- or Lost calls nothing at all — reconciliation propagates failure, and a
  -- broker must never be unable to move a lead through the pipeline because
  -- Meta is unreachable. That coupling is exactly what 0123 removed by dropping
  -- the stage-history trigger, and re-adding it here for every stage would undo
  -- that in a subtler place.
  --
  -- ON A SALE it is the opposite: the write and the conversion are two halves of
  -- one fact. Purchase carries the annual premium, and a sale recorded without
  -- one queued is a silent permanent loss — Meta deduplicates on event_id, so it
  -- cannot be sent later. A failed sale is a retry the broker can see.
  --
  -- The function is idempotent and gated on source, so a non-Meta sale and a
  -- second move within the sale stages both cost one indexed read and insert
  -- nothing.
  if p_new_stage in ('Sold', 'Submitted', 'Issued') then
    perform ensure_conversion_signals(p_lead_id);
  end if;

  return p_new_stage;
end;
$$ language plpgsql;


commit;


-- ── After applying, these should hold ───────────────────────────────────
--
-- Take a QUALIFIED Meta lead with a premium row carrying
-- total_new_premium_annual, sitting at Pending.
--
-- Move it STRAIGHT TO ISSUED, never touching Sold:
--
--   select update_lead_stage('<lead>', '<its broker>', 'Issued', current_date);
--
--   select event_name, dataset_target, payload from meta_signal
--   where lead_id = '<lead>' and event_name = 'Purchase';
--     -> one row on common_full (and cream_full if the lead is cream),
--        payload carrying total_new_premium_at_purchase
--
-- Move it on to Sold, then Submitted, and re-run that query: the count MUST NOT
-- change. A second row means the uniqueness constraint is not holding and Meta
-- is about to be told twice.
--
-- An ORDINARY move queues nothing and — this is the part that matters — cannot
-- fail because of Meta:
--
--   select update_lead_stage('<another lead>', '<its broker>', 'In Progress');
--     -> succeeds; no Purchase row
--
-- A MANUAL lead at a sale stage queues nothing, via the source gate inside
-- ensure_conversion_signals rather than anything in this function.
