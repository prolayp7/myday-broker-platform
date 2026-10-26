-- One Meta dataset: Auto Insurance receives all three signals.
--
-- WHAT THIS UNDOES. 0123 and 0131 fanned every conversion out to five datasets
-- (Auto Insurance = 'legacy', Common, Common Full Funnel, Preferred, Preferred
-- Full Funnel), each with its own rule: a "Preferred" shape read from the
-- principal driver, full-funnel twins that alone received Purchase, and Rule A
-- (meta_dataset_saw_lead) so a new dataset only heard about leads it saw arrive.
--
-- WHY. Meta's Conversion Leads optimisation wants roughly 200 leads a month
-- into ONE dataset, with a steady daily flow of CRM events. Splitting a book of
-- this size five ways keeps every dataset under that line, so none of them can
-- ever be optimised on — the opposite of the point. Decided by the owner on
-- 2026-10-24: one dataset, Auto Insurance (3253348891512104, the one the ads'
-- CRM funnel is set up on and that holds all the history), three signals:
--
--   Lead                  a Meta lead arrives in the CRM
--   CompleteRegistration  the broker marks the lead qualified
--   Purchase              Sold / Submitted / Issued with a premium; value is
--                         the YEARLY premium (total_new_premium_annual), CAD
--
-- NOTHING IN FLIGHT IS LOST. Production on 2026-10-24: 252 rows, every one
-- Sent — 212 'legacy', 40 'common' (August history) — and none at all in
-- common_full, preferred or preferred_full. No Meta lead has arrived since the
-- 10-05 switch.
--
-- 'legacy' KEEPS ITS NAME. It is Auto Insurance and now the only live label;
-- renaming it would rewrite every historical row for no change in behaviour.
--
-- THE CONSTRAINT IS LEFT AS 0131 MADE IT, all five labels. The other four are
-- history now — production's 40 August 'common' rows, and staging's Sent test
-- rows in every target — and narrowing the check would fail on those rows
-- (Postgres validates existing data when a check is added). Nothing writes
-- them again: both functions below write 'legacy', the column default becomes
-- 'legacy', and the sender only ever picks up 'legacy' rows
-- (repositories/meta_signal.py::list_pending), so a leftover or retried row
-- from the old setup can never reach Auto Insurance.
--
-- WHAT STAYS. The source gate, the qualification gate, the Purchase premium
-- snapshot, and the driver columns licensed_5y_plus / convictions_3y /
-- at_fault_claims_6y — the qualify popup and DASH still use them. Only their
-- part in choosing a dataset goes, and with it the trigger that re-ran
-- reconciliation when they changed.
--
-- ROLLOUT: APPLY THIS BEFORE THE CODE, though either order is safe. The old
-- code under this migration already maps 'legacy' to META_CAPI_DATASET_ID and
-- finds nothing queued for the other targets. The new code under the old
-- functions would send only the 'legacy' copy of each fan-out and leave the
-- rest Pending.
--
-- SAFE TO RE-RUN. APPLY BY HAND in the Supabase SQL editor, staging first.

begin;


-- ── 1. The never-sent queue for the retired targets ──────────────────────
--
-- Pending only. Sent and Failed rows are history and are never touched.
delete from meta_signal
 where dataset_target in ('common', 'common_full', 'preferred', 'preferred_full')
   and status = 'Pending';

-- A Purchase queued by the older SQL with no premium above zero. 0131 tested
-- the premium only for null, and total_new_premium_annual sums with coalesce,
-- so an empty premium row queued a Purchase worth 0. Never sent, never valid:
-- removed here, and the sender refuses any that slip through
-- (services/meta_signal.py::_send_one).
delete from meta_signal
 where event_name = 'Purchase'
   and status = 'Pending'
   and coalesce((payload->>'total_new_premium_at_purchase')::numeric, 0) <= 0;


-- ── 2. New rows default to Auto Insurance ───────────────────────────────
alter table meta_signal
  alter column dataset_target set default 'legacy';


-- ── 3. Arrival: one Lead row ────────────────────────────────────────────
create or replace function fn_meta_signal_on_lead_created()
returns trigger as $$
begin
  -- new.source directly: the row being inserted IS the lead. Manual, website
  -- and tiktok leads create nothing; ensure_conversion_signals applies the
  -- same gate to every later event.
  if new.source = 'meta' then
    insert into meta_signal (lead_id, platform, event_name, dataset_target, triggered_by)
    values (new.id, 'Meta', 'Lead', 'legacy', null)
    on conflict do nothing;
  end if;

  return new;
end;
$$ language plpgsql;

comment on function fn_meta_signal_on_lead_created() is
  'Queues the Lead conversion the moment a Meta lead is downloaded into the CRM. Writes the outbox row only — the drain sends it.';


-- ── 4. Reconciliation: qualified, then sold ─────────────────────────────
--
-- Still the single producer of CompleteRegistration and Purchase, still
-- idempotent: it reads current state and inserts only what is missing, so its
-- callers (set_lead_qualification, update_lead_stage) can call it freely.
create or replace function ensure_conversion_signals(p_lead_id uuid)
returns void as $$
declare
  v_source       text;
  v_is_qualified boolean;
  v_stage        text;
  v_premium      numeric;
begin
  select source, is_qualified, stage
    into v_source, v_is_qualified, v_stage
  from lead
  where id = p_lead_id;

  -- THE SOURCE GATE, FIRST AND UNCONDITIONAL. Most of all for Purchase, which
  -- has no qualification gate: without this a manual walk-in moved to Sold
  -- would tell Meta an advertisement produced that customer.
  if v_source is distinct from 'meta' then
    return;
  end if;

  -- CompleteRegistration: the broker's answer, and nothing else. `is true`
  -- because the column is three-state and null means nobody has been asked.
  if v_is_qualified is true then
    insert into meta_signal (lead_id, platform, event_name, dataset_target, triggered_by)
    values (p_lead_id, 'Meta', 'CompleteRegistration', 'legacy', null)
    on conflict do nothing;
  end if;

  -- Purchase: a state test over all three sale stages, because stages are
  -- free-form and a lead can reach Issued without touching Sold. No
  -- qualification gate: money is a fact, not a judgement.
  if v_stage in ('Sold', 'Submitted', 'Issued') then
    -- THE YEARLY PREMIUM. total_new_premium_annual is generated by the
    -- database from whatever unit the broker typed in (0103/0106), so a
    -- monthly entry still reaches Meta as its annual figure. One premium row
    -- per lead (idx_premium_history_lead_id_unique), hence no order by.
    select total_new_premium_annual into v_premium
    from premium_history
    where lead_id = p_lead_id
    limit 1;

    -- Snapshotted here and never revisited: Meta deduplicates on the event id,
    -- so a sale cannot be amended once queued.
    --
    -- NO PREMIUM, NO PURCHASE, and "no premium" includes zero:
    -- total_new_premium_annual adds its parts with coalesce(…, 0) (0106), so
    -- an empty premium row reads 0, not null. A Purchase worth $0 would tell
    -- Meta the sale was worthless. The API already refuses a sale stage
    -- without premiums; this holds for every other path too.
    if v_premium > 0 then
      insert into meta_signal (lead_id, platform, event_name, dataset_target, triggered_by, payload)
      values (
        p_lead_id, 'Meta', 'Purchase', 'legacy', null,
        jsonb_build_object('total_new_premium_at_purchase', v_premium)
      )
      on conflict do nothing;
    end if;
  end if;
end;
$$ language plpgsql;

comment on function ensure_conversion_signals(uuid) is
  'The single producer of CompleteRegistration and Purchase rows, all to Auto Insurance (legacy). Idempotent: reads current state and inserts only what is missing. Refuses any lead whose source is not meta.';


-- ── 5. The multi-dataset machinery goes ─────────────────────────────────
--
-- Rule A's helper, and the driver trigger that existed only because the
-- driver's facts decided Preferred. The columns it watched stay.
drop function if exists meta_dataset_saw_lead(uuid, text);

drop trigger if exists trg_reconcile_on_driver_preferred_facts on driver;
drop function if exists fn_reconcile_on_driver_preferred_facts();


commit;


-- ── After applying, these should hold ───────────────────────────────────
--
-- New rows default to legacy:
--
--   select column_default from information_schema.columns
--    where table_name = 'meta_signal' and column_name = 'dataset_target';
--
-- The retired pieces are gone (both return 0):
--
--   select count(*) from pg_proc
--    where proname in ('meta_dataset_saw_lead', 'fn_reconcile_on_driver_preferred_facts');
--   select count(*) from pg_trigger where tgname = 'trg_reconcile_on_driver_preferred_facts';
--
-- No live function mentions a retired target (returns 0):
--
--   select count(*) from pg_proc
--    where proname in ('ensure_conversion_signals', 'fn_meta_signal_on_lead_created')
--      and (prosrc like '%common%' or prosrc like '%preferred%');
--
-- Nothing Pending outside legacy, and no Pending Purchase worth 0 (both 0),
-- and history is untouched:
--
--   select count(*) from meta_signal where status = 'Pending' and dataset_target <> 'legacy';
--   select count(*) from meta_signal where status = 'Pending' and event_name = 'Purchase'
--      and coalesce((payload->>'total_new_premium_at_purchase')::numeric, 0) <= 0;
--
--   select dataset_target, event_name, status, count(*)
--   from meta_signal group by 1, 2, 3 order by 1, 2, 3;
--
-- A NEW Meta lead gets exactly one Lead row, labelled legacy; qualifying it adds
-- one CompleteRegistration; selling it with a premium adds one Purchase whose
-- payload holds the annual premium; selling it with an empty premium row adds
-- nothing.
