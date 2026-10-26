-- Three distinct broker states, not two:
--   1. Active — normal working broker in a real group.
--   2. In Recycle Bin — "Deactivate" moves the broker into the single global
--      Recycle Bin group (a normal broker_group row, is_recycle_bin=true,
--      exempt from the 10-cap). Fully reversible: "Restore" moves them back
--      into any real group via the same move-broker mechanism already built.
--   3. Archived — a PERMANENT, one-way lock applied to a broker while
--      they're in the Recycle Bin. No restore, no edit, no move — every
--      field stays exactly as it was, forever, for compliance. This is a
--      flag, not a delete: the row is never removed.
--
-- Group deletion is unaffected by any of this — a broker is always moved
-- OUT of a group (into the Recycle Bin) before that group can be considered
-- empty, so the existing delete-when-empty logic needs no changes at all.
alter table broker_group add column is_recycle_bin boolean not null default false;
alter table broker add column is_archived boolean not null default false;
alter table broker add column archived_at timestamptz;
