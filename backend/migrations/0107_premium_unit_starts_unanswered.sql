-- A PREMIUM UNIT NOBODY PICKED IS NOT AN ANSWER.
--
-- 0101 added current_premium_unit / new_premium_unit as `not null default
-- 'year'`. The default was defensible at the time: every figure stored before
-- that migration was annual by definition (0099 made annual the only stored
-- unit), so the default described the existing rows correctly.
--
-- It stopped being true the moment the unit became a QUESTION PUT TO THE
-- BROKER. A premium row is created as soon as anything on the New Policy
-- screen is saved, so a lead where nobody has reached the premiums yet still
-- had a row asserting 'year' for both sides. The screen read that back and lit
-- the Year button, and the broker saw an answer they never gave.
--
-- Two ways that goes wrong, and both are quiet:
--
--   * A broker quoting MONTHLY has no reason to touch a control that already
--     looks answered. They type into a box labelled per-year and the figure is
--     stored as annual, which is a twelve-times error nobody sees.
--   * Clicking Year to "confirm" it CLEARS the toggle instead, because
--     clicking the active button is how a broker un-picks a wrong unit
--     (UnitToggle). The one click that looks like agreement undoes itself.
--
-- NULL MEANS NOT ASKED YET, which is a state this table already knows how to
-- express: every premium figure is nullable for exactly the same reason. The
-- broker picks Year or Month before a figure can be typed - the boxes are
-- disabled until they do - so nothing can be stored under a unit that was
-- never chosen.
--
-- THE CHECK CONSTRAINT STILL REFUSES ANYTHING ELSE. Dropping `not null` widens
-- the column by exactly one value, the one that means "no answer". 'week' is
-- still rejected.
--
-- ROWS THAT ALREADY HOLD A FIGURE KEEP THEIR UNIT. Only rows where nobody
-- entered a premium on that side are cleared, because only those are claiming
-- an answer to a question nobody was asked. A row with money in it was typed
-- under a unit that the trigger from 0106 has already used to derive its pair,
-- and rewriting it would strand a real figure with no reading.

begin;

-- ============================================================================
-- 1. Let the question go unanswered
-- ============================================================================

alter table premium_history
  alter column current_premium_unit drop not null,
  alter column current_premium_unit drop default,
  alter column new_premium_unit drop not null,
  alter column new_premium_unit drop default;

-- ============================================================================
-- 2. Clear the units nobody picked
--
-- ONLY WHERE THE SIDE IS EMPTY. Both readings of all three products null means
-- nothing was ever entered on that side, so its unit is the 0101 default
-- speaking rather than the broker. A side with any figure keeps its unit: that
-- figure was typed under it, and 0106's trigger already derived the other half
-- of the pair from it.
--
-- Checks BOTH halves of each pair. Checking only the annual columns would miss
-- a monthly-entered row and wrongly clear a unit that is load-bearing.
-- ============================================================================

update premium_history
set current_premium_unit = null
where current_auto_premium_annual is null
  and current_auto_premium_monthly is null
  and current_homeowner_premium_annual is null
  and current_homeowner_premium_monthly is null
  and current_tenant_premium_annual is null
  and current_tenant_premium_monthly is null;

update premium_history
set new_premium_unit = null
where new_auto_premium_annual is null
  and new_auto_premium_monthly is null
  and new_homeowner_premium_annual is null
  and new_homeowner_premium_monthly is null
  and new_tenant_premium_annual is null
  and new_tenant_premium_monthly is null;

-- ============================================================================
-- 3. Teach the derive trigger that a null unit derives nothing
--
-- Rebuilt from 0106 with one change: each side is skipped entirely when its
-- unit is null. The previous version's `if unit = 'month' ... else ... end if`
-- treated null as the else branch and would divide an annual figure that does
-- not exist yet - harmless in arithmetic (null / 12 is null) but it would
-- write nulls over nulls on every save and hide the real question, which is
-- whether a figure can be stored at all before a unit is chosen. It cannot:
-- with no unit there is no column to put it in.
--
-- The change-aware guards are unchanged and still load-bearing - see 0106 for
-- why re-deriving on a bare unit switch invents money.
-- ============================================================================

create or replace function premium_history_derive_units()
returns trigger as $$
begin
  if new.current_premium_unit = 'month' then
    if tg_op = 'INSERT' or (
         new.current_auto_premium_monthly is distinct from old.current_auto_premium_monthly
      or new.current_homeowner_premium_monthly is distinct from old.current_homeowner_premium_monthly
      or new.current_tenant_premium_monthly is distinct from old.current_tenant_premium_monthly
    ) then
      new.current_auto_premium_annual := new.current_auto_premium_monthly * 12;
      new.current_homeowner_premium_annual := new.current_homeowner_premium_monthly * 12;
      new.current_tenant_premium_annual := new.current_tenant_premium_monthly * 12;
    end if;
  elsif new.current_premium_unit = 'year' then
    if tg_op = 'INSERT' or (
         new.current_auto_premium_annual is distinct from old.current_auto_premium_annual
      or new.current_homeowner_premium_annual is distinct from old.current_homeowner_premium_annual
      or new.current_tenant_premium_annual is distinct from old.current_tenant_premium_annual
    ) then
      new.current_auto_premium_monthly := round(new.current_auto_premium_annual / 12);
      new.current_homeowner_premium_monthly := round(new.current_homeowner_premium_annual / 12);
      new.current_tenant_premium_monthly := round(new.current_tenant_premium_annual / 12);
    end if;
  end if;

  if new.new_premium_unit = 'month' then
    if tg_op = 'INSERT' or (
         new.new_auto_premium_monthly is distinct from old.new_auto_premium_monthly
      or new.new_homeowner_premium_monthly is distinct from old.new_homeowner_premium_monthly
      or new.new_tenant_premium_monthly is distinct from old.new_tenant_premium_monthly
    ) then
      new.new_auto_premium_annual := new.new_auto_premium_monthly * 12;
      new.new_homeowner_premium_annual := new.new_homeowner_premium_monthly * 12;
      new.new_tenant_premium_annual := new.new_tenant_premium_monthly * 12;
    end if;
  elsif new.new_premium_unit = 'year' then
    if tg_op = 'INSERT' or (
         new.new_auto_premium_annual is distinct from old.new_auto_premium_annual
      or new.new_homeowner_premium_annual is distinct from old.new_homeowner_premium_annual
      or new.new_tenant_premium_annual is distinct from old.new_tenant_premium_annual
    ) then
      new.new_auto_premium_monthly := round(new.new_auto_premium_annual / 12);
      new.new_homeowner_premium_monthly := round(new.new_homeowner_premium_annual / 12);
      new.new_tenant_premium_monthly := round(new.new_tenant_premium_annual / 12);
    end if;
  end if;

  return new;
end;
$$ language plpgsql;

-- ============================================================================
-- 4. Say what null means
-- ============================================================================

comment on column premium_history.current_premium_unit is
  'Which box the broker typed the three current_* figures into, or NULL when they have not chosen yet (0107). Null is why the Year/Month toggle can open with neither side lit - a default of year was an answer nobody gave, and a broker quoting monthly had no reason to correct it.';

comment on column premium_history.new_premium_unit is
  'Which box the broker typed the three new_* figures into, or NULL when they have not chosen yet. See current_premium_unit (0107).';

commit;
