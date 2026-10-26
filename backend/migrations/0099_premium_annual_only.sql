-- Annual becomes the only premium unit. Monthly leaves the database.
--
-- WHY. 0067 stored MONTHLY and generated annual as monthly * 12. That is
-- backwards for every consumer this table has:
--
--   * the Meta Purchase event carries total_new_premium_annual (0078)
--   * services/commission.py multiplies the annual columns
--   * an insurer quoting "the premium" means the annual one
--
-- and it destroys the figure a broker types. $6,950 a year stored as monthly
-- is 579.1666..., and 579 * 12 is 6,948 — the number does not survive the
-- round trip. Storing what was entered and deriving the other direction makes
-- the typed value exact and the displayed one cosmetic.
--
-- IT ALSO CAUSED A REAL WRONG NUMBER. The form's boxes are labelled $/mo and
-- nothing enforced it, so a broker reading an annual premium off a quote typed
-- it straight in. Lead Ranjith carries new_auto_premium_monthly = 6924, stored
-- as $83,088 a year. Nothing flagged it, and total_new_premium_annual is what
-- Meta would have received as the value of that sale.
--
-- WHY NOW. 12 rows, 11 with any premium, and not one Purchase event has ever
-- been sent — Purchase fires at Sold and no lead has reached it. The
-- conversion is monthly * 12, which is exactly what the generated columns
-- already hold, so no stored value changes meaning. This is the cheapest this
-- will ever be.
--
-- WHAT REPLACES IT. Eighteen premium columns become nine: six stored amounts
-- and three generated summaries. The monthly totals and monthly savings go
-- entirely — a UI that wants a monthly figure divides by twelve for display,
-- which is not a fact worth storing twice.
--
-- ONE TRANSACTION. A failure part-way through would leave the table with some
-- columns converted and others not, which no migration and no application
-- could then read.

begin;

-- ── 1. The six summaries first ──────────────────────────────────────────
-- They read the monthly columns, so nothing else can be dropped while they
-- exist. The three annual ones are recreated at the end against the new
-- source columns; the three monthly ones are not coming back.
alter table premium_history
  drop column total_current_premium_monthly,
  drop column total_new_premium_monthly,
  drop column total_current_premium_annual,
  drop column total_new_premium_annual,
  drop column savings_monthly,
  drop column savings_annual;


-- ── 2. The annual products become ordinary stored columns ───────────────
-- DROP EXPRESSION keeps every value exactly as it stands and simply stops
-- Postgres recomputing it. No temporary column, no copy, no rename — the
-- rows are untouched by this statement.
--
-- Requires Postgres 13+. Supabase runs 15+.
alter table premium_history
  alter column current_auto_premium_annual      drop expression,
  alter column new_auto_premium_annual          drop expression,
  alter column current_homeowner_premium_annual drop expression,
  alter column new_homeowner_premium_annual     drop expression,
  alter column current_tenant_premium_annual    drop expression,
  alter column new_tenant_premium_annual        drop expression;


-- ── 3. Monthly leaves ───────────────────────────────────────────────────
alter table premium_history
  drop column current_auto_premium_monthly,
  drop column new_auto_premium_monthly,
  drop column current_homeowner_premium_monthly,
  drop column new_homeowner_premium_monthly,
  drop column current_tenant_premium_monthly,
  drop column new_tenant_premium_monthly;


-- ── 4. Three summaries, from the six stored amounts ─────────────────────
-- Generated, for the reason 0067 gives: the inputs are immutable per row, so
-- a generated total is exactly as stable as a stored one and cannot drift
-- from its own parts. coalesce, because a lead quoting auto only leaves the
-- property columns null and null would swallow the whole sum.
alter table premium_history
  add column total_current_premium_annual numeric
    generated always as (
      coalesce(current_auto_premium_annual, 0)
      + coalesce(current_homeowner_premium_annual, 0)
      + coalesce(current_tenant_premium_annual, 0)
    ) stored,

  -- THE NUMBER META RECEIVES as a Purchase event's value, and the one
  -- commission multiplies.
  add column total_new_premium_annual numeric
    generated always as (
      coalesce(new_auto_premium_annual, 0)
      + coalesce(new_homeowner_premium_annual, 0)
      + coalesce(new_tenant_premium_annual, 0)
    ) stored,

  -- The customer-facing "how much we saved you". Negative when the proposed
  -- policy costs more, which is a real outcome and not an error.
  add column savings_annual numeric
    generated always as (
      (coalesce(current_auto_premium_annual, 0)
       + coalesce(current_homeowner_premium_annual, 0)
       + coalesce(current_tenant_premium_annual, 0))
      - (coalesce(new_auto_premium_annual, 0)
         + coalesce(new_homeowner_premium_annual, 0)
         + coalesce(new_tenant_premium_annual, 0))
    ) stored;

comment on column premium_history.total_new_premium_annual is
  'What the Meta Purchase event carries as its value, and what commission is calculated from. Generated from the three new-business columns.';

commit;
