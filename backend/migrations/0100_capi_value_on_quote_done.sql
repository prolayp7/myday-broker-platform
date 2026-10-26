-- The value moves from Purchase to CompleteRegistration, and Purchase stops
-- existing.
--
-- WHY. Purchase fired at Sold and carried the annual proposed premium. It has
-- NEVER FIRED — not once. Every row this table has ever held:
--
--     Lead                  Sent   50
--     CompleteRegistration  Sent    5
--     Purchase                      0
--
-- and no lead has ever reached Sold (81 leads: New 57, Lost 17, Pending 6,
-- Quote Done 1). So Meta has received identity and nothing else, while 14
-- premium rows sat in the database with a real proposed total on 6 of them.
-- The number existed the whole time; no event carried it.
--
-- Sold is also weeks of insurer paperwork away from the moment the broker
-- knows the figure. Quote Done is when the quote exists — earlier, and far
-- more frequent, so Meta's model gets more signal and gets it sooner.
--
-- AFTER THIS:
--     lead arrives              -> Lead                 (no value)
--     Quote Done, qualified     -> CompleteRegistration (value + CAD)
--     Sold/Submitted/Issued     -> nothing
--
-- THE CRM IS UNCHANGED. All seven stages stay exactly as they are, with their
-- sub-stages, rail, counts and history. The one behaviour change is in Python,
-- not here: Quote Done now requires the proposed premium
-- (services/lead.py::_STAGES_NEEDING_PREMIUM), which runs BEFORE the stage
-- write and therefore before this trigger — so a lead with no premium is
-- refused and no signal row is ever created. That is what makes a valueless
-- CompleteRegistration impossible.
--
-- ─────────────────────────────────────────────────────────────────────────
-- BUILT FROM 0078, WHICH IS THE CURRENT DEFINITION.
--
-- The chain is 0057 -> 0067 -> 0075 -> 0078. 0075's header records why this
-- matters: its first draft copied 0057 verbatim and silently reverted 0067's
-- total_new_premium -> total_new_premium_annual rename, breaking Sold. Diffed
-- against 0078 before applying. The ONLY differences are:
--
--   * the guard is `<> 'Quote Done'` instead of `not in ('Quote Done','Sold')`
--   * the two branches collapse to one insert — the premium select moves
--     verbatim off the deleted Sold branch onto CompleteRegistration
--   * the payload key is renamed (see below)
--
-- The source gate, the `is true` qualification gate, and
-- `on conflict do nothing` are byte-identical to 0078.
--
-- fn_meta_signal_on_lead_created (0096) is NOT touched. Lead still fires on
-- arrival, before anyone has looked at the lead — that is the negative signal
-- Meta needs to weigh the positive ones against, and it carries no value.

create or replace function fn_meta_signal_on_stage_history()
returns trigger as $$
declare
  v_platform text;
  v_is_qualified boolean;
  v_total_new_premium numeric;
begin
  -- Quote Done is now the ONLY stage that signals anything. Sold, Submitted
  -- and Issued remain real CRM stages; they simply tell Meta nothing.
  if new.to_stage <> 'Quote Done' then
    return new;
  end if;

  select source, is_qualified
  into v_platform, v_is_qualified
  from lead where id = new.lead_id;

  if v_platform <> 'meta' then
    return new;
  end if;

  if v_is_qualified is not true then
    return new;
  end if;

  -- One premium row per lead (idx_premium_history_lead_id_unique), which is
  -- why there is no order by — 0067 removed it for that reason.
  select total_new_premium_annual into v_total_new_premium
  from premium_history
  where lead_id = new.lead_id
  limit 1;

  -- RENAMED from total_new_premium_at_sale. There is no sale here to be "at",
  -- and with zero Purchase rows and zero unsent old payloads there is nothing
  -- to stay compatible with — so the inaccurate name goes rather than being
  -- carried forever. services/meta_signal.py reads this exact key.
  --
  -- Snapshotted AT THIS MOMENT and never revisited: event_id is what Meta
  -- deduplicates on, so a premium corrected after this row exists sends
  -- nothing and changes nothing. What was true at Quote Done is what Meta is
  -- told.
  insert into meta_signal (lead_id, platform, event_name, triggered_by, payload)
  values (
    new.lead_id, 'Meta', 'CompleteRegistration', new.broker_id,
    jsonb_build_object('total_new_premium_at_registration', v_total_new_premium)
  )
  on conflict do nothing;

  return new;
end;
$$ language plpgsql;


-- ── Purchase is no longer a value this column may hold ──────────────────
--
-- Nothing is lost: no row has ever used it. The point is that bringing
-- Purchase back should now COST A MIGRATION, rather than being one string
-- away in application code.
--
-- The constraint is UNNAMED in 0056 (declared inline on the column), so its
-- name was auto-generated. Resolved from the catalog rather than hardcoded —
-- a wrong DROP CONSTRAINT would abort this whole migration.
do $$
declare
  v_name text;
begin
  select conname into v_name
  from pg_constraint
  where conrelid = 'meta_signal'::regclass
    and contype = 'c'
    and pg_get_constraintdef(oid) like '%event_name%';

  if v_name is not null then
    execute format('alter table meta_signal drop constraint %I', v_name);
  end if;
end $$;

-- Explicitly named this time, so any future migration can rely on it.
alter table meta_signal
  add constraint meta_signal_event_name_check
  check (event_name in ('Lead', 'CompleteRegistration'));


-- ── The live column comment 0099 installed is now false ─────────────────
--
-- This is real schema state, not a code comment — it shows in \d+ and in
-- every schema browser, and would go on telling the next reader that a
-- Purchase event carries this number.
comment on column premium_history.total_new_premium_annual is
  'What the Meta CompleteRegistration event carries as its value, and what commission is calculated from. Generated from the three new-business columns.';
