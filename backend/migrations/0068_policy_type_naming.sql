-- Policy type vocabulary: say whether auto is included.
--
-- "Homeowners" and "Tenants" never stated that they bundle an AUTO policy
-- with the property one — a broker reading the dropdown had to know it. The
-- new names say it outright, and the five values become symmetrical: three
-- standalone, two bundled.
--
-- CONDO IS KEPT, and renamed to match. It earns the HOMEOWNER rate (a condo
-- is owner-occupied property), so it is not a rating distinction — but
-- property.property_type already carries 'Condo' as a first-class value with
-- condo-only fields hanging off it, and having the lead-level label say
-- 'Auto and Homeowner' for a quote the property row calls a condo is the
-- kind of disagreement that gets read as a bug.
--
-- The two are NOT redundant. property.property_type is per property row: a
-- rental dwelling under a condo primary rates as a house. This column is the
-- lead-level shape of the deal. Report condo volume from property_type, which
-- is the finer-grained truth.
--
-- ORDER MATTERS. A CHECK constraint validates PER STATEMENT, not at commit,
-- so the old constraint must be dropped BEFORE the rows are updated —
-- otherwise the update trips the constraint it is on its way to replacing.
-- Same sequencing as 0067 §2, for the same reason.
--
-- KEEP IN SYNC (hand-maintained, no generator):
--   * backend/schemas/insurer.py         REQUIRED_RATES keys
--   * frontend/src/domain/vehicle.ts     POLICY_TYPES, PREMIUM_FIELDS_FOR_POLICY

begin;

-- 1. Drop first — see the note above on per-statement validation.
alter table lead drop constraint if exists lead_combined_policy_type_check;

-- 2. Rename the stored values.
update lead set combined_policy_type = 'Homeowner Only'
where combined_policy_type = 'Home Only';

update lead set combined_policy_type = 'Auto and Homeowner'
where combined_policy_type = 'Homeowners';

update lead set combined_policy_type = 'Auto and Tenant'
where combined_policy_type = 'Tenants';

update lead set combined_policy_type = 'Auto and Condo'
where combined_policy_type = 'Condo';

-- 3. Re-add with the same explicit name, so a future migration can drop it
--    by name rather than hunting pg_constraint (0067's first attempt failed
--    exactly there).
alter table lead add constraint lead_combined_policy_type_check check (
  combined_policy_type in (
    'Auto Only',
    'Homeowner Only',
    'Tenant Only',
    'Auto and Homeowner',
    'Auto and Tenant',
    'Auto and Condo'
  )
);

-- 4. Prove nothing was missed. Any surviving old value means a row slipped
--    past the updates above, and the constraint would have caught it — this
--    is belt-and-braces so the failure is legible rather than a 23514.
do $$
declare
  stale int;
begin
  select count(*) into stale
  from lead
  where combined_policy_type is not null
    and combined_policy_type not in (
      'Auto Only', 'Homeowner Only', 'Tenant Only',
      'Auto and Homeowner', 'Auto and Tenant', 'Auto and Condo'
    );

  if stale > 0 then
    raise exception 'Aborted: % lead row(s) still hold an unrecognised policy type', stale;
  end if;
end $$;

select combined_policy_type, count(*)
from lead
group by combined_policy_type
order by combined_policy_type nulls last;

commit;
