-- Enforce the 10-brokers-per-group cap AT THE DATABASE LEVEL, so it can't be
-- overshot by concurrent onboards/moves. The application still does a fast
-- check-then-insert for a friendly error in the common case, but that check
-- has a race: two simultaneous requests can both read "9" and both insert,
-- landing the group at 11. Only the database can serialize that correctly.
--
-- How the race is closed: before counting, we lock the target broker_group
-- row (SELECT ... FOR UPDATE). Two concurrent inserts into the SAME group now
-- queue on that lock — the second one waits, then counts AFTER the first has
-- committed its row, sees 10, and is rejected. Different groups don't block
-- each other (they lock different rows), so this adds no contention to normal
-- use.
--
-- The Recycle Bin is exempt (it's a holding area, not a working team) — same
-- rule the application already applies.
--
-- Fires on INSERT (onboarding) and on UPDATE OF group_id (moving a broker
-- into a group). A plain profile edit that doesn't touch group_id never
-- triggers the count.
create or replace function enforce_broker_group_cap()
returns trigger as $$
declare
  is_bin boolean;
  current_count int;
begin
  -- On UPDATE, only care when the broker is actually changing groups.
  if tg_op = 'UPDATE' and new.group_id = old.group_id then
    return new;
  end if;

  -- Lock the destination group row so concurrent inserts/moves into the same
  -- group serialize here rather than racing the count below.
  select is_recycle_bin into is_bin
  from broker_group
  where id = new.group_id
  for update;

  -- Recycle Bin is cap-exempt.
  if is_bin then
    return new;
  end if;

  select count(*) into current_count
  from broker
  where group_id = new.group_id;

  if current_count >= 10 then
    -- SQLSTATE 'P0001' (raise_exception). The app maps this to a clean 409;
    -- see services/broker.py. The message is a backstop only — the app's own
    -- pre-check produces the user-facing message in the common (non-racing)
    -- case.
    raise exception 'broker group is full (10 max)'
      using errcode = 'P0001';
  end if;

  return new;
end;
$$ language plpgsql;

create trigger trg_enforce_broker_group_cap
  before insert or update of group_id on broker
  for each row
  execute function enforce_broker_group_cap();
