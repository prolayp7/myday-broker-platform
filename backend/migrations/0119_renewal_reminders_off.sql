-- "Stop Reminder" — the broker silences a lead's renewal ladder from WhatsApp.
--
-- The renewal nudge (0118) carries a quick-reply button. Tapping it sets this
-- column, and the remaining rungs for that lead never fire: pressed on the
-- 30-day nudge, the 15 and the 5 do not follow.
--
-- A COLUMN ON THE ROW, not a side table. The same shape lead.assigned_by (0018)
-- and lead.licensed_in_canada (0108) take — a fact about this lead, stored on
-- this lead.
--
-- NOT NULL DEFAULT FALSE, unlike those two. A nullable flag would have three
-- states where the question has two: every lead either gets renewal nudges or
-- does not, and "unanswered" is not a third answer. Existing rows are all false
-- because nobody has stopped anything yet.
--
-- LADDER RUNGS ONLY. A broker's own custom reminders are untouched by this —
-- they are not renewal alerts, and silencing someone's own follow-ups because
-- they muted an automated nudge would be wrong. The drain enforces that by
-- filtering on auto_rule, not by reading this column alone.
--
-- ONE-WAY, AND THAT IS ACCEPTED. There is no CRM toggle: WhatsApp offers no
-- un-stop button, so a mistaken tap cannot be undone here. The recovery path is
-- the OTHER button in the same message — "Set Up Reminder" — which lets the
-- broker set a date they chose instead of the schedule they just cancelled.
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.

begin;

alter table lead
  add column renewal_reminders_off boolean not null default false;

comment on column lead.renewal_reminders_off is
  'True when the broker tapped Stop Reminder on a renewal WhatsApp. Stops future ladder rungs (renewal_30/15/5) for this lead and cancels any still pending. Does not affect the broker''s own custom reminders. One-way: recovery is to set a custom reminder instead.';

commit;
