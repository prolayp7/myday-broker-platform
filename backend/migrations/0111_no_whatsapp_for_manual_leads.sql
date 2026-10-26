-- A manually created lead does not notify its broker.
--
-- THE POINT OF THE ALERT is speed-to-contact: a lead arrives from an ad, the
-- round-robin assigns it, and the broker's phone tells them before a
-- competitor rings the customer first.
--
-- A MANUAL LEAD HAS NONE OF THAT. The broker is typing it in themselves,
-- usually with the customer still on the line — they are already looking at
-- it. Notifying their handset about a lead they just created is noise, and a
-- channel that sends noise is a channel people mute. Losing the real alerts
-- to habituation is a far worse outcome than missing this one.
--
-- WHY THE TRIGGER AND NOT services/whatsapp.py. The sender could skip these
-- rows, but the row would still be created — a queue full of things that must
-- never send. This is the same class of decision as the consent check below
-- it, and 0109 stated the principle: a broker who never opted in
-- "accumulates no rows at all, rather than a queue of things that can never
-- be sent."
--
-- SAFE BECAUSE OF HOW A MANUAL LEAD IS WRITTEN. services/lead.py's
-- create_manual_lead inserts source='manual' and assigned_broker_id in the
-- SAME statement, so by the time this after-insert trigger runs, new.source
-- is already populated. No ordering race, and nothing to look up.
--
-- ONLY 'manual'. Website and TikTok leads still alert: those arrive on their
-- own and nobody is watching for them. One more line here the day that
-- changes.
--
-- Everything else in this function is carried over from 0109 byte-for-byte,
-- including the on-conflict block whose comment explains why a Cancelled row
-- is revived rather than ignored.
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.

create or replace function queue_whatsapp_notification()
returns trigger as $$
declare
  v_opted_in timestamptz;
begin
  -- A lead the broker typed in themselves. See this migration's header.
  -- FIRST, before the consent lookup, so a manual lead costs not even a
  -- select against broker.
  if new.source = 'manual' then
    return new;
  end if;

  -- Unassigned (a lead returned to the pool) — nothing to tell anyone.
  if new.assigned_broker_id is null then
    return new;
  end if;

  -- No consent, no message. Checked HERE as well as before sending so that a
  -- broker who never opted in accumulates no rows at all, rather than a queue
  -- of things that can never be sent.
  select whatsapp_opted_in_at into v_opted_in
  from broker where id = new.assigned_broker_id;

  if v_opted_in is null then
    return new;
  end if;

  insert into whatsapp_notification (lead_id, broker_id)
  values (new.id, new.assigned_broker_id)
  on conflict (lead_id, broker_id) do update
    -- REACTIVATE A CANCELLED ROW, do not silently do nothing.
    --
    -- A plain "do nothing" here loses a legitimate notification:
    --   lead assigned to A      -> Pending row for A
    --   reassigned to B         -> A's row Cancelled (A no longer owns it)
    --   reassigned back to A    -> unique constraint blocks the insert,
    --                              "do nothing" swallows it, and A is NEVER
    --                              told about a lead they now own.
    --
    -- Only 'Cancelled' is revived. Not 'Submitted'/'Delivered' (already sent,
    -- reviving would double-message), and not 'Failed' — attempts there are
    -- spent and it needs a person to look, which is a deliberate manual
    -- decision, not something a reassignment should quietly undo.
    set status = 'Pending',
        attempt_count = 0,
        error_message = null
    where whatsapp_notification.status = 'Cancelled';

  return new;
end;
$$ language plpgsql;

-- The trigger itself is unchanged and is NOT recreated: create or replace
-- function swaps the body underneath the existing trg_lead_whatsapp_assign,
-- so there is no window in which the trigger is missing.

-- No backfill. Nothing manual is queued right now (the queue holds only
-- Delivered and Failed rows), so there is nothing to clean up — and deleting
-- rows on the strength of an assumption is worse than leaving them. The
-- pre-flight query in the header of this repo's apply notes, or simply:
--
--   select n.id, l.source, n.status
--   from whatsapp_notification n join lead l on l.id = n.lead_id
--   where l.source = 'manual' and n.status = 'Pending';
--
-- returns nothing today; if it ever returns rows, decide then.
