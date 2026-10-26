-- Email the broker when the WhatsApp notification fails for good.
--
-- THE PROBLEM. On 2026-09-01 a broker was assigned 14 leads and was told about
-- almost none of them. Every notification was queued, submitted, and then
-- rejected by Meta; the rows sat at 'Failed' and nothing read them. Nobody
-- noticed for roughly 48 hours, because the leads were still sitting in the CRM
-- so no broker had anything to complain about. Speed-to-contact is the whole
-- game on an insurance lead, and those leads went cold in silence.
--
-- THE RULE THIS ENCODES. Every assigned lead reaches its broker through at
-- least one channel. WhatsApp stays the primary — it is faster and a broker
-- reads it sooner — and email is what happens when WhatsApp has definitively
-- given up.
--
-- ONLY ON TERMINAL FAILURE, never on a first stumble. A transient failure is
-- retried seconds later by the drain and usually succeeds; emailing on it would
-- double-notify on every hiccup and train brokers to ignore both channels. The
-- three cases that count are a permanent error from Meta, attempts exhausted,
-- and a delivery webhook reporting 'failed' after the message was accepted.
--
-- NOT A SECOND QUEUE. Two nullable columns on the row that already exists,
-- rather than a fallback_email table with its own status machine and its own
-- drain. One notification is one row, and that row tells the whole story of how
-- the broker was told — or was not. A second table would mean two places to
-- look and two things to keep in step.
--
-- NO ADMIN ALERT HERE. An hourly "notifications are failing" email with its own
-- throttle and dedupe state was considered and cut: admin visibility is the
-- read-only tracking screen, which shows these columns. A second alerting
-- system is not worth its own failure modes at this scale.
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.


-- WHEN the fallback went out. A timestamp rather than a boolean, for the reason
-- whatsapp_opted_in_at (0109) is one: the question worth answering later is
-- "when was the broker told", which a true/false cannot.
--
-- THIS COLUMN IS ALSO THE LOCK. Every send is guarded on it being null, so a
-- row that is re-marked, retried, or hit by a late duplicate webhook cannot
-- email the same broker twice about the same lead. Being the guard is why it is
-- set even in the paths below that record a failure to send.
alter table whatsapp_notification
  add column fallback_email_sent_at timestamptz;

-- WHY the fallback itself did not work — a bad address, the provider down, no
-- API key configured.
--
-- WITHOUT THIS COLUMN THE FALLBACK REPEATS THE ORIGINAL BUG. A null
-- fallback_email_sent_at alone cannot distinguish "no email was needed" from
-- "the email was needed and failed", so a broken fallback would look exactly
-- like a healthy one — silent, invisible, and discovered days later by someone
-- asking why a lead was never called. The tracking screen renders both columns
-- for precisely this reason.
alter table whatsapp_notification
  add column fallback_email_error text;

-- No backfill, on purpose. The rows that failed before this existed were never
-- going to send an email, and writing a timestamp onto them would claim a
-- broker was told something they were not.
