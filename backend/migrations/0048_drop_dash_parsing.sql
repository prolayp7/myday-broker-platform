-- Removes the DASH parsing domain AND the vehicle table entirely.
--
-- DASH parsing: per instruction, no auto-import of any kind. The broker reads
-- DASH's own website during the call and types what they see straight into
-- New Policy by hand. Removes upload/URL parse, Gemini extraction, and DASH's
-- own frozen record of a report.
--
-- vehicle: built this session as the broker's editable record, seeded from
-- DASH by VIN. With DASH parsing gone the seeding half of that design no
-- longer applies, and the table is being dropped so vehicles can be replanned
-- from scratch rather than carrying assumptions (VIN-matching, dash_vehicle_id
-- seeding) that belonged to the old workflow.
--
-- DESTRUCTIVE. Drops every DASH report ever pulled and every vehicle ever
-- entered. Confirmed before running. Nothing here is reversible without a
-- database backup taken before this migration ran.
--
-- Order:
--   1. vehicle first. It has a foreign key INTO dash_vehicle
--      (vehicle.dash_vehicle_id) from OUTSIDE the dash_* family, which blocks
--      dropping dash_vehicle until it is gone. Caught by actually running an
--      earlier version of this migration in the wrong order: "cannot drop
--      table dash_vehicle because other objects depend on it".
--   2. Then the dash_* tables, children before parents:
--      dash_claim_kol -> dash_claim -> dash_policy (nullable) / dash_report
--      dash_inquiry -> dash_report
--      dash_vehicle -> dash_policy
--      dash_policy -> dash_report
--      dash_report -> driver

drop table if exists vehicle;

drop table if exists dash_claim_kol;
drop table if exists dash_claim;
drop table if exists dash_inquiry;
drop table if exists dash_vehicle;
drop table if exists dash_policy;
drop table if exists dash_report;
