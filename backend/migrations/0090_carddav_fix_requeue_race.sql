-- Fix: a lead edited WHILE its card is being written loses the edit forever.
--
-- THE BUG. requeue_carddav_contact (0089) carried `and status <> 'Pending'`,
-- meaning "don't bother re-queueing something already queued". That is wrong
-- whenever a write is already in flight:
--
--   1. the drain reads the row (Pending) and builds a card from the lead as it
--      is right now
--   2. it PUTs — a network call of up to 30 seconds
--   3. mid-flight, someone corrects the customer's name. The trigger fires and
--      matches ZERO rows, because the row is still 'Pending'. The requeue is
--      silently dropped.
--   4. the PUT returns and the drain marks the row Synced with the hash of the
--      PRE-EDIT card
--
-- The row is now Synced, its hash matches a card that no longer reflects the
-- lead, and nothing will ever queue it again — the one trigger that would have
-- has already fired and done nothing. The broker keeps the stale contact
-- indefinitely, and no error is raised anywhere.
--
-- THE FIX, in two halves. This migration is the first: drop the WHERE clause so
-- an edit always leaves a mark. The second half is in the application — the
-- drain now marks a row Synced only if it is STILL the Pending row it read
-- (repositories/carddav_contact.mark_synced), so a requeue that lands mid-flight
-- survives instead of being overwritten.
--
-- Bumping updated_at unconditionally is what makes that guard possible: it is
-- the token the drain compares against.
--
-- APPLY BY HAND in the Supabase SQL editor.

create or replace function requeue_carddav_contact()
returns trigger as $$
begin
  -- EVERY row for this lead, with no status filter.
  --
  -- Setting an already-Pending row to Pending looks like a no-op and is not:
  -- the updated_at trigger fires, which is precisely how an in-flight write
  -- learns that it is about to save something stale.
  update carddav_contact
     set status = 'Pending',
         attempt_count = 0,
         error_message = null
   where lead_id = new.id;

  return new;
end;
$$ language plpgsql;

-- The trigger itself is unchanged (0089 created it) — `create or replace
-- function` above is enough, since the trigger references the function by name.
