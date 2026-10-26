-- The seeded lead price was a placeholder. The real rate is $3.
--
-- 0088 created lead_cost_settings with `values (20)` — a number chosen to make
-- the calculation answer on its first day, not a price anyone agreed to. It
-- then stayed, because the endpoint that changes it existed and no screen ever
-- called it: the only way to edit the price was to reach the row directly.
-- That gap was closed on 2026-08-23 (f567841, bbbcee0), and the price was set
-- to its real value of 3.00 the same evening.
--
-- WHY A MIGRATION AND NOT JUST THE ONE-OFF SCRIPT. The script beside this
-- (scripts/fix_seeded_lead_cost.py) corrects the deliveries already stamped at
-- $20 in this database. It does nothing for the NEXT database: a staging
-- rebuild, a fresh developer environment, or a restore would replay 0088, seed
-- $20 again, and start billing at a price nobody chose. The seed has to be
-- corrected in the migration chain or the bug is only fixed once.
--
-- GUARDED ON `= 20`, so this is a no-op the moment the price is anything
-- deliberate. An admin who sets $5 tomorrow must not find it reset to $3 by a
-- replay — this migration corrects a placeholder, it does not assert a policy.
--
-- 0088 IS LEFT ALONE. Rewriting it would erase the record of what the database
-- actually did for its first week, and every environment that already ran it
-- would silently disagree with the file. Migration history is a record, not a
-- draft.

update lead_cost_settings
set cost_per_lead = 3
where id = 1
  and cost_per_lead = 20;
