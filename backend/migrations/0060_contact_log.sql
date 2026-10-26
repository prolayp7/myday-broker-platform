-- One row per contact attempt on a lead — a Zoom call or a broker's own
-- manually logged contact (WhatsApp, in person), read as one timeline.
--
-- zoom_call_id is what tells the two apart: set = this row came from a Zoom
-- call, null = a broker typed it. No separate source column — that would be
-- the same fact stored twice, and two copies of one fact can disagree.
create table contact_log (
  id uuid primary key default gen_random_uuid(),

  lead_id uuid not null references lead(id) on delete cascade,
  broker_id uuid not null references broker(id),

  -- Set only when this row came from a real Zoom call. Null means a broker
  -- logged it by hand.
  --
  -- No cascade: deleting a Zoom record must never delete the CONTACT. That a
  -- broker spoke to this customer is a fact about the relationship, and it
  -- survives the call recording being purged for retention — the row simply
  -- stops offering a transcript. Restricting the delete instead would be
  -- worse: it would make retention deletion fail against exactly the calls
  -- that mattered enough to be logged.
  zoom_call_id uuid references zoom_call(id) on delete set null,

  outcome text not null check (outcome in (
    'Answered', 'No Answer', 'Busy', 'Wrong Number', 'Not Interested', 'Call Back Requested'
  )),

  -- The lead's stage at the MOMENT this contact happened — before any stage
  -- change this same contact goes on to cause. Not derivable for free: lead.
  -- stage is only the CURRENT stage, and reconstructing "what stage was it
  -- back then" from lead_stage_history would mean every report re-doing a
  -- latest-row-before-this-timestamp join. Stored once here instead, same
  -- reasoning as premium_history.savings — a fact worth keeping outright
  -- rather than recomputing every time it's asked for. This is what makes
  -- "how many touches to move a lead out of New" a straight count, not a join.
  --
  -- NO CHECK CONSTRAINT, deliberately — the same rule lead_stage_history
  -- follows. This records what was true at the time, including stage names
  -- since retired: migration 0051 renamed three stages and removed a fourth,
  -- and a constraint here would have made those old rows illegal retroactively
  -- and blocked the rename.
  stage_at_contact text not null,

  -- The broker's own note. Optional on a Zoom call (the recording speaks for
  -- itself); effectively required in practice for a manually logged contact.
  notes text,

  -- Filled in only once a broker clicks "Get Transcript" on a Zoom call row —
  -- fetched from Zoom's recording and run through Google Speech-to-Text at
  -- that point, not automatically for every call. Cached here after the first
  -- fetch so a second click doesn't re-transcribe.
  transcript text,

  -- When the contact happened, not when this row was written.
  occurred_at timestamptz not null default now(),

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger trg_contact_log_updated_at
  before update on contact_log
  for each row execute function set_updated_at();

-- The lead's own timeline, newest first — the Contact History tab's only read.
-- occurred_at descending is part of the index because every view of this table
-- is in time order; without it each lead's history is a sort.
create index idx_contact_log_lead_id on contact_log(lead_id, occurred_at desc);

-- "How many calls did this broker make?" — activity reporting, and the admin
-- broker-activity screen.
create index idx_contact_log_broker_id on contact_log(broker_id, occurred_at desc);

-- One contact_log row per Zoom call. Zoom fires two webhooks per call and
-- documented duplicate deliveries on top of that; without this, a redelivery
-- arriving while the first is still being processed writes a SECOND contact
-- row for one real conversation, and the broker sees the same call twice in
-- their timeline. Partial, so the many manually-logged rows (zoom_call_id
-- null) are not forced to be unique against each other.
create unique index idx_contact_log_zoom_call_id on contact_log(zoom_call_id)
  where zoom_call_id is not null;

alter table contact_log enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) reaches this
-- table, same rule as every other table in this schema.
