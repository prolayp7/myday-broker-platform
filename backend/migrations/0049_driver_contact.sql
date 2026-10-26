-- The applicant's own name, phone and email, on the driver record.
--
-- WHY THESE DID NOT EXIST: 0035 assumed DASH would fill a driver's identity —
-- that is why the columns dropped below were never written by anything. DASH
-- parsing is gone (0048), so the broker is now the only source, and the
-- New Policy screen needs somewhere to put what they type.
--
-- SEEDED FROM THE LEAD, ONCE. services/driver.get_or_create_principal copies
-- lead.name/phone/email into the applicant row at the moment that row is
-- created — which is the first time a broker opens the lead. 0035's own
-- comment argued the opposite ("blank, not seeded ... unverified marketing
-- data"), and that reasoning held while DASH was going to overwrite it with
-- something verified. With DASH gone, blank means the broker retypes a name
-- already on their screen. The lead's details are a starting point the broker
-- confirms or corrects, not a claim of verification.
--
-- Seeded ONCE, deliberately: if a broker clears the name, it stays cleared.
-- A field that refills itself after being emptied is a bug report, not a
-- feature.
--
-- One `name`, not first/middle/last. The lead carries a single name string
-- and splitting it is guesswork ("Mary Jane Smith" — middle name, or double
-- first name?). Nothing downstream needs the parts.
alter table driver add column name text;
alter table driver add column phone text;
alter table driver add column email text;

-- Dropped, not left dormant. Nothing has ever written them: DASH was their
-- only intended writer, no UI collects them, and every driver row in the
-- database has all three null (checked before writing this). Keeping them
-- alongside `name` would leave two ways to store a name and no way to tell
-- which one is authoritative.
alter table driver drop column first_name;
alter table driver drop column middle_name;
alter table driver drop column last_name;
