-- WhatsApp lead notifications: the broker's phone tells them a lead landed.
--
-- THE PROBLEM. The round-robin engine assigns a lead and nothing tells the
-- broker. They find out by opening MyDay and looking. A lead that arrives
-- while a broker is driving or with a customer sits unworked until they next
-- check, and speed-to-contact is the whole game on an insurance lead.
--
-- THE FIX. A one-way WhatsApp template message to the broker's own number the
-- moment a lead is assigned to them: customer name, phone, and how long they
-- have been licensed in Canada. The broker still works the lead in the CRM —
-- this only makes them aware it exists. NOT customer-facing, no inbound
-- messages, no chat relay (all three considered and rejected 2026-07-12).
--
-- SAME SHAPE AS meta_signal (0056) AND carddav_contact (0089), and for the
-- same reason: a Postgres trigger cannot make an HTTP call, so the trigger
-- records that a message is DUE and a background pass in the app sends it.
-- Calling Twilio inside the assignment transaction would let an httpx timeout
-- fail a lead's assignment, which is never an acceptable trade.
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.


-- ── consent, which is not the same thing as having a phone number ────────
--
-- broker.phone is a CRM contact detail someone typed during onboarding. It is
-- NOT consent to be messaged on WhatsApp. Meta requires explicit opt-in even
-- for an approved template, and sending without it puts the WhatsApp Business
-- account's own standing at risk — so the absence of this timestamp is a hard
-- stop, checked in the trigger below AND again before each send.
--
-- A TIMESTAMP, NOT A BOOLEAN, because the question consent has to answer
-- later is "when did they agree", which a true/false cannot.
alter table broker add column whatsapp_opted_in_at timestamptz;


-- ── the number we actually send to ───────────────────────────────────────
--
-- WhatsApp needs E.164. broker.phone is free text, so it is normalised by the
-- SAME function lead.phone_e164 uses (0058) rather than a second copy of that
-- logic in Python — one normaliser, one set of edge cases, no drift.
alter table broker add column phone_e164 text;

-- CONSENT BELONGS TO THE NUMBER, NOT TO THE BROKER ROW. A broker who changes
-- their phone has not consented for the new one, and that number may now
-- belong to a stranger — who would receive a customer's name and phone. So a
-- changed number revokes the opt-in and the broker opts in again. Re-consenting
-- is one deliberate action; messaging the wrong person is not recoverable.
--
-- Guarded on a real change (is distinct from) so that an unrelated update to
-- the broker row — a cap change, a pause — never quietly clears consent.
create or replace function set_broker_phone_e164()
returns trigger as $$
begin
  new.phone_e164 = normalize_phone_e164(new.phone);

  if tg_op = 'UPDATE' and new.phone_e164 is distinct from old.phone_e164 then
    new.whatsapp_opted_in_at = null;
  end if;

  return new;
end;
$$ language plpgsql;

create trigger trg_broker_phone_e164
  before insert or update of phone on broker
  for each row execute function set_broker_phone_e164();

-- Backfill the numbers. Deliberately does NOT set whatsapp_opted_in_at —
-- nobody has consented yet, and a backfill is not consent.
update broker set phone_e164 = normalize_phone_e164(phone);


-- ── the queue, which is also the state ───────────────────────────────────
create table whatsapp_notification (
  id uuid primary key default gen_random_uuid(),

  -- Which lead, and which broker is being told about it.
  lead_id uuid not null references lead(id) on delete cascade,
  broker_id uuid not null references broker(id) on delete cascade,

  -- FIVE STATES, BECAUSE A TWILIO SID IS NOT A DELIVERY.
  --   'Pending'    a send is due
  --   'Submitted'  Twilio accepted it (their API returns status "queued")
  --   'Delivered'  Twilio's status callback confirmed it reached the handset
  --   'Failed'     attempts spent, or a permanent error; needs a person
  --   'Cancelled'  the lead moved to another broker before we sent
  --
  -- Submitted and Delivered are separate on purpose. Marking a row 'Sent' on
  -- the API response would record success for messages that never arrived —
  -- and this feature's failures are invisible (the lead is still in the CRM,
  -- so nobody complains), which is exactly why the record has to be honest.
  status text not null default 'Pending'
    check (status in ('Pending', 'Submitted', 'Delivered', 'Failed', 'Cancelled')),

  attempt_count int not null default 0,

  -- The reason, including Twilio's ErrorCode. What makes a Failed row
  -- actionable instead of just red.
  error_message text,

  -- Twilio's message SID: the join key the status callback comes back on, and
  -- what you paste into Twilio's Messaging Logs to see one message's fate.
  message_sid text,

  submitted_at timestamptz,
  delivered_at timestamptz,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),

  -- ONE NOTIFICATION PER BROKER PER LEAD, EVER. Without this a lead that is
  -- reassigned back and forth would message the same broker repeatedly.
  unique (lead_id, broker_id)
);

create index idx_whatsapp_notification_lead on whatsapp_notification(lead_id);
create index idx_whatsapp_notification_broker on whatsapp_notification(broker_id);

-- The drain's own query: pending rows, oldest first. Partial, so the index
-- holds only what the drain looks for and stays tiny — the table is almost
-- always empty of Pending rows.
create index idx_whatsapp_notification_pending
  on whatsapp_notification(created_at)
  where status = 'Pending';

-- The status callback's lookup.
create index idx_whatsapp_notification_sid
  on whatsapp_notification(message_sid)
  where message_sid is not null;

create trigger trg_whatsapp_notification_updated_at
  before update on whatsapp_notification
  for each row execute function set_updated_at();

alter table whatsapp_notification enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) reaches this
-- table, same rule as every other table in this schema.


-- ── what queues a notification ───────────────────────────────────────────
--
-- ONE TRIGGER CATCHES EVERY ASSIGNMENT PATH. The round-robin engine
-- (assign_lead, 0019/0024), the admin paths (admin_assign_lead, 0025) and the
-- nightly reset drain (0020) all end up writing lead.assigned_broker_id, so
-- watching that column catches all of them at once. Same approach as
-- trg_lead_carddav_assign (0089) and for the same reason.
create or replace function queue_whatsapp_notification()
returns trigger as $$
declare
  v_opted_in timestamptz;
begin
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

create trigger trg_lead_whatsapp_assign
  after insert or update of assigned_broker_id on lead
  for each row execute function queue_whatsapp_notification();


-- ── no backfill, on purpose ──────────────────────────────────────────────
--
-- Every lead already assigned is old news. Messaging forty brokers about
-- leads they received days ago would be noise on the feature's first run, and
-- noise is how a notification channel gets muted. Only assignments from here
-- on notify.
