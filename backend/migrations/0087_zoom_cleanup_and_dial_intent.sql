-- Zoom: known numbers only, and an explicit intent for every outbound call.
--
-- TWO CHANGES, one migration. Nothing is in production, so there is no reason
-- to stage them.
--
-- ── 1. AMBIGUOUS goes away ──────────────────────────────────────────────
--
-- match_status had three values. 'ambiguous' meant "several leads hold this
-- number and the broker tiebreaker could not narrow it to one" — a state that
-- was written, indexed, given two resolution columns, and then READ BY
-- NOTHING. repositories/zoom_call.py::list_unresolved_for_broker is called by
-- no route and no screen; resolved_by and resolved_at have never held a value.
--
-- To a broker, 'ambiguous' and 'unmatched' are the same thing: the call is on
-- no timeline. Keeping two names for one outcome only invites someone to build
-- the queue screen the columns imply, and the decision is not to.
--
-- The BROKER TIEBREAKER ITSELF STAYS (services/zoom_matching.py). What changes
-- is only what happens when it cannot narrow: unmatched, not a third state.
--
-- ── 2. dial_intent ──────────────────────────────────────────────────────
--
-- When a broker presses Call inside a lead, they have stated which lead the
-- call is about. Today that fact is thrown away and the webhook is left to
-- infer it from the phone number — which fails whenever the number is not on
-- the lead yet (a customer's second number, typed into the new keypad) and
-- also whenever it is on TWO leads the same broker holds.
--
-- One row per outbound dial, consumed by the call it belongs to.

begin;

-- ── AMBIGUOUS -> UNMATCHED ──────────────────────────────────────────────

-- FIRST, before the constraint changes: the new CHECK would reject any
-- surviving 'ambiguous' row and fail the whole migration. Live data has none
-- (7 rows, all 'matched'), so this is expected to be a no-op — it runs because
-- "expected" is not "guaranteed".
update zoom_call set match_status = 'unmatched' where match_status = 'ambiguous';

-- The index that existed to find them.
drop index if exists idx_zoom_call_unresolved;

-- The inline CHECK from 0059 was auto-named by Postgres. Dropped by that
-- conventional name; `if exists` so a differently-named one does not fail the
-- migration, and the add below is what actually enforces the new rule.
alter table zoom_call drop constraint if exists zoom_call_match_status_check;

alter table zoom_call
  add constraint zoom_call_match_status_check
  check (match_status in ('matched', 'unmatched'));

-- The resolution columns. Never written, and there is no manual-resolution
-- flow to write them: a call that cannot be placed is left unmatched, and the
-- broker rings from the lead instead.
alter table zoom_call drop column if exists resolved_by;
alter table zoom_call drop column if exists resolved_at;

-- ── dial_intent ─────────────────────────────────────────────────────────

-- What the broker SAID, recorded at the moment they pressed Call — as opposed
-- to what the phone number implies, which is what matching guesses afterwards.
--
-- A SEPARATE TABLE, not a column on zoom_call, because this row exists BEFORE
-- Zoom has told us anything: there is no zoom_call row yet to hang it on.
--
-- Short-lived by design. A row is consumed by the call it belongs to and swept
-- away after an hour; nothing reads it later, so it cannot drift out of step
-- with anything.
create table dial_intent (
  id uuid primary key default gen_random_uuid(),

  lead_id uuid not null references lead(id) on delete cascade,

  -- Who dialled. Half the lookup key: two brokers ringing the same customer at
  -- the same time must not take each other's intents.
  broker_id uuid not null references broker(id),

  -- Already normalised, by the same normalize_phone_e164 the triggers use
  -- (0058). Nobody displays this — it exists to be compared against
  -- zoom_call.callee_number_e164, and a number that will not normalise could
  -- never match a call, so the endpoint refuses it rather than storing it.
  phone_e164 text not null,

  -- Set when a call claims this intent. NOT a delete: the row stays until the
  -- sweep clears it, so a redelivery can tell "already used" from "never
  -- existed" — and those two need different handling in ingestion.
  consumed_at timestamptz,

  created_at timestamptz not null default now()
);

-- The ingestion lookup. Partial on consumed_at because a consumed intent is
-- never a candidate again, so it has no business in the index.
--
-- created_at desc: several intents can match the window when a broker redials,
-- and the most recent one before the call is the one it belongs to.
create index idx_dial_intent_lookup
  on dial_intent(broker_id, phone_e164, created_at desc)
  where consumed_at is null;

-- The sweep's query.
create index idx_dial_intent_created_at on dial_intent(created_at);

alter table dial_intent enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) reaches this
-- table, same rule as every other table in this schema.

commit;
