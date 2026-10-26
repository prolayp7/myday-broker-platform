-- The raw record of one Zoom Phone call. Written from Zoom's webhook, before
-- anything tries to match it to a lead — same "store first, interpret second"
-- rule as webhook_event.
--
-- Zoom fires TWO webhooks per call (one for the caller, one for the callee).
-- zoom_call_id is Zoom's own call_id, unique here, so the second webhook for
-- the same call updates this row instead of creating a duplicate.
--
-- NO lead_id COLUMN, deliberately. An earlier draft had one alongside
-- contact_log.lead_id — the same fact in two places. Re-attaching a call to a
-- different lead would then have to update both, and any path updating only
-- one would leave the call record and the customer's timeline disagreeing
-- about who was rung, with nothing to say which was right. contact_log is the
-- single answer: a call belongs to whichever lead its contact_log row points
-- at, and match_status here says whether that row exists yet.
create table zoom_call (
  id uuid primary key default gen_random_uuid(),

  -- Zoom's own identifier for this call. Unique so the caller/callee webhook
  -- pair (and any Zoom redelivery) can never create two rows for one call.
  zoom_call_id text not null unique,

  -- The two numbers AS ZOOM SENT THEM. Kept raw because this table is Zoom's
  -- own record and a raw value is what makes a bad match diagnosable later.
  caller_number text not null,
  callee_number text not null,

  -- Both numbers in E.164, written by the trigger below. Both sides are
  -- normalised because the customer is the CALLER when they ring in and the
  -- CALLEE when a broker dials out — matching reads whichever one is not ours.
  --
  -- lead.phone_e164 is normalised (migration 0058), so comparing it against a
  -- raw Zoom string would never equal anything. Stored rather than normalised
  -- at lookup time because a query that wraps a column in a function cannot
  -- use an ordinary index on it.
  caller_number_e164 text,
  callee_number_e164 text,

  direction text not null check (direction in ('inbound', 'outbound')),

  -- Which broker was on this call — whoever dialled out, or whoever picked up.
  -- Resolved from Zoom's own user id on the webhook via broker.zoom_user_id
  -- (0062).
  --
  -- THE COLUMN THAT GIVES AN UNMATCHED CALL AN OWNER. Without it the queue of
  -- calls needing a human is one shared pile that everybody can see and nobody
  -- is responsible for — and a pile like that is never emptied. With it, an
  -- unmatched call sits in the queue of the broker who actually had the
  -- conversation, which is also the only person who knows who they were
  -- talking to.
  --
  -- Nullable: a call answered by someone with no zoom_user_id set, or by an
  -- auto-attendant, has no broker behind it. Those stay in an admin-level
  -- queue rather than being forced onto an arbitrary person.
  broker_id uuid references broker(id),

  -- How this call found its lead. Take the customer's number — the callee when
  -- we dialled out, the caller when they rang in — and look for a lead holding
  -- it. If more than one comes back, use the broker on the call (broker_id
  -- above) to choose between them.
  --
  -- BROKER IS A TIEBREAKER, NOT A FILTER. That distinction is the whole rule:
  --
  --   one candidate lead    -> file it, whoever holds it
  --   several candidates    -> keep only the ones this broker holds
  --   still several, or none -> queue
  --
  -- Filtering by broker up front was the first design and it broke three
  -- ordinary situations. A lead sitting unassigned in the pool (0019 leaves
  -- assigned_broker_id null whenever every broker is at their daily cap) is
  -- held by nobody, so no call could ever match it. An admin or manager holds
  -- no leads at all, so every call they make would be unmatched. And a
  -- customer who rings the broker they have already spoken to three times,
  -- after the lead was reassigned to someone else, would fail to match the
  -- very lead the conversation is about.
  --
  -- The privacy case that motivated the broker check still holds, because it
  -- only ever mattered when there was a CONFLICT. Two leads sharing a
  -- household landline, held by two different brokers: the tiebreaker keeps
  -- each broker's recording and transcript on their own lead. A single
  -- unambiguous match was never a privacy risk — there was only one customer
  -- it could have been.
  --
  -- The lookup searches THREE places, all normalised:
  --   lead.phone_e164     — what the ad captured (0058).
  --   driver.phone_e164   — what the broker confirmed on the call, and the
  --                         number that actually reaches the person (0058).
  --                         A hit resolves to that driver's lead.
  --   lead_phone.phone_e164 — any OTHER number this customer has been seen
  --                         on, put there by a broker who matched a call from
  --                         it by hand (0063). This is what stops the same
  --                         second number needing the same manual fix on
  --                         every future call.
  --
  --   'matched'    — the search settled on exactly ONE lead, either because
  --                  only one had that number or because the broker
  --                  tiebreaker resolved it. Filed automatically.
  --   'unmatched'  — no lead anywhere has that number. A wrong number, a
  --                  supplier, a personal call. Ordinary, not an error.
  --   'ambiguous'  — several leads have it and the tiebreaker could not
  --                  separate them: one broker holding two leads for the same
  --                  household, or a customer who submitted the form twice and
  --                  landed with them both times. Deliberately NOT auto-filed
  --                  — guessing puts a real conversation on the wrong
  --                  customer's record, and nobody finds out until it matters.
  --
  -- One lead reached by BOTH routes is still 'matched', not ambiguous: the
  -- lead and its own applicant sharing a number is the normal case, not a
  -- conflict. Ambiguity is about distinct LEADS, never distinct rows.
  --
  -- unmatched and ambiguous are a QUEUE, not a discard. The call is still
  -- fully recorded here; a broker attaches it to the right lead by hand.
  match_status text not null default 'unmatched'
    check (match_status in ('matched', 'unmatched', 'ambiguous')),

  -- Who filed this call against a lead by hand, and when. Null on the ordinary
  -- case — the matcher found one lead and nobody had to intervene.
  --
  -- Worth recording because a resolved call looks identical to an
  -- automatically matched one once match_status flips to 'matched': the
  -- ambiguity that existed is erased by the fix. If a call later turns out to
  -- be on the wrong customer's record, these two columns are the difference
  -- between "the matcher has a bug" and "someone chose this, ask them why".
  resolved_by uuid references broker(id),
  resolved_at timestamptz,

  -- When the call happened, not when the webhook arrived.
  occurred_at timestamptz not null,
  -- Null when the call never connected (no answer, busy).
  duration_seconds int,
  -- Zoom's own outcome, e.g. 'answered', 'no_answer', 'busy'. Kept as Zoom's
  -- raw text rather than mapped to our contact_log.outcome enum here — the
  -- mapping is a service-layer decision, not a schema one.
  call_result text,

  -- Where Zoom's recording can be fetched from, if this call was recorded.
  -- Zoom's own download_url is time-limited, which is why the transcript is
  -- pulled on demand (broker clicks "Get Transcript") rather than eagerly for
  -- every call — by the time someone asks, the row above is what still exists
  -- even if this URL has since expired.
  recording_url text,

  -- Zoom's webhook exactly as it arrived. The same reasoning as
  -- webhook_event.raw_payload for Meta: the columns above are our reading of
  -- the payload, and a reading can be wrong. When a call files against the
  -- wrong lead, or a field turns out to mean something other than we assumed,
  -- this is the only way to find out what Zoom actually said — and to re-run
  -- the matching afterwards instead of asking Zoom for history it may no
  -- longer have.
  raw_payload jsonb,

  created_at timestamptz not null default now(),
  -- This row IS updated after insert: Zoom's second webhook for the same call
  -- fills in what the first one lacked, and the recording URL arrives later
  -- still. So unlike an append-only log, updated_at earns its place here.
  updated_at timestamptz not null default now()
);

create trigger trg_zoom_call_updated_at
  before update on zoom_call
  for each row execute function set_updated_at();

-- Normalise both numbers on the way in, using the same function lead.phone_e164
-- uses (0058). In the database rather than the webhook handler so the two sides
-- of the comparison can never drift apart — a matcher normalising one side with
-- slightly different rules than the other is a bug that looks like "some calls
-- just don't match".
create or replace function set_zoom_call_e164()
returns trigger as $$
begin
  new.caller_number_e164 = normalize_phone_e164(new.caller_number);
  new.callee_number_e164 = normalize_phone_e164(new.callee_number);
  return new;
end;
$$ language plpgsql;

create trigger trg_zoom_call_e164
  before insert or update of caller_number, callee_number on zoom_call
  for each row execute function set_zoom_call_e164();

-- Matching reads whichever side is the customer: the caller on an inbound
-- call, the callee on an outbound one. Both indexed, since either can be the
-- one searched.
create index idx_zoom_call_caller_e164 on zoom_call(caller_number_e164)
  where caller_number_e164 is not null;
create index idx_zoom_call_callee_e164 on zoom_call(callee_number_e164)
  where callee_number_e164 is not null;

-- THE UNMATCHED QUEUE — the calls a broker has to place by hand.
--
-- Led by broker_id because that is how the screen reads it: "the calls I made
-- that aren't filed yet", not "everyone's". An admin sweep of the whole queue
-- still uses this index, just scanning more of it.
--
-- Partial on match_status, so it holds only the handful actually needing a
-- human — every cleanly matched call stays out of it however many accumulate.
-- This partial index IS the queue; a separate table for unmatched calls would
-- hold the same columns for the same rows, and moving a row between tables on
-- match is how rows get lost.
create index idx_zoom_call_unresolved on zoom_call(broker_id, occurred_at desc)
  where match_status in ('unmatched', 'ambiguous');

-- No index for "this lead's calls": that read starts from contact_log, which
-- is already indexed on (lead_id, occurred_at desc) and joins to here by
-- primary key.

alter table zoom_call enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) reaches this
-- table, same rule as every other table in this schema.
