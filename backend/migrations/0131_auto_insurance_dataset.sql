-- Auto Insurance becomes a dataset of its own, the four new datasets start
-- clean, and "cream" is called Preferred everywhere it lives.
--
-- WHY. New PolicyScanner ads start on the Auto Insurance dataset because it
-- already holds CRM history, while Common, Common Full, Preferred and
-- Preferred Full build their own. Until now there was no target for Auto
-- Insurance: services/meta_signal.py let 'common' fall back to
-- META_CAPI_DATASET_ID, and production only ever set that variable. So 'common'
-- WAS Auto Insurance, and the moment 'common' gets its own id Auto Insurance
-- would stop hearing anything. 'legacy' is that missing fifth target. It reads
-- META_CAPI_DATASET_ID; 'common' loses its fallback in the same release.
--
-- WHAT EACH TARGET RECEIVES after this:
--
--                    Lead          CompleteRegistration   Purchase
--   legacy           every lead    every qualified        every sale
--   common           every lead    every qualified        never
--   common_full      every lead    every qualified        every sale
--   preferred        every lead    Preferred only         never
--   preferred_full   every lead    Preferred only         Preferred sales only
--
-- ── 1. "CREAM" BECOMES "PREFERRED", IN THE DATABASE TOO ─────────────────
--
-- The Meta datasets are named Preferred, the screen says Preferred, and the
-- Railway variables are META_CAPI_DATASET_PREFERRED(_FULL). The database said
-- 'cream'. One word for one thing, so every live object is renamed here:
--
--   meta_signal.dataset_target   'cream' -> 'preferred',
--                                'cream_full' -> 'preferred_full'
--   the target check constraint  allows the new names only
--   ensure_conversion_signals    literals and variables
--   the driver trigger           trg_reconcile_on_driver_cream_facts and its
--                                function become ..._preferred_facts
--
-- Old migrations keep the old word: they are history, not live objects.
--
-- ── 2. RELABEL BY RECORDED DESTINATION, NOT BY LABEL ──────────────────────
--
-- Measured in production on 2026-10-05, every 'common' row was Sent:
--
--   209  payload.dataset_id = 3253348891512104 (Auto Insurance)  21 Aug - 29 Sep
--     1  payload.dataset_id = 1047675657670808 (now Common)      21 Aug 03:42
--    39  dataset_id not recorded (sent before it was stored)     18 - 21 Aug
--
-- Only the 209 become 'legacy'. Their ids are Meta's event_id, so relabelling
-- cannot send anything twice. The other 40 STAY 'common': one is confirmed to
-- have reached the dataset now called Common, and the 39 have NO RECORDED
-- DESTINATION and are retained as 'common' as the conservative choice — the
-- one recorded neighbour went to that dataset, but nothing proves the rest did.
--
-- ── 3. THE NEVER-SENT QUEUE IS CLEARED ──────────────────────────────────
--
-- common_full / cream / cream_full held only Pending rows (34 / 24 / 23, none
-- Sent or Failed): their datasets were never configured. Most are past Meta's
-- 7-day event_time limit and would fail terminally the moment the datasets
-- are. Deleting all of them makes the four new datasets start at the same
-- moment, this migration.
--
-- ── 4. RULE A: A NEW DATASET ONLY HEARS FOLLOW-UPS FOR LEADS IT SAW ARRIVE ─
--
-- CompleteRegistration and Purchase go to common / common_full / preferred /
-- preferred_full only where that target holds the lead's Lead row. So leads
-- from before this migration keep reporting to Auto Insurance alone, and no
-- new dataset holds a quote or a sale without its lead.
--
-- KNOWN LIMITATION: the rule checks that a Lead ROW EXISTS, not that Meta
-- received it. For the 40 retained 'common' rows above that is the honest
-- best available — their destination is partly unrecorded.
--
-- 'legacy' is UNGUARDED, exactly as 'common' behaved while it borrowed Auto
-- Insurance's id. OWNER DECISION (2026-10-05): a lead sold before this switch
-- and reconciled again later can queue its first 'legacy' Purchase with a
-- fresh event_time. Accepted: those leads came from the Insurance with Eldho
-- ads, not the PolicyScanner ads that will optimise on Auto Insurance.
--
-- ── ROLLOUT, AND WHY THERE IS NO REVERSE MIGRATION ──────────────────────
--
-- Apply with sending PAUSED (META_CAPI_ACCESS_TOKEN removed), so no row is in
-- flight while it is relabelled. The previous code does not know 'legacy' or
-- 'preferred' and cannot send them, so reverting code after this would
-- silently starve those datasets. If the switch goes wrong: stay paused and
-- fix forward — Pending rows wait safely, well inside Meta's 7-day limit.
--
-- ensure_conversion_signals BELOW CARRIES 0130's RULE (Auto and Tenant counts
-- as Preferred). Check production has 0130 before applying this; if it does
-- not, this migration switches that rule on too.
--
--   select prosrc like '%Auto and Tenant%'
--   from pg_proc where proname = 'ensure_conversion_signals';
--
-- SAFE TO RE-RUN: every step is idempotent, so a database that ran the first
-- draft of this file (staging) is brought to the same end state.
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.
-- Staging first.

begin;


-- ── 1. History and queue, under no constraint while they change ─────────
alter table meta_signal
  drop constraint if exists meta_signal_dataset_target_check;

-- What reached Auto Insurance is labelled Auto Insurance.
update meta_signal
   set dataset_target = 'legacy'
 where dataset_target = 'common'
   and payload->>'dataset_id' = '3253348891512104';

-- The never-sent queue. Both spellings, so a re-run after a partial apply
-- clears the same rows.
delete from meta_signal
 where dataset_target in ('common_full', 'cream', 'cream_full',
                          'preferred', 'preferred_full')
   and status = 'Pending';

-- Whatever is left under the old word is renamed, never dropped. Production
-- held none on 2026-10-05; this is here so that is a fact, not an assumption.
update meta_signal set dataset_target = 'preferred'      where dataset_target = 'cream';
update meta_signal set dataset_target = 'preferred_full' where dataset_target = 'cream_full';


-- ── 2. Five targets, Preferred by name ──────────────────────────────────
alter table meta_signal
  add constraint meta_signal_dataset_target_check
  check (dataset_target in ('legacy', 'common', 'common_full', 'preferred', 'preferred_full'));


-- ── 3. Arrival: five Lead rows ──────────────────────────────────────────
--
-- 0123's body with a 'legacy' row added and the Preferred pair renamed.
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
      (new.id, 'Meta', 'Lead', 'legacy',      null),
      (new.id, 'Meta', 'Lead', 'common',      null),
      (new.id, 'Meta', 'Lead', 'common_full', null),
      (new.id, 'Meta', 'Lead', 'preferred',       null),
      (new.id, 'Meta', 'Lead', 'preferred_full',  null)
    on conflict do nothing;
  end if;

  return new;
end;
$$ language plpgsql;

comment on function fn_meta_signal_on_lead_created() is
  'Queues the Lead conversion into all five datasets the moment a Meta lead is downloaded into the CRM. Writes the outbox rows only — the drain sends them.';

-- ── 4. Rule A, in one place ─────────────────────────────────────────────
--
-- A FUNCTION rather than five copies of the same `exists`, so the rule cannot
-- drift between the CompleteRegistration and Purchase branches. STABLE: it
-- reads meta_signal and nothing else.
create or replace function meta_dataset_saw_lead(p_lead_id uuid, p_target text)
returns boolean as $$
  select exists (
    select 1 from meta_signal
    where lead_id = p_lead_id
      and event_name = 'Lead'
      and dataset_target = p_target
  );
$$ language sql stable;

comment on function meta_dataset_saw_lead(uuid, text) is
  'True when the target holds this lead''s Lead row — the condition for sending it the lead''s later CompleteRegistration and Purchase. Checks the row exists, not that Meta received it.';


-- ── 5. Reconciliation ───────────────────────────────────────────────────
--
-- 0130's logic. The differences: 'legacy' gets CompleteRegistration and
-- Purchase unguarded, every other target's insert is wrapped in
-- meta_dataset_saw_lead, and cream is Preferred in literals, variables and
-- comments.
create or replace function ensure_conversion_signals(p_lead_id uuid)
returns void as $$
declare
  v_source                text;
  v_is_qualified          boolean;
  v_policy_type           text;
  v_stage                 text;
  v_premium               numeric;
  v_licensed_5y           boolean;
  v_convictions           int;
  v_at_fault              int;
  v_is_preferred          boolean;
  v_preferred_full_exists boolean;
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
  --
  -- AUTO INSURANCE ('legacy', 0131) IS UNGUARDED, exactly as 'common' was while
  -- it borrowed Auto Insurance's id. The other targets only hear follow-ups for
  -- leads they saw arrive — see meta_dataset_saw_lead.
  if v_is_qualified is true then
    insert into meta_signal (lead_id, platform, event_name, dataset_target, triggered_by)
    values (p_lead_id, 'Meta', 'CompleteRegistration', 'legacy', null)
    on conflict do nothing;

    if meta_dataset_saw_lead(p_lead_id, 'common') then
      insert into meta_signal (lead_id, platform, event_name, dataset_target, triggered_by)
      values (p_lead_id, 'Meta', 'CompleteRegistration', 'common', null)
      on conflict do nothing;
    end if;

    if meta_dataset_saw_lead(p_lead_id, 'common_full') then
      insert into meta_signal (lead_id, platform, event_name, dataset_target, triggered_by)
      values (p_lead_id, 'Meta', 'CompleteRegistration', 'common_full', null)
      on conflict do nothing;
    end if;
  end if;

  -- ── Preferred: four conditions, all of which must hold ────────────────────
  --
  -- Read from the PRINCIPAL driver only — build_user_data reads that row alone,
  -- so a spouse's record is not what Meta would be told about.
  select licensed_5y_plus, convictions_3y, at_fault_claims_6y
    into v_licensed_5y, v_convictions, v_at_fault
  from driver
  where lead_id = p_lead_id and is_principal
  limit 1;

  -- UNKNOWN IS NEVER PREFERRED. A null count means nobody has looked, which is the
  -- opposite of a clean record — `= 0` rather than a falsy test, because null
  -- and zero mean opposite things here. This is why 0114 gave these columns no
  -- defaults. A null at-fault count means "not Preferred YET": when DASH later
  -- writes 0, reconciliation re-runs and the Preferred rows appear.
  --
  -- TENANT IS INCLUDED (0130). 0123 left it out on distinctiveness grounds —
  -- a tenant bundle measures ~82% of a homeowner one by premium, so including
  -- it makes Preferred most of the bundled book. Reversed by business decision:
  -- what makes a customer worth finding is the clean driver on a bundle, and
  -- a renter with a clean record is that customer as much as an owner is.
  v_is_preferred := v_is_qualified is true
            and v_policy_type in ('Auto and Homeowner', 'Auto and Condo', 'Auto and Tenant')
            and v_licensed_5y is true
            and v_convictions = 0
            and v_at_fault = 0;

  if v_is_preferred then
    if meta_dataset_saw_lead(p_lead_id, 'preferred') then
      insert into meta_signal (lead_id, platform, event_name, dataset_target, triggered_by)
      values (p_lead_id, 'Meta', 'CompleteRegistration', 'preferred', null)
      on conflict do nothing;
    end if;

    if meta_dataset_saw_lead(p_lead_id, 'preferred_full') then
      insert into meta_signal (lead_id, platform, event_name, dataset_target, triggered_by)
      values (p_lead_id, 'Meta', 'CompleteRegistration', 'preferred_full', null)
      on conflict do nothing;
    end if;
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
        p_lead_id, 'Meta', 'Purchase', 'legacy', null,
        jsonb_build_object('total_new_premium_at_purchase', v_premium)
      )
      on conflict do nothing;

      if meta_dataset_saw_lead(p_lead_id, 'common_full') then
        insert into meta_signal (lead_id, platform, event_name, dataset_target, triggered_by, payload)
        values (
          p_lead_id, 'Meta', 'Purchase', 'common_full', null,
          jsonb_build_object('total_new_premium_at_purchase', v_premium)
        )
        on conflict do nothing;
      end if;

      -- PREFERRED MEMBERSHIP, ONCE ENTERED, IS NOT REVOKED.
      --
      -- The first clause is the load-bearing one: if this lead already entered
      -- the Preferred funnel, its sale must follow, even if a later CRM edit changed
      -- a fact. Otherwise that dataset holds a conversion with no matching sale,
      -- teaching the model that Preferred leads do not buy — and Meta cannot be told
      -- a conversion was withdrawn.
      --
      -- The second catches a lead whose Preferred facts only completed after it was
      -- qualified, which is the ordinary case given DASH arrives late.
      select exists (
        select 1 from meta_signal
        where lead_id = p_lead_id
          and event_name = 'CompleteRegistration'
          and dataset_target = 'preferred_full'
      ) into v_preferred_full_exists;

      if (v_preferred_full_exists or v_is_preferred)
         and meta_dataset_saw_lead(p_lead_id, 'preferred_full') then
        insert into meta_signal (lead_id, platform, event_name, dataset_target, triggered_by, payload)
        values (
          p_lead_id, 'Meta', 'Purchase', 'preferred_full', null,
          jsonb_build_object('total_new_premium_at_purchase', v_premium)
        )
        on conflict do nothing;
      end if;
    end if;
  end if;
end;
$$ language plpgsql;

comment on function ensure_conversion_signals(uuid) is
  'The single producer of CompleteRegistration and Purchase rows. Idempotent: reads current state and inserts only what is missing. Refuses any lead whose source is not meta. Auto Insurance (legacy) is unguarded; every other target only hears follow-ups for leads it has a Lead row for.';


-- ── 6. The driver trigger, renamed ──────────────────────────────────────
--
-- 0125's function and trigger, unchanged in behaviour: re-run reconciliation
-- when a PRINCIPAL driver's licence, convictions or at-fault count actually
-- changes. Only the names move. The old trigger is dropped before the new one
-- exists, inside this transaction, so no driver update can see neither.
create or replace function fn_reconcile_on_driver_preferred_facts()
returns trigger as $$
begin
  -- THE PRINCIPAL CHECK COMES FIRST, and reads NEW rather than OLD: a driver
  -- promoted to principal with facts already on the row should reconcile, and a
  -- demoted one should not.
  if not new.is_principal then
    return new;
  end if;

  -- `is distinct from` handles the nulls these columns are full of. Without it
  -- a null-to-null rewrite reads as "no change" only by accident, and a
  -- null-to-0 change — the exact moment a DASH report says "no claims" and a
  -- lead becomes Preferred — would read as null and be missed.
  if new.convictions_3y     is distinct from old.convictions_3y
     or new.licensed_5y_plus   is distinct from old.licensed_5y_plus
     or new.at_fault_claims_6y is distinct from old.at_fault_claims_6y
  then
    -- Same transaction as the driver write. A failure rolls the fact back with
    -- it, which is the correct outcome: a driver row whose Preferred facts are
    -- stored but whose conversion was never queued is a silent permanent loss,
    -- while a failed save is a retry the broker can see.
    perform ensure_conversion_signals(new.lead_id);
  end if;

  return new;
end;
$$ language plpgsql;


comment on function fn_reconcile_on_driver_preferred_facts() is
  'Re-runs ensure_conversion_signals when a PRINCIPAL driver''s Preferred facts actually change. Holds no rules of its own — it only says the input changed.';

drop trigger if exists trg_reconcile_on_driver_cream_facts on driver;
drop trigger if exists trg_reconcile_on_driver_preferred_facts on driver;

create trigger trg_reconcile_on_driver_preferred_facts
  after update of convictions_3y, licensed_5y_plus, at_fault_claims_6y
  on driver
  for each row
  execute function fn_reconcile_on_driver_preferred_facts();

drop function if exists fn_reconcile_on_driver_cream_facts();



commit;


-- ── After applying, these should hold ───────────────────────────────────
--
-- No live object says cream any more (all three return 0):
--
--   select count(*) from meta_signal where dataset_target like 'cream%';
--   select count(*) from pg_proc
--    where proname in ('ensure_conversion_signals', 'fn_meta_signal_on_lead_created',
--                      'meta_dataset_saw_lead', 'fn_reconcile_on_driver_preferred_facts')
--      and prosrc ilike '%cream%';
--   select count(*) from pg_trigger where tgname ilike '%cream%'
--      or tgfoid::regproc::text ilike '%cream%';
--
-- The constraint allows exactly the five names:
--
--   select pg_get_constraintdef(oid) from pg_constraint
--    where conname = 'meta_signal_dataset_target_check';
--
-- The relabel moved exactly the rows recorded as Auto Insurance:
--
--   select dataset_target, coalesce(payload->>'dataset_id', '(not recorded)'), count(*)
--   from meta_signal where dataset_target in ('legacy', 'common')
--   group by 1, 2 order by 1, 2;
--     legacy / 3253348891512104 / 209, common / 1047675657670808 / 1,
--     common / (not recorded) / 39     (production, 2026-10-05)
--
-- A NEW Meta lead gets five Lead rows:
--
--   select dataset_target from meta_signal
--   where lead_id = '<new lead>' and event_name = 'Lead' order by 1;
--     common / common_full / legacy / preferred / preferred_full
--
-- Qualifying it adds CompleteRegistration to legacy, common and common_full;
-- qualifying a lead from BEFORE this migration adds it to legacy only.
