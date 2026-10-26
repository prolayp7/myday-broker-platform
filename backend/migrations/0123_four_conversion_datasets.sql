-- Four conversion datasets, and a conversion that no longer depends on a stage
-- move.
--
-- WHAT THIS REPLACES. Until now CompleteRegistration was created by exactly one
-- thing: trg_meta_signal_on_stage_history, firing `after insert on
-- lead_stage_history`. That table only gets a row when a broker changes a stage,
-- so a broker who worked a lead thoroughly and never touched the stage dropdown
-- produced no judgement, no signal, and nothing Meta could ever be told —
-- permanently, because Meta deduplicates on event_id and an event not sent at
-- the time can never be sent later.
--
-- THE REPLACEMENT is ensure_conversion_signals(lead_id): one idempotent
-- function that asks, from CURRENT STATE, which conversion rows should exist and
-- inserts whatever is missing. Its callers are the four inputs that can change
-- the answer — qualification, the principal driver's cream facts, the policy
-- type, and a move into a sale stage — each wired in a later step.
--
-- THE DROP AND THE REPLACEMENT SHIP IN THE SAME FILE, DELIBERATELY. Splitting
-- them would leave a window with no producer of CompleteRegistration at all:
-- the trigger below is the only one today, and the cream path cannot compensate
-- because repositories/meta_signal.py::insert_cream_registration refuses unless
-- a common row already exists. Every qualified lead in that window would be lost
-- to the 7-day limit. One file, one transaction, no window.
--
-- NOT A BODY SWAP. 0116 changed behaviour with `create or replace function`,
-- leaving trg_meta_signal_on_stage_history bound to every stage-history insert —
-- correct there, because the firing point was not what changed. Here the firing
-- point IS what changes, so the trigger is dropped outright. Leaving it attached
-- would run reconciliation on all 513 stage-history rows (only 6 are sales) and,
-- because the new function is failure-propagating by design, a Meta outage would
-- block ordinary stage moves — and assign_lead writes a stage-history row too
-- (0030), so it could block lead ASSIGNMENT.
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.

begin;


-- ── 1. Four dataset targets ─────────────────────────────────────────────
--
-- TWO AXES: quality (all qualified / cream) x depth (stops at qualified /
-- through the sale). The '_full' pair receives Purchase; the other two never do,
-- even for a customer who buys. That is the whole reason there are four rather
-- than two — Meta optimises differently against a model that has seen purchases.
--
-- 'common' AND 'cream' KEEP THEIR NAMES so no historical row is rewritten. The
-- 222 rows already in this table stay correct as they are.
alter table meta_signal
  drop constraint if exists meta_signal_dataset_target_check;

alter table meta_signal
  add constraint meta_signal_dataset_target_check
  check (dataset_target in ('common', 'common_full', 'cream', 'cream_full'));


-- ── 2. Purchase becomes a legal event again ─────────────────────────────
--
-- 0100 removed it, and its reasoning was sound AT THE TIME: "It has NEVER FIRED
-- — not once … and no lead has ever reached Sold (81 leads: New 57, Lost 17,
-- Pending 6, Quote Done 1)." Six leads have now reached Sold, five of them Meta.
-- The condition that made Purchase worthless no longer holds, and 0100
-- deliberately made restoring it cost a migration rather than a config change.
alter table meta_signal
  drop constraint if exists meta_signal_event_name_check;

alter table meta_signal
  add constraint meta_signal_event_name_check
  check (event_name in ('Lead', 'CompleteRegistration', 'Purchase'));


-- ── 3. The arrival Lead event fans out to all four ──────────────────────
--
-- WHY ALL FOUR, including the cream pair: at arrival nobody knows whether a lead
-- is cream — those facts arrive days later from the broker and the DASH report.
-- Sending every arrival everywhere gives cream a baseline of the whole book
-- against its premium few conversions, so Meta learns to spot cream customers
-- AMONG EVERYONE, which is exactly what it must do at bid time.
--
-- The alternative breaks: a Lead event sent only once a lead is classified cream
-- gives a tiny baseline with no negative examples, and Meta rejects events dated
-- more than 7 days old while leads take days to be decided.
--
-- THE BUG THIS FIXES. The previous body omitted dataset_target entirely, so the
-- column default ('common', 0112) silently made it a common row. Measured before
-- writing: all 189 Lead rows ever sent are 'common', zero in any other target.
--
-- ONE STATEMENT, four rows, inside the transaction that inserts the lead, with
-- (lead_id, event_name, dataset_target) uniqueness making a replay a no-op.
create or replace function fn_meta_signal_on_lead_created()
returns trigger as $$
begin
  -- new.source directly: the row being inserted IS the lead, so there is
  -- nothing to look up. Manual, website and tiktok leads create nothing —
  -- ensure_conversion_signals below enforces the same rule for every later
  -- event, so the source gate exists in exactly two places and both are here.
  if new.source = 'meta' then
    insert into meta_signal (lead_id, platform, event_name, dataset_target, triggered_by)
    values
      (new.id, 'Meta', 'Lead', 'common',      null),
      (new.id, 'Meta', 'Lead', 'common_full', null),
      (new.id, 'Meta', 'Lead', 'cream',       null),
      (new.id, 'Meta', 'Lead', 'cream_full',  null)
    on conflict do nothing;
  end if;

  return new;
end;
$$ language plpgsql;

comment on function fn_meta_signal_on_lead_created() is
  'Queues the Lead conversion into all four datasets the moment a Meta lead is downloaded into the CRM. Writes the outbox rows only — the drain sends them.';


-- ── 4. The single reconciliation function ───────────────────────────────
--
-- IT ASKS "WHAT SHOULD EXIST", NEVER "WHAT JUST HAPPENED". Every decision is
-- read from current state, so calling it twice inserts once, calling it when
-- nothing changed does nothing, and the order its inputs arrive in cannot matter.
-- That is the same stance services/meta_signal_cream.py already documents, and
-- the reason it needs no queue, no state machine and no status column.
--
-- SAFE TO CALL FROM ANY PATH, at any time, for any lead.
create or replace function ensure_conversion_signals(p_lead_id uuid)
returns void as $$
declare
  v_source              text;
  v_is_qualified        boolean;
  v_policy_type         text;
  v_stage               text;
  v_premium             numeric;
  v_licensed_5y         boolean;
  v_convictions         int;
  v_at_fault            int;
  v_is_cream            boolean;
  v_cream_full_exists   boolean;
begin
  select source, is_qualified, combined_policy_type, stage
    into v_source, v_is_qualified, v_policy_type, v_stage
  from lead
  where id = p_lead_id;

  -- ── THE SOURCE GATE, FIRST AND UNCONDITIONAL ──────────────────────────
  --
  -- The old trigger gated on this as its third statement, and losing it while
  -- rewriting was the one mistake in this design that would have sent Meta data
  -- that was FALSE rather than merely incomplete.
  --
  -- It matters most for Purchase, which deliberately has no qualification gate
  -- (money is a fact, not a judgement). Without this line a manual walk-in with
  -- a premium moved to Sold would queue a conversion telling Meta an
  -- advertisement produced that customer. One manual lead is already at Sold in
  -- production; it would have fired on its next reconciliation.
  --
  -- INSIDE THE FUNCTION, not in its callers: a caller-side check would need
  -- repeating in four places and re-adding by whoever wires the fifth. It also
  -- covers website leads today and becomes `in ('meta','tiktok')` in exactly one
  -- place if TikTok ingestion is ever built.
  if v_source is distinct from 'meta' then
    return;
  end if;

  -- ── CompleteRegistration: the broker's answer, and nothing else ────────
  --
  -- No stage requirement (a lead judged at New fires), no premium requirement
  -- (this event carries no money), no insurance-history requirement. `is true`
  -- rather than truthiness because the column is three-state and null means
  -- "nobody has been asked", which must not pass.
  if v_is_qualified is true then
    insert into meta_signal (lead_id, platform, event_name, dataset_target, triggered_by)
    values
      (p_lead_id, 'Meta', 'CompleteRegistration', 'common',      null),
      (p_lead_id, 'Meta', 'CompleteRegistration', 'common_full', null)
    on conflict do nothing;
  end if;

  -- ── Cream: four conditions, all of which must hold ────────────────────
  --
  -- Read from the PRINCIPAL driver only — build_user_data reads that row alone,
  -- so a spouse's record is not what Meta would be told about.
  select licensed_5y_plus, convictions_3y, at_fault_claims_6y
    into v_licensed_5y, v_convictions, v_at_fault
  from driver
  where lead_id = p_lead_id and is_principal
  limit 1;

  -- UNKNOWN IS NEVER CREAM. A null count means nobody has looked, which is the
  -- opposite of a clean record — `= 0` rather than a falsy test, because null
  -- and zero mean opposite things here. This is why 0114 gave these columns no
  -- defaults. A null at-fault count means "not cream YET": when DASH later
  -- writes 0, reconciliation re-runs and the cream rows appear.
  --
  -- TENANT IS EXCLUDED DELIBERATELY. A tenant bundle measures ~82% of a
  -- homeowner one by premium, so the case is not value but distinctiveness:
  -- include tenants and cream becomes most of the bundled book, saying little
  -- that the common dataset does not.
  v_is_cream := v_is_qualified is true
            and v_policy_type in ('Auto and Homeowner', 'Auto and Condo')
            and v_licensed_5y is true
            and v_convictions = 0
            and v_at_fault = 0;

  if v_is_cream then
    insert into meta_signal (lead_id, platform, event_name, dataset_target, triggered_by)
    values
      (p_lead_id, 'Meta', 'CompleteRegistration', 'cream',      null),
      (p_lead_id, 'Meta', 'CompleteRegistration', 'cream_full', null)
    on conflict do nothing;
  end if;

  -- ── Purchase: a state test, not an event test ─────────────────────────
  --
  -- ALL THREE SALE STAGES, because stages are free-form — there is no transition
  -- table and brokers demonstrably skip (New -> Quote Done 57 times, New -> Sold
  -- once). A lead can go Pending -> Issued and never touch Sold; under a
  -- Sold-only rule that sale would report nothing, though an issued policy is
  -- unquestionably downstream of one.
  --
  -- Asking "what stage is this lead at" rather than "which transition just
  -- happened" also makes the repeats free: Sold queues the row, then Submitted
  -- and Issued each re-run this and hit `on conflict do nothing`.
  if v_stage in ('Sold', 'Submitted', 'Issued') then
    -- One premium row per lead (idx_premium_history_lead_id_unique), which is
    -- why there is no order by — 0067 removed it for that reason.
    select total_new_premium_annual into v_premium
    from premium_history
    where lead_id = p_lead_id
    limit 1;

    if v_premium is not null then
      -- THE PREMIUM IS SNAPSHOTTED HERE AND NEVER REVISITED. A sale at 2400
      -- corrected to 2650 an hour later still reports 2400: what was true at the
      -- sale is what Meta is told, and the event cannot be amended afterwards
      -- because deduplication keys on its id. 0116 states the same rule for
      -- CompleteRegistration; Purchase inherits it rather than inventing another.
      insert into meta_signal (lead_id, platform, event_name, dataset_target, triggered_by, payload)
      values (
        p_lead_id, 'Meta', 'Purchase', 'common_full', null,
        jsonb_build_object('total_new_premium_at_purchase', v_premium)
      )
      on conflict do nothing;

      -- CREAM MEMBERSHIP, ONCE ENTERED, IS NOT REVOKED.
      --
      -- The first clause is the load-bearing one: if this lead already entered
      -- the cream funnel, its sale must follow, even if a later CRM edit changed
      -- a fact. Otherwise that dataset holds a conversion with no matching sale,
      -- teaching the model that cream leads do not buy — and Meta cannot be told
      -- a conversion was withdrawn.
      --
      -- The second catches a lead whose cream facts only completed after it was
      -- qualified, which is the ordinary case given DASH arrives late.
      select exists (
        select 1 from meta_signal
        where lead_id = p_lead_id
          and event_name = 'CompleteRegistration'
          and dataset_target = 'cream_full'
      ) into v_cream_full_exists;

      if v_cream_full_exists or v_is_cream then
        insert into meta_signal (lead_id, platform, event_name, dataset_target, triggered_by, payload)
        values (
          p_lead_id, 'Meta', 'Purchase', 'cream_full', null,
          jsonb_build_object('total_new_premium_at_purchase', v_premium)
        )
        on conflict do nothing;
      end if;
    end if;
  end if;
end;
$$ language plpgsql;

comment on function ensure_conversion_signals(uuid) is
  'The single producer of CompleteRegistration and Purchase rows. Idempotent: reads current state and inserts only what is missing. Refuses any lead whose source is not meta.';


-- ── 5. The old stage-driven path is removed entirely ────────────────────
--
-- THE TRIGGER, NOT JUST THE FUNCTION. See this file's header: a body swap would
-- leave it bound to every lead_stage_history insert, which is 507 non-sale rows
-- against 6 sales, and would make ordinary stage moves — and lead assignment —
-- fail whenever Meta signalling does.
--
-- Sale-stage reconciliation is wired into update_lead_stage in a later step,
-- where the destination stage is known and only the three that matter call it.
drop trigger if exists trg_meta_signal_on_stage_history on lead_stage_history;
drop function if exists fn_meta_signal_on_stage_history();


commit;


-- ── After applying, these should hold ───────────────────────────────────
--
-- The old path is gone — both queries return no rows:
--
--   select 1 from pg_trigger where tgname = 'trg_meta_signal_on_stage_history';
--   select 1 from pg_proc where proname = 'fn_meta_signal_on_stage_history';
--
-- The new function exists and refuses non-Meta leads. Against any manual lead:
--
--   select ensure_conversion_signals('<a manual lead id>');
--   select count(*) from meta_signal where lead_id = '<that lead>';   -- 0
--
-- Calling it twice on a qualified Meta lead inserts once:
--
--   select ensure_conversion_signals('<a qualified meta lead>');
--   select ensure_conversion_signals('<the same lead>');
--   select event_name, dataset_target, count(*)
--   from meta_signal where lead_id = '<that lead>'
--   group by 1, 2;                        -- every count must be 1
--
-- A NEW Meta lead (insert one on staging) gets four Lead rows:
--
--   select dataset_target from meta_signal
--   where lead_id = '<new lead>' and event_name = 'Lead'
--   order by dataset_target;
--     cream / cream_full / common / common_full
--
-- and a new MANUAL lead gets none.
--
-- Nothing is backfilled: existing leads keep the rows they already have, and
-- the 189 historical Lead rows stay 'common'.
