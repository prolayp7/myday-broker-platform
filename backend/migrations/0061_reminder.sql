-- A follow-up a broker owes a customer: "call back Tuesday", "send the renewal
-- docs Friday".
--
-- Its own table, not a column on contact_log, for two reasons: a broker can
-- have SEVERAL open follow-ups on one lead at once (a callback and a document
-- chase are separate promises, completed separately), and a follow-up can be
-- set with no originating call at all.
--
-- THIS IS WHAT MY DAY IS WAITING FOR. That screen currently renders a
-- "Follow-ups — not set up yet" placeholder because nothing in the schema
-- schedules a callback. Its two numbers are "overdue" and "due today", which
-- is why due_at is not null and why the index at the bottom is shaped the way
-- it is.
create table reminder (
  id uuid primary key default gen_random_uuid(),

  lead_id uuid not null references lead(id) on delete cascade,

  -- Whose list this sits on. Set from the lead's assigned broker at creation,
  -- and MOVED when a lead is reassigned — an open promise belongs to whoever
  -- holds the lead now, not to whoever made it. (contact_log does the opposite:
  -- a call Sarah made stays Sarah's forever, because it is a record of the
  -- past. A reminder is a debt against the future.)
  broker_id uuid not null references broker(id),

  -- What KIND of promise this is. A broker's list is not homogeneous and
  -- sorting it purely by date buries the one that actually matters:
  --
  --   'Callback'  — "ring me Tuesday". A soft appointment; moving it costs
  --                 nothing.
  --   'Renewal'   — the customer's CURRENT policy expires on this date. The
  --                 hard one: miss it and they auto-renewed with their existing
  --                 insurer and are gone for a year. Cannot be moved — it is
  --                 the insurer's date, not ours — which is why it is a
  --                 different kind and not just another callback.
  --   'Document'  — waiting on something from the customer (void cheque,
  --                 signed application, licence photo). A large share of real
  --                 broker follow-ups are these, not phone calls.
  --   'Other'     — deliberately present so a new situation never needs a
  --                 migration before a broker can write the reminder down.
  kind text not null default 'Callback'
    check (kind in ('Callback', 'Renewal', 'Document', 'Other')),

  -- When this is due. Snoozing MOVES this value rather than setting a status —
  -- a row marked "snoozed" whose due_at stayed in the past would keep showing
  -- up as overdue forever, which is the bug that makes people stop trusting a
  -- reminder system.
  --
  -- For kind = 'Renewal' this is when the BROKER should be chasing it, not the
  -- policy's own expiry date — a renewal worked on its expiry morning is
  -- already lost. The expiry itself is the customer's fact and belongs on the
  -- policy record; this column is only ever "when does this land on someone's
  -- list".
  due_at timestamptz not null,

  -- What to do. NOT NULL: a reminder without a reason is an alarm nobody can
  -- act on, and by the time it fires the broker has forgotten why they set it.
  note text not null check (length(trim(note)) > 0),

  -- Open until something closes it.
  --   'Open'      — still owed.
  --   'Done'      — the broker did the thing.
  --   'Dismissed' — no longer relevant (lead went Lost, customer bought
  --                 elsewhere). Deliberately NOT folded into 'Done': counting
  --                 an abandoned follow-up as completed work would flatter
  --                 every follow-through report in the system.
  status text not null default 'Open' check (status in ('Open', 'Done', 'Dismissed')),

  -- When it was closed, and by whom. Null while Open.
  --
  -- completed_at is what makes "was this done on time?" answerable — comparing
  -- it against due_at. Without it, status = 'Done' says the work happened but
  -- never says whether it happened the same day or three weeks late, which is
  -- the only thing worth measuring about a follow-up.
  completed_at timestamptz,
  -- Who closed it. Usually broker_id, but an admin can dismiss follow-ups when
  -- offboarding someone — so it is its own column rather than assumed.
  completed_by uuid references broker(id),

  -- Which contact this came out of, when it came out of one — e.g. a call
  -- whose outcome was 'Call Back Requested'. Null when a broker set it
  -- manually. on delete set null: deleting a call must not silently delete the
  -- promise the broker made on it.
  source_contact_log_id uuid references contact_log(id) on delete set null,

  -- How many times the broker has tried and failed to close this out.
  --
  -- A broker rings, no answer; rings the next day, no answer. That is ONE
  -- unkept promise attempted three times, not three promises. Without this
  -- the obvious implementation spawns a fresh row per attempt, the list fills
  -- with near-duplicates, and the broker stops reading it — which is how a
  -- reminder system dies. Instead the same row's due_at moves forward and this
  -- goes up.
  --
  -- Also the number that says when to stop: a customer chased six times
  -- without answering is a different conversation (mark the lead Lost) than
  -- one chased once, and nothing else in the schema makes that visible.
  attempt_count int not null default 0,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  -- A closed reminder has a closing time; an open one cannot. Enforced here
  -- rather than trusted to the service layer, because the two halves are
  -- written by different code paths (complete, dismiss, admin bulk-dismiss)
  -- and only the database sees all of them.
  constraint reminder_closed_has_timestamp check (
    (status = 'Open' and completed_at is null)
    or (status <> 'Open' and completed_at is not null)
  )
);

create trigger trg_reminder_updated_at
  before update on reminder
  for each row execute function set_updated_at();

-- My Day's two numbers: this broker's OPEN reminders, overdue or due today.
-- Partial (status = 'Open') because that query never looks at closed rows, and
-- closed rows are the ones that accumulate forever — the index stays small
-- while the table grows.
create index idx_reminder_open_due on reminder(broker_id, due_at)
  where status = 'Open';

-- The lead's own follow-up list, shown on the Contact History tab. Not
-- partial: that panel shows completed reminders too, struck through, so a
-- broker can see what they already did for this customer.
create index idx_reminder_lead_id on reminder(lead_id, due_at);

alter table reminder enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) reaches this
-- table, same rule as every other table in this schema.
