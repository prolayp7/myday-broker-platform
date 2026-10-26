-- A clean driver on an auto and tenant bundle is Preferred too.
--
-- THE ONE CHANGE. Cream ("Preferred" in Meta) required an Auto and Homeowner or
-- Auto and Condo policy. Auto and Tenant now counts as well. The other four
-- conditions are untouched: the broker's Qualified answer, licensed five years
-- or more, no convictions in three years, no at-fault claims in six — all read
-- from the principal driver, and unknown is still never cream.
--
-- THE BODY IS 0123's, BYTE FOR BYTE, apart from the policy-type line and the
-- comment above it. 0123 is still the current definition — 0124 to 0129 call
-- ensure_conversion_signals but none redefines it — so copying from anywhere
-- else would silently revert something. Diffed against 0123: those two are the
-- only differences.
--
-- NO BACKFILL. A tenant lead that is already qualified with clean facts picks
-- up its cream rows the next time anything re-runs reconciliation: a
-- qualification answer, a change to one of the principal driver's three facts,
-- a policy-type switch, or a move into a sale stage. Every one of those already
-- calls this function, which is the point of it reading current state.
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.
-- Staging first.

begin;


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
  -- TENANT IS INCLUDED (0130). 0123 left it out on distinctiveness grounds —
  -- a tenant bundle measures ~82% of a homeowner one by premium, so including
  -- it makes cream most of the bundled book. Reversed by business decision:
  -- what makes a customer worth finding is the clean driver on a bundle, and
  -- a renter with a clean record is that customer as much as an owner is.
  v_is_cream := v_is_qualified is true
            and v_policy_type in ('Auto and Homeowner', 'Auto and Condo', 'Auto and Tenant')
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


commit;


-- ── After applying, these should hold ───────────────────────────────────
--
-- The deployed body carries the new list:
--
--   select prosrc like '%Auto and Tenant%'
--   from pg_proc where proname = 'ensure_conversion_signals';      -- true
--
-- A qualified Meta lead on Auto and Tenant whose principal driver is licensed
-- 5+ years with 0 convictions and 0 at-fault claims gets all four
-- CompleteRegistration rows:
--
--   select dataset_target from meta_signal
--   where lead_id = '<that lead>' and event_name = 'CompleteRegistration'
--   order by dataset_target;
--     common / common_full / cream / cream_full
--
-- An Auto-only lead with the same facts still gets common and common_full only.
