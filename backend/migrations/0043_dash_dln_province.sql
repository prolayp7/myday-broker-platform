-- Split the issuing province out of every DASH DLN field, matching
-- driver.license_province's existing shape (0035). DASH always prints a DLN
-- as "<number> <province>" on one line (e.g. "F7172-55339-70224 Ontario") —
-- the province is a separate piece of data, not part of the licence number
-- itself, and worth its own column rather than folding it back into free text.
--
-- The base *_dln columns now hold ONLY the number (no trailing province).
alter table dash_report
  add column dln_province text;

alter table dash_policy
  add column operator_dln_province text;

alter table dash_claim
  add column first_party_dln_province text,
  add column third_party_dln_province text;
