-- Make "group names are unique within a community" race-proof at the DB
-- level. The application already checks this (_reject_duplicate_name in
-- services/broker_group.py), but that's a check-then-insert: two concurrent
-- "create group X" requests can both pass the check and both insert. Only a
-- DB constraint can stop that.
--
-- Case-insensitive to match the app's ilike check — "GTA Team" and "gta team"
-- count as the same name. Scoped per community: the same name may exist in
-- two different communities (that's allowed, and intentional).
--
-- Safe to apply: verified no existing (community, lower(name)) duplicates.
-- The app-level check stays as-is — it still produces the friendly 409 in the
-- common case; this index is the backstop that closes the concurrent race.
create unique index uq_broker_group_name_per_community
  on broker_group (community_id, lower(name));
