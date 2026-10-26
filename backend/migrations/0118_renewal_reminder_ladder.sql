-- Renewal reminders reach the broker on WhatsApp, three times.
--
-- WHAT WAS WRONG. reminder rows are passive — read by the Reminders screen and
-- nowhere else (services/reminder.py references no send path at all). A broker
-- who does not open that screen learns nothing, and a customer who renews with
-- their existing insurer is gone for a year.
--
-- The two rules 0104 set up also aimed at the wrong leads: renewal_pending
-- (5 days, stage Pending) and renewal_lost (30 days, stage Lost). Everything in
-- between got nothing.
--
-- WHAT THIS ADDS. A three-rung ladder — 30, 15 and 5 days before renewal — for
-- leads still in the active pipeline, each rung queueing a WhatsApp to the
-- assigned broker. renewal_lost is kept exactly as it was; renewal_pending is
-- retired because the ladder's 5-day rung already covers Pending.
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.

begin;


-- ── 1. The rule vocabulary ──────────────────────────────────────────────
--
-- The 0104 constraint is INLINE, so Postgres named it itself. Dropped by that
-- generated name rather than guessed: `reminder_auto_rule_check` is what
-- `alter table ... add column x text check (...)` produces.
--
-- BOTH OLD NAMES ARE KEPT. renewal_pending stops being written today, but rows
-- carrying it already exist and a narrower constraint would refuse to validate
-- against them. A vocabulary is not a statement about what is still created.
alter table reminder drop constraint if exists reminder_auto_rule_check;
alter table reminder add constraint reminder_auto_rule_check check (
  auto_rule is null or auto_rule in (
    'renewal_pending',  -- retired, historical rows only
    'renewal_lost',     -- still written, unchanged from 0104
    'renewal_30', 'renewal_15', 'renewal_5'
  )
);


-- ── 2. The notification queue ───────────────────────────────────────────
--
-- ITS OWN TABLE, NOT whatsapp_notification. That one is unique (lead_id,
-- broker_id) — "one notification per broker per lead, EVER" (0109) — which is
-- right for a lead alert and impossible for three rungs. Widening a live
-- production table to hold a second concern would put both at risk, so this
-- mirrors its proven shape instead: same statuses, same retry counter, same
-- fallback-email columns.
--
-- reminder_id IS THE ONLY IDENTITY, and that is deliberate. The reminder
-- already uniquely represents (lead_id, auto_rule, auto_for_renewal_date) via
-- idx_reminder_auto_cycle, so copying any of those here would create fields
-- free to disagree with their source. Lead, broker and renewal date are all
-- read THROUGH the reminder at send time.
--
-- That is also what makes reassignment a non-issue. admin_assign_lead (0076)
-- moves reminder.broker_id when a lead changes hands; a broker_id copied onto
-- this row would go stale, the drain's ownership check would cancel it, and
-- the unique key would block a replacement — a nudge lost in silence.
create table renewal_notification (
  id uuid primary key default gen_random_uuid(),
  reminder_id uuid not null unique references reminder(id) on delete cascade,

  status text not null default 'Pending'
    check (status in ('Pending', 'Submitted', 'Delivered', 'Failed', 'Cancelled')),
  attempt_count int not null default 0,
  error_message text,

  -- Meta's message id (wamid). Also how an inbound button press finds its way
  -- back here: the tap arrives carrying context.id, which is this.
  message_sid text,
  submitted_at timestamptz,
  delivered_at timestamptz,

  -- 0110's pattern. A terminal WhatsApp failure still reaches the broker, by
  -- email, and these record that it happened exactly once.
  fallback_email_sent_at timestamptz,
  fallback_email_error text,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- The drain's own query: oldest Pending first. Partial, because every other
-- status is read by id or by message_sid, never scanned.
create index idx_renewal_notification_pending
  on renewal_notification (created_at) where status = 'Pending';

-- Delivery receipts and inbound button presses both arrive knowing only the
-- message id.
create index idx_renewal_notification_sid
  on renewal_notification (message_sid) where message_sid is not null;

alter table renewal_notification enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) touches this,
-- the same posture every table in this schema takes.

create trigger trg_renewal_notification_updated_at
  before update on renewal_notification
  for each row
  execute function set_updated_at();


-- ── 3. Queue the row IN THE SAME TRANSACTION as the reminder ────────────
--
-- WHY A TRIGGER AND NOT PYTHON. The reminder insert and the notification
-- insert are two writes. If the first lands and the second fails,
-- idx_reminder_auto_cycle PERMANENTLY blocks recreating the reminder — the
-- sweep will never make that rung again — so the WhatsApp is lost with nothing
-- left to retry. Two separate Supabase calls cannot close that window; one
-- transaction can.
--
-- PLUMBING ONLY. This decides nothing about WHEN a rung is due; every band
-- lives in services/reminder.py, which is the one place they are expressed.
-- The trigger only carries a decision already made.
create or replace function queue_renewal_notification()
returns trigger as $$
begin
  -- LADDER RUNGS ONLY.
  --
  -- auto_rule is null → a broker's own reminder. It has its own sweep and its
  -- own template, and queueing here would fire it the moment it was created
  -- rather than when it comes due.
  --
  -- renewal_lost → deliberately absent. It keeps working exactly as it does
  -- today: a row the broker reads on screen, no WhatsApp. Messaging leads that
  -- already said no is a separate decision, not a side effect of this file.
  if new.auto_rule is null
     or new.auto_rule not in ('renewal_30', 'renewal_15', 'renewal_5') then
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

create trigger trg_reminder_queue_renewal_notification
  after insert on reminder
  for each row
  execute function queue_renewal_notification();


comment on table renewal_notification is
  'One queued WhatsApp per renewal-ladder reminder. Keyed solely on reminder_id: lead, broker and renewal date are read through the reminder at send time, so a reassignment or a corrected date is picked up automatically.';

comment on column renewal_notification.message_sid is
  'Meta''s wamid. Delivery receipts and inbound button presses both identify a row by this — an inbound tap carries it as context.id.';


commit;
