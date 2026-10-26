-- Drop the `manager` role. Two tiers from here: `admin` and `broker`.
--
-- WHY. `manager` never meant anything. auth_dependencies.py::require_admin —
-- the single authorization decision in the whole backend — accepted
-- ("admin", "manager") and treated them identically, and no code anywhere
-- ever checked for "admin" specifically. The business has now confirmed it
-- never will: there is one admin and one manager, and they are the same
-- person.
--
-- A permission tier that grants exactly what the tier above it grants is not
-- free — it is a trap. The first genuinely admin-only feature would silently
-- be manager-accessible, and until then the Role dropdown lets a manager
-- promote themselves to admin. Deleting the value closes both without a line
-- of new authorization logic.
--
-- The only row holding it is qa-tester-one@example.com, an inactive test
-- account. No real person is affected.
--
-- SAFE TO RUN WHILE THE APP IS LIVE. `role` is re-read from this table on
-- every request (auth_dependencies.py) rather than trusted from the JWT, so
-- the change takes effect on the next request with no stale-token window and
-- nobody needing to log in again. Reads cannot break either: `Role` appears
-- only on request models, never a response, and routes/broker.py declares no
-- response_model at all.

-- 1. Reassign the holder BEFORE the constraint can reject it. Demoting is the
--    conservative direction — a wrongly-demoted account is locked out and
--    obvious, where a wrongly-promoted one is silent.
update broker set role = 'broker' where role = 'manager';

-- 2. Swap the constraint.
--
--    0003 declared it INLINE (`role text ... check (role in (...))`), so
--    Postgres auto-generated the name. Guessing that name would make this
--    migration fail halfway — after the update above has already run — so it
--    is looked up instead. The replacement is named explicitly, so whoever
--    changes this next can drop it by name and skip all of this.
do $$
declare
  v_constraint_name text;
begin
  select conname into v_constraint_name
  from pg_constraint
  where conrelid = 'broker'::regclass
    and contype = 'c'
    and pg_get_constraintdef(oid) ilike '%role%';

  if v_constraint_name is not null then
    execute format('alter table broker drop constraint %I', v_constraint_name);
  end if;

  alter table broker
    add constraint broker_role_check check (role in ('broker', 'admin'));
end $$;

-- Re-running is harmless: the update matches nothing, and the lookup finds
-- the constraint added above and replaces it with an identical one.
--
-- ROLLBACK, if ever needed:
--   alter table broker drop constraint broker_role_check;
--   alter table broker add constraint broker_role_check
--     check (role in ('broker', 'manager', 'admin'));
-- The demoted row was qa-tester-one@example.com (inactive), previously
-- 'manager'. No other data is touched.
