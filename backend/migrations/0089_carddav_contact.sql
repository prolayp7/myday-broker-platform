-- CardDAV contact sync: leads in the broker's phone address book.
--
-- THE PROBLEM. A customer rings a broker's Zoom number and the screen shows a
-- bare number. The CRM knows who it is, but the CRM is not involved at the
-- moment the phone rings — the only Zoom connection is a webhook that fires
-- AFTER the call has completed (routes/zoom_webhooks.py). Nothing joins the
-- name to the ringing.
--
-- THE FIX. The phone already names a ringing number for everyone in its
-- address book, and Zoom's own Phone Contacts Matching reads that address book
-- for its incoming-call screen. So the leads go in the address book: a Radicale
-- CardDAV server holds one collection per broker, the CRM writes vCards into
-- it, and each broker's phone subscribes once.
--
-- ADD-ONLY, DELIBERATELY. Nothing here ever removes a card. When a lead is
-- reassigned the new broker gets it and the old broker keeps it. A delete would
-- only land on the old broker's NEXT sync — 15+ minutes away, never if their
-- phone is off — and they can export their contacts regardless, so it would buy
-- the appearance of control rather than control. There is no 'Deleting' status
-- and no DELETE path anywhere in this feature.
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.


-- ── the broker's CardDAV credential ──────────────────────────────────────
--
-- Separate from their CRM password, necessarily: password_hash is bcrypt and
-- unrecoverable, while a phone needs a credential it can actually send. This
-- one is bcrypt-hashed too — the CRM never replays it (writes go through the
-- admin principal), so nothing needs the plaintext after the one-time reveal.

-- The username is first.last@policyscanner.ca, derived from the broker's name
-- because it is something they recognise and can read back over the phone. It
-- is ONLY A LABEL: Radicale never sends mail to it and never checks it exists,
-- so it works whether or not those mailboxes are live.
--
-- FROZEN AT CREATION, never re-derived. This string is the collection path
-- (/USERNAME/leads/), so changing it when a broker's name changes would strand
-- their address book and force them to re-add the account on their phone.
--
-- UNIQUE is the real collision guard. Two brokers named John Smith derive the
-- same string, and RADICALE HAS NO UNIQUENESS CHECK of its own — the second
-- would silently open the first's address book. The Python side appends a
-- number for the friendly path; this constraint is what makes a bug there fail
-- loudly rather than leak one broker's leads to another.
alter table broker add column carddav_username text unique;

alter table broker add column carddav_password_hash text;

-- Not a secret, and the profile screen reads it: "Set on 3 Aug" vs "never".
alter table broker add column carddav_password_set_at timestamptz;


-- ── the queue, which is also the state ───────────────────────────────────
--
-- One table for both jobs, following meta_signal (0056): a trigger records
-- that work is DUE, and a background pass does it. Nothing calls Radicale
-- inside a transaction — an httpx timeout must never be able to fail a lead's
-- assignment.
create table carddav_contact (
  id uuid primary key default gen_random_uuid(),

  -- Which customer, and whose address book they belong in.
  lead_id uuid not null references lead(id) on delete cascade,
  broker_id uuid not null references broker(id) on delete cascade,

  -- Both the UID inside the vCard and the filename it is PUT to
  -- (<uid>.vcf), so one value locates the card in the URL, in Radicale's file
  -- tree, and inside the file itself.
  --
  -- Deterministic: 'myday-' || lead_id. If this table is ever lost, a rebuild
  -- produces the same UID and therefore the same URL, so the card is UPDATED
  -- rather than duplicated beside itself. A real recovery property that costs
  -- nothing.
  vcard_uid text not null,

  -- Three states, no delete path.
  --   'Pending'  a create or update is due
  --   'Synced'   Radicale holds exactly what we last built
  --   'Failed'   attempts exhausted; needs a person
  status text not null default 'Pending'
    check (status in ('Pending', 'Synced', 'Failed')),

  -- Hash of the exact vCard body last written successfully. THIS IS WHAT MAKES
  -- AN EDIT DETECTABLE WITHOUT A ROUND-TRIP: rebuild the card, hash it, and if
  -- it matches there is nothing to send. Without it every cycle re-PUTs every
  -- card, and every PUT bumps Radicale's sync token — so every broker's phone
  -- would redownload their whole address book on a schedule.
  content_hash text,

  -- Radicale's ETag from the last successful PUT, for If-Match on the next
  -- one. A 412 then means the card changed underneath us (a broker editing the
  -- contact on their own phone) rather than being silently clobbered.
  etag text,

  -- Incremented per attempt. Makes a stuck row visible to an admin instead of
  -- being retried forever in silence.
  attempt_count int not null default 0,
  error_message text,
  synced_at timestamptz,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- One row per (lead, broker), for good. A lead reassigned away and back finds
-- its row already here and writes nothing — which is what makes the assignment
-- trigger a single conflict-free insert.
--
-- 'Failed' excluded so a broken row can be superseded by a fresh attempt, the
-- same carve-out meta_signal's index makes and for the same reason.
create unique index idx_carddav_contact_lead_broker_active
  on carddav_contact(lead_id, broker_id)
  where status <> 'Failed';

-- The drain's exact read: everything still due, oldest first.
create index idx_carddav_contact_pending
  on carddav_contact(status, created_at)
  where status = 'Pending';

-- The health endpoint's read: cards per broker.
create index idx_carddav_contact_broker on carddav_contact(broker_id);

create trigger trg_carddav_contact_updated_at
  before update on carddav_contact
  for each row execute function set_updated_at();

alter table carddav_contact enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) reaches this
-- table, same rule as every other table in this schema.


-- ── the triggers ─────────────────────────────────────────────────────────
--
-- TRIGGERS, NOT PYTHON. The assignment path is PL/pgSQL — assign_lead (0019)
-- and the SQL-only morning reset drain (0020) — so a hook written in Python
-- would silently miss every drain-assigned lead. 0056's header records exactly
-- this trap for meta_signal; it applies here unchanged.

-- Queue a card for whichever broker now holds the lead.
--
-- ONE INSERT, NOTHING ELSE. The previous holder's row is deliberately not
-- touched: that is the whole add-only rule, and it is why this function has no
-- branch for the old broker.
create or replace function queue_carddav_contact()
returns trigger as $$
begin
  if new.assigned_broker_id is null then
    return new;
  end if;

  insert into carddav_contact (lead_id, broker_id, vcard_uid)
  values (new.id, new.assigned_broker_id, 'myday-' || new.id)
  -- Already queued or already synced for this broker: nothing to do. Matches
  -- the partial unique index above, so a re-assignment is a no-op rather than
  -- a constraint violation that would abort the whole lead update.
  on conflict (lead_id, broker_id) where status <> 'Failed' do nothing;

  return new;
end;
$$ language plpgsql;

create trigger trg_lead_carddav_assign
  after insert or update of assigned_broker_id on lead
  for each row execute function queue_carddav_contact();


-- Re-queue every card for a lead whose details changed.
--
-- EVERY ROW, not just the current holder's. After a reassignment the card sits
-- on more than one phone, and a corrected name or number should reach all of
-- them — a broker holding a stale card is exactly the failure this feature
-- exists to prevent.
--
-- content_hash is left alone on purpose: the drain rebuilds, compares, and
-- skips if nothing actually changed, so a no-op edit costs one hash rather than
-- one HTTP call.
create or replace function requeue_carddav_contact()
returns trigger as $$
begin
  update carddav_contact
     set status = 'Pending',
         attempt_count = 0,
         error_message = null
   where lead_id = new.id
     and status <> 'Pending';

  return new;
end;
$$ language plpgsql;

-- AFTER, not BEFORE: lead.phone_e164 is written by a BEFORE trigger on this
-- same update (0058), so only an AFTER trigger is guaranteed to see the
-- normalised value the card is built from.
create trigger trg_lead_carddav_content
  after update of name, phone, email on lead
  for each row execute function requeue_carddav_contact();


-- ── backfill ─────────────────────────────────────────────────────────────
--
-- Every lead already held by a broker, queued once. Without this the feature
-- would only ever cover leads assigned AFTER the migration ran, and a broker's
-- address book would start empty — which reads as "it doesn't work".
insert into carddav_contact (lead_id, broker_id, vcard_uid)
select id, assigned_broker_id, 'myday-' || id
  from lead
 where assigned_broker_id is not null
on conflict (lead_id, broker_id) where status <> 'Failed' do nothing;
