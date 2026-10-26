-- The unique index that 0045 should have carried, added separately because
-- 0045 had already been applied by the time the constraint was designed.
-- Editing 0045 in place would leave the file disagreeing with what actually
-- ran on the database.
--
-- WHY IT MATTERS: vehicle rows are matched to a DASH report BY VIN, never by
-- dash_vehicle_id. Measured on the live database — re-parsing a report deletes
-- and re-inserts every dash_vehicle row with new ids, which sets
-- vehicle.dash_vehicle_id back to null (ON DELETE SET NULL), while the VIN
-- stays identical. So the VIN is the only stable link between what DASH
-- reports and what the broker has collected.
--
-- That makes "one VIN per lead" the rule the whole sync depends on. Enforced
-- here rather than trusted to application code: two overlapping DASH saves
-- could otherwise insert the same VIN twice, and the match would become
-- ambiguous with no way to tell which row the broker's data belonged to.
--
-- PARTIAL (where vin is not null): DASH sometimes prints no VIN, or redacts
-- it. Those vehicles cannot be matched to anything, so they must never collide
-- with each other — two cars with no VIN are always two different cars.
create unique index idx_vehicle_lead_vin
  on vehicle(lead_id, vin)
  where vin is not null;
