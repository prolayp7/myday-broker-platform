-- THE COLUMN A FIGURE LANDS IN NOW SAYS WHAT IT MEANS.
--
-- Until this migration the six typed columns were named *_premium_annual and
-- held WHATEVER THE BROKER TYPED - monthly or yearly - with
-- current_premium_unit / new_premium_unit (0101) saying which. Every reader
-- downstream had to consult that flag to know what a number meant, and the
-- rule "multiply by twelve when the unit says month" ended up hand-written in
-- four separate places.
--
-- One of the four forgot it. services/commission.py reads the three new_*
-- columns raw, so a monthly-entered policy would have projected a TWELFTH of
-- the commission actually earned. It has no production call site yet, which is
-- the only reason nobody has been underpaid.
--
-- The screen bugs came from the same root. A monthly quote reopened showing
-- the annual reading ($320/mo displayed as $320/yr, which looks exactly like
-- the total was divided by twelve), and the Issued dialog still prints the
-- typed figure labelled "annual" whatever the unit says.
--
-- 0103's header records FIVE earlier attempts to fix premium arithmetic, each
-- of which shipped a new defect. This table has already been reshaped three
-- times - 0067 (typed monthly, annual generated), 0099 (inverted to
-- annual-only), 0101/0103 (unit flag bolted on). This is the fourth and is
-- meant to be the last, because it removes the thing that caused all of them:
-- a column whose name contradicts its contents.
--
-- WHAT CHANGES. Six *_premium_monthly columns are added beside the existing
-- six. Nothing is renamed. The broker picks Month, types 320, and 320 is
-- stored in current_auto_premium_monthly; Postgres derives
-- current_auto_premium_annual = 3840. Pick Year and it works the other way.
-- Both readings always exist, and the *_annual columns finally hold annual
-- figures - which is why total_new_premium_annual keeps its name AND its
-- meaning, and the Meta CAPI trigger (0100), My Day and the commission
-- projection all keep working untouched while silently becoming correct.
--
-- THREE PRODUCTS, NOT FOUR. Condo shares the homeowner column because it earns
-- the homeowner rate (domain/vehicle.ts PREMIUM_FIELDS_FOR_POLICY,
-- schemas/lead.py PRODUCTS_FOR). So this adds six columns, not eight.
--
-- A TRIGGER, NOT A GENERATED COLUMN. Which half of each pair is typed and
-- which is derived depends on the unit, and a generated column cannot be
-- conditionally generated. The trigger runs BEFORE INSERT OR UPDATE inside the
-- same statement, so the pair can never be seen disagreeing. The application
-- still sends only the typed figure - the UI displays, Postgres calculates.
--
-- THE UNIT FLAG STAYS, with a smaller job: it records which box the broker
-- typed in, so the form reopens on the right one and the trigger knows which
-- way to derive. It no longer reinterprets a stored number.

begin;

-- ============================================================================
-- 1. The six new columns
--
-- Plain numeric and nullable, exactly like the annual six. Not generated: the
-- trigger below fills whichever half of the pair the broker did not type, and
-- a generated column cannot change direction per row.
-- ============================================================================

alter table premium_history
  add column if not exists current_auto_premium_monthly numeric,
  add column if not exists current_homeowner_premium_monthly numeric,
  add column if not exists current_tenant_premium_monthly numeric,
  add column if not exists new_auto_premium_monthly numeric,
  add column if not exists new_homeowner_premium_monthly numeric,
  add column if not exists new_tenant_premium_monthly numeric;

-- ============================================================================
-- 2. Backfill, strictly by the unit each row RECORDS
--
-- Nothing is guessed at. A row flagged 'month' holds a monthly figure in the
-- annual column, so the figure moves across and the annual column is
-- multiplied. A row flagged 'year' already holds an annual figure, so only the
-- monthly reading has to be derived.
--
-- ROWS THE OLD BUG FLIPPED STAY WRONG, deliberately. Before ba3b650, editing
-- any premium box could overwrite a broker's saved 'month' with 'year'. Those
-- rows now read as annual figures because that is what they claim to be, and
-- a low number cannot be told apart from a genuinely cheap policy. Inventing a
-- twelve-times-larger premium a broker never typed would be worse than showing
-- a wrong one they can see and correct. Re-entering the lead fixes it.
--
-- Runs BEFORE the trigger exists, so these statements are the only writer here
-- and the arithmetic is visible in one place rather than half here and half in
-- a trigger firing mid-backfill.
-- ============================================================================

-- CURRENT side, rows the broker typed in MONTHS.
update premium_history set
      current_auto_premium_monthly = current_auto_premium_annual,
      current_homeowner_premium_monthly = current_homeowner_premium_annual,
      current_tenant_premium_monthly = current_tenant_premium_annual,
      current_auto_premium_annual = current_auto_premium_annual * 12,
      current_homeowner_premium_annual = current_homeowner_premium_annual * 12,
      current_tenant_premium_annual = current_tenant_premium_annual * 12
where current_premium_unit = 'month';

-- CURRENT side, rows typed in YEARS: the annual figure already stands.
update premium_history set
      current_auto_premium_monthly = round(current_auto_premium_annual / 12),
      current_homeowner_premium_monthly = round(current_homeowner_premium_annual / 12),
      current_tenant_premium_monthly = round(current_tenant_premium_annual / 12)
where current_premium_unit = 'year';

-- NEW side, rows the broker typed in MONTHS.
update premium_history set
      new_auto_premium_monthly = new_auto_premium_annual,
      new_homeowner_premium_monthly = new_homeowner_premium_annual,
      new_tenant_premium_monthly = new_tenant_premium_annual,
      new_auto_premium_annual = new_auto_premium_annual * 12,
      new_homeowner_premium_annual = new_homeowner_premium_annual * 12,
      new_tenant_premium_annual = new_tenant_premium_annual * 12
where new_premium_unit = 'month';

-- NEW side, rows typed in YEARS: the annual figure already stands.
update premium_history set
      new_auto_premium_monthly = round(new_auto_premium_annual / 12),
      new_homeowner_premium_monthly = round(new_homeowner_premium_annual / 12),
      new_tenant_premium_monthly = round(new_tenant_premium_annual / 12)
where new_premium_unit = 'year';

-- ============================================================================
-- 3. The one place the twelve lives
--
-- Every read of a premium is now a plain column read. No service, no route, no
-- screen multiplies or divides a premium again - this function is the whole
-- rule, and it runs inside the writing statement so the two halves of a pair
-- cannot be observed out of step.
--
-- NULL IN, NULL OUT falls out of the arithmetic: null * 12 is null, so a
-- product the broker has not entered stays empty on both sides rather than
-- becoming a zero that reads as "quoted, free".
--
-- ROUNDED going down to months, because premiums are quoted in whole dollars.
-- The annual figure stays the exact one typed - it is what Meta receives and
-- what commission is paid on - so the rounding only ever affects the monthly
-- DISPLAY of a yearly quote, never a number anybody is paid.
--
-- ONLY DERIVES WHEN THE TYPED FIGURE MOVED, and this guard is the difference
-- between a display and a slow corruption. Switching the unit alone sends no
-- figures (the form PATCHes just the flag), and re-deriving on that would push
-- every number through a round trip it does not survive: a $1,819 annual
-- becomes 152 a month, and 152 * 12 is 1,824. Five dollars invented by
-- clicking a button. Toggling twice more invents five more.
--
-- So a row whose typed side is unchanged keeps BOTH figures exactly as they
-- are. The pair is already consistent - it was derived when the figure was
-- entered - and the only thing a unit switch changes is which box the form
-- reopens on. On INSERT there is no previous row, so everything derives.
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
  else
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
  else
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

drop trigger if exists trg_premium_history_derive_units on premium_history;

create trigger trg_premium_history_derive_units
  before insert or update on premium_history
  for each row execute function premium_history_derive_units();

-- ============================================================================
-- 4. The totals, with the unit arithmetic taken out
--
-- 0103 had to write `* case when unit = 'month' then 12 else 1 end` into all
-- six of these, because the annual columns could not be trusted to be annual.
-- They can now, so every total is a plain sum and every saving a plain
-- subtraction.
--
-- SAVINGS NO LONGER NEEDS A PER-SIDE MULTIPLIER either. A broker can still
-- have Current in months and Proposed in years - subtracting one from the
-- other used to be the original bug in miniature, and is now just arithmetic
-- on two annual figures.
--
-- Dropped and re-added because a generated expression cannot be altered in
-- place. Nothing is lost: they are derived, and Postgres recomputes every row.
-- ============================================================================

alter table premium_history
  drop column total_current_premium_annual,
  drop column total_current_premium_monthly,
  drop column total_new_premium_annual,
  drop column total_new_premium_monthly,
  drop column savings_annual,
  drop column savings_monthly;

alter table premium_history
  add column total_current_premium_annual numeric generated always as (
    coalesce(current_auto_premium_annual, 0)
     + coalesce(current_homeowner_premium_annual, 0)
     + coalesce(current_tenant_premium_annual, 0)
  ) stored,
  add column total_current_premium_monthly numeric generated always as (
    coalesce(current_auto_premium_monthly, 0)
     + coalesce(current_homeowner_premium_monthly, 0)
     + coalesce(current_tenant_premium_monthly, 0)
  ) stored,
  add column total_new_premium_annual numeric generated always as (
    coalesce(new_auto_premium_annual, 0)
     + coalesce(new_homeowner_premium_annual, 0)
     + coalesce(new_tenant_premium_annual, 0)
  ) stored,
  add column total_new_premium_monthly numeric generated always as (
    coalesce(new_auto_premium_monthly, 0)
     + coalesce(new_homeowner_premium_monthly, 0)
     + coalesce(new_tenant_premium_monthly, 0)
  ) stored,
  add column savings_annual numeric generated always as (
    (coalesce(current_auto_premium_annual, 0)
     + coalesce(current_homeowner_premium_annual, 0)
     + coalesce(current_tenant_premium_annual, 0))
    - (coalesce(new_auto_premium_annual, 0)
     + coalesce(new_homeowner_premium_annual, 0)
     + coalesce(new_tenant_premium_annual, 0))
  ) stored,
  add column savings_monthly numeric generated always as (
    (coalesce(current_auto_premium_monthly, 0)
     + coalesce(current_homeowner_premium_monthly, 0)
     + coalesce(current_tenant_premium_monthly, 0))
    - (coalesce(new_auto_premium_monthly, 0)
     + coalesce(new_homeowner_premium_monthly, 0)
     + coalesce(new_tenant_premium_monthly, 0))
  ) stored;

-- ============================================================================
-- 5. Say what the columns mean, now that they mean it
-- ============================================================================

comment on column premium_history.current_auto_premium_annual is
  'The current auto premium PER YEAR. Genuinely annual since 0106: when the broker types in months the trigger derives this as monthly * 12. Safe to read directly - no unit check needed.';

comment on column premium_history.current_auto_premium_monthly is
  'The current auto premium PER MONTH. Typed when current_premium_unit is month, otherwise derived as round(annual / 12).';

comment on column premium_history.current_premium_unit is
  'Which box the broker typed the three current_* figures into. Tells the form which reading to reopen on and the trigger which way to derive. It no longer reinterprets a stored number - both readings are always present and correct (0106).';

comment on column premium_history.new_premium_unit is
  'Which box the broker typed the three new_* figures into. See current_premium_unit (0106).';

comment on column premium_history.total_new_premium_annual is
  'What the Meta CompleteRegistration event carries as its value. A plain sum of the three new_* annual columns since 0106 - they are always annual now, so no unit multiplier is applied here or anywhere else.';

commit;
