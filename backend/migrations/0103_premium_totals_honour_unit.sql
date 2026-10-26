-- THE TOTALS LEARN WHAT UNIT THE FIGURES ARE IN, and every one exists in both
-- units so nothing outside the database ever divides or multiplies a premium.
--
-- THE BUG. The six product columns are named *_premium_annual but hold
-- WHATEVER THE BROKER TYPED — monthly or yearly, with current_premium_unit /
-- new_premium_unit (0101) saying which. The three generated totals summed them
-- with no reference to the unit, so a monthly figure was added as though it
-- were a yearly one. Live, before this migration:
--
--     Murali      390 + 90 a month  ->  total said 480, should be 5,760
--     d654e369    260 + 75 a month  ->  total said 335, should be 4,020
--     f1df4cab    240      a month  ->  total said 260, should be 2,880
--
-- WHY POSTGRES AND NOT PYTHON. Five attempts to fix this in application code
-- each shipped a defect: one divided stored figures for display, one
-- multiplied by twelve TWICE (3650 -> 43800 -> 525600), one compared a typed
-- value against an already-updated draft and silently saved nothing. A
-- generated column cannot drift — it is recomputed on every write, so there is
-- no ordering to get wrong, no retry that multiplies twice, and no frontend
-- copy to keep in step. The UI stops calculating and only displays.
--
-- SIX DERIVED COLUMNS: the two Combined figures and the saving, each in both
-- units. A broker quoting in Month wants the Combined line in months; the
-- business measures the year; Meta receives the year. All three readings exist
-- as columns rather than being divided at three different call sites.
--
-- PER-PRODUCT COLUMNS WERE CONSIDERED AND DROPPED. An earlier draft added a
-- monthly and an annual for each of the six products too — twenty premium
-- columns in all. A per-product annual is just the typed figure and the unit
-- beside it, and current_auto_annual sitting next to
-- current_auto_premium_annual would be two similar names meaning different
-- things: the exact confusion that caused this bug.
--
-- THE TYPED COLUMNS KEEP THEIR NAMES, even though *_premium_annual is now a
-- slight misnomer for a column that may hold a monthly figure. They are
-- referenced 134 times across the backend, the schemas and the frontend
-- (premiumApiField builds the name at runtime), and renaming them to fix a
-- word is not worth that blast radius. The unit column beside them says how to
-- read one.
--
-- total_new_premium_annual KEEPS ITS NAME for a stronger reason: it is what
-- fn_meta_signal_on_stage_history reads for Meta's conversion value (0100) and
-- what My Day sums for premium written. Renaming it would mean editing a
-- trigger to fix arithmetic. It simply starts returning the right number.
--
-- MONTHLY COLUMNS ARE ROUNDED. Dividing an annual figure by twelve is the only
-- place a fraction could appear, and premiums are quoted in whole dollars —
-- all 26 live rows hold whole numbers today.

begin;

-- Dropped and re-added because a generated expression cannot be altered in
-- place. Nothing is lost: they are derived, and Postgres recomputes every row
-- as it creates them.
alter table premium_history
  drop column total_current_premium_annual,
  drop column total_new_premium_annual,
  drop column savings_annual;

-- ── The two Combined totals, both ways ──────────────────────────────────
alter table premium_history
  add column total_current_premium_annual numeric generated always as (
    (coalesce(current_auto_premium_annual, 0)
     + coalesce(current_homeowner_premium_annual, 0)
     + coalesce(current_tenant_premium_annual, 0))
    * case when current_premium_unit = 'month' then 12 else 1 end
  ) stored,
  add column total_current_premium_monthly numeric generated always as (
    round((coalesce(current_auto_premium_annual, 0)
     + coalesce(current_homeowner_premium_annual, 0)
     + coalesce(current_tenant_premium_annual, 0))
    * case when current_premium_unit = 'month' then 12 else 1 end / 12)
  ) stored,

  add column total_new_premium_annual numeric generated always as (
    (coalesce(new_auto_premium_annual, 0)
     + coalesce(new_homeowner_premium_annual, 0)
     + coalesce(new_tenant_premium_annual, 0))
    * case when new_premium_unit = 'month' then 12 else 1 end
  ) stored,
  add column total_new_premium_monthly numeric generated always as (
    round((coalesce(new_auto_premium_annual, 0)
     + coalesce(new_homeowner_premium_annual, 0)
     + coalesce(new_tenant_premium_annual, 0))
    * case when new_premium_unit = 'month' then 12 else 1 end / 12)
  ) stored;

-- ── What the customer saves, both ways ──────────────────────────────────
--
-- EACH SIDE CARRIES ITS OWN MULTIPLIER. A broker can have Current in months
-- and Proposed in years, and the difference between them means nothing until
-- both are annual — subtracting a monthly figure from a yearly one is the
-- original bug in miniature.
--
-- Negative is a real outcome, not an error: the proposed policy sometimes
-- costs more, and the broker has to see that before the customer does.
alter table premium_history
  add column savings_annual numeric generated always as (
    (coalesce(current_auto_premium_annual, 0)
     + coalesce(current_homeowner_premium_annual, 0)
     + coalesce(current_tenant_premium_annual, 0))
    * case when current_premium_unit = 'month' then 12 else 1 end
    - (coalesce(new_auto_premium_annual, 0)
       + coalesce(new_homeowner_premium_annual, 0)
       + coalesce(new_tenant_premium_annual, 0))
    * case when new_premium_unit = 'month' then 12 else 1 end
  ) stored,
  add column savings_monthly numeric generated always as (
    round(
      ((coalesce(current_auto_premium_annual, 0)
        + coalesce(current_homeowner_premium_annual, 0)
        + coalesce(current_tenant_premium_annual, 0))
       * case when current_premium_unit = 'month' then 12 else 1 end
       - (coalesce(new_auto_premium_annual, 0)
          + coalesce(new_homeowner_premium_annual, 0)
          + coalesce(new_tenant_premium_annual, 0))
       * case when new_premium_unit = 'month' then 12 else 1 end) / 12
    )
  ) stored;

comment on column premium_history.total_new_premium_annual is
  'What the Meta CompleteRegistration event carries as its value. Generated from the three new-business columns and new_premium_unit, so a monthly quote is multiplied by twelve before it leaves.';

commit;
