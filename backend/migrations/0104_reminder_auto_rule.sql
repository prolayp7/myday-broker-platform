-- REMINDERS THE SYSTEM SETS ITSELF, and the guard that stops it setting them
-- again every hour.
--
-- Two rules, both keyed off the principal driver's renewal_date (0098):
--
--   'renewal_pending'  5 days out, lead is Pending — quote it before the
--                      customer auto-renews with their existing insurer.
--   'renewal_lost'     30 days out, lead is Lost — a customer who said no in
--                      March is a different conversation in September, when
--                      their own renewal is in front of them.
--
-- WHY A COLUMN AND NOT JUST kind='Renewal'. Both rules produce Renewal
-- reminders, so kind cannot tell them apart, and neither can it tell a
-- system-set row from one a broker typed. auto_rule records WHY the row
-- exists, which is what the unique index below needs to key on.
--
-- ── THE CYCLE DATE IS THE WHOLE POINT ───────────────────────────────────
--
-- The sweep runs hourly. Without a guard, a lead matching for a month becomes
-- ~700 identical rows, and a list full of near-duplicates is how a reminder
-- system dies — 0061's own header says exactly this about attempt_count.
--
-- The obvious guard is a partial index on (lead_id, auto_rule) where status =
-- 'Open'. That is WRONG, in two ways that only show up later:
--
--   * Dismissing a reminder removes it from a status-filtered index, so the
--     next sweep an hour later recreates the thing the broker just dismissed.
--     The system argues with them once an hour, forever.
--   * It cannot tell this year's renewal from next year's, so a customer
--     would get one renewal reminder and never another.
--
-- Keying on the renewal date fixes both. A dismissed row still occupies its
-- tuple and blocks re-creation; next year's renewal is a different date, so
-- the next cycle is allowed through.
--
-- NO PARTIAL PREDICATE NEEDED. Postgres treats NULLs as DISTINCT in a unique
-- index, so manual reminders — both columns null — never collide with each
-- other however many a broker sets on one lead. Only system rows, which carry
-- both values, are constrained. This is the one place that default behaviour
-- is load-bearing rather than incidental.
--
-- BOTH COLUMNS NULLABLE, and no check tying them together: a manual reminder
-- has neither, a system one has both. A row with exactly one would be
-- meaningless, but nothing writes one — the sweep is the only writer of
-- either, and it always writes the pair.

alter table reminder
  add column auto_rule text
    check (auto_rule is null or auto_rule in ('renewal_pending', 'renewal_lost')),
  add column auto_for_renewal_date date;

create unique index idx_reminder_auto_cycle
  on reminder(lead_id, auto_rule, auto_for_renewal_date);

comment on column reminder.auto_rule is
  'Which automatic rule created this reminder, or null when a broker set it by hand. Paired with auto_for_renewal_date to make the sweep idempotent.';

comment on column reminder.auto_for_renewal_date is
  'The renewal this reminder is about. Part of the uniqueness key, so re-running the sweep creates nothing and next year''s renewal still gets its own reminder.';
