-- A broker's own Callback reminder now queues a WhatsApp, the same way a
-- renewal-ladder rung does.
--
-- WHAT THIS TURNS ON. Until now queue_renewal_notification() returned early on
-- any reminder with a null auto_rule, which is every reminder a broker sets by
-- hand — so "remind me to call them back on Thursday" existed only as a row on
-- a screen nobody looks at on a Thursday. This is the one line that makes it
-- arrive on their phone.
--
-- 0118's own comment claimed a broker's reminder "has its own sweep and its
-- own template". That was aspirational — there was no callback sweep, no
-- callback template and no send path. This migration and the callback branch
-- in services/renewal_notification.py are what finally make it true. The
-- comment is corrected below so the next reader is not misled again.
--
-- STILL BROKER-FACING ONLY. The send path addresses broker.phone_e164 and has
-- no customer branch anywhere (services/renewal_notification.py, and 0109's
-- header records that customer-facing messaging was considered and rejected
-- 2026-07-12). The worst this can do is message a broker about their own
-- reminder. Nothing here puts a customer's phone into a send.
--
-- NOT A SCHEDULER. Queueing only records that a message is OWED. When it
-- actually goes out is decided by the drain, which as of this change filters
-- on reminder.due_at — a callback set for next Tuesday sits Pending until
-- Tuesday. Before that filter existed this trigger would have fired the
-- message within 60 seconds of the broker setting it, which is the single
-- most important thing to keep true about this pair of changes.

begin;

create or replace function queue_renewal_notification()
returns trigger as $$
begin
  -- TWO KINDS QUEUE, for different reasons.
  --
  -- Ladder rungs (renewal_30/15/5): created by the hourly sweep on the day
  -- they are due, so queueing is effectively "send now".
  --
  -- Manual Callbacks (auto_rule null, kind 'Callback'): created by a broker
  -- days ahead. These rely on the drain's due_at filter to hold them until
  -- their time — see the module comment above.
  --
  -- renewal_lost → still deliberately absent. It keeps working exactly as it
  -- does today: a row the broker reads on screen, no WhatsApp. Messaging leads
  -- that already said no remains a separate decision.
  --
  -- Other manual kinds (Renewal, Document, Other set by hand) → also absent,
  -- and deliberately: only Callback has an approved template. Widening this
  -- means approving a template first, not editing this condition.
  -- THE NULL CHECK IS LOAD-BEARING, not defensive noise. `null in (...)`
  -- evaluates to NULL in Postgres, not false — so for a manual Document
  -- reminder the whole condition became `NULL or false` = NULL, `if not NULL`
  -- is not true, the early return never fired, and it queued a WhatsApp for a
  -- template that does not exist. Caught on staging; the `is not null` guard
  -- is what keeps three-valued logic out of this decision.
  if not (
    (new.auto_rule is not null
     and new.auto_rule in ('renewal_30', 'renewal_15', 'renewal_5'))
    or (new.auto_rule is null and new.kind = 'Callback')
  ) then
    return new;
  end if;

  -- on conflict: the sweep's own upsert already makes re-runs free, and this
  -- keeps the same promise one level down.
  insert into renewal_notification (reminder_id)
  values (new.id)
  on conflict (reminder_id) do nothing;

  return new;
end;
$$ language plpgsql;

-- The name is now wrong — it carries callbacks too — but renaming the table
-- would touch every reference for no behavioural gain, so the comment carries
-- the correction instead.
comment on table renewal_notification is
  'One queued WhatsApp per reminder: renewal-ladder rungs (0118) and brokers'' own Callback reminders (0122). Named for renewals only because it predates callbacks. Keyed solely on reminder_id — lead, broker and counts are read through the reminder at SEND time, so a reassignment or a later call is picked up automatically. Queueing does not mean "send now": the drain holds a row until reminder.due_at has passed.';

commit;
