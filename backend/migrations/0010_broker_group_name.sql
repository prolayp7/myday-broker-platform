-- Admin-typed label for a broker group (e.g. "Nature 1 - GTA Auto Team"),
-- separate from the auto-generated community + group_number pairing.
-- The 10-broker-per-group cap is enforced in application code, not here —
-- it's a fixed constant, not a per-row column.
alter table broker_group add column name text not null default '';
