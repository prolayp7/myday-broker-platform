-- Every number a customer might ring from, beyond the one the ad captured.
--
-- WHY THIS EXISTS. A customer calls from a number we do not hold — a work
-- phone, a new mobile, a spouse's handset. The broker recognises the voice and
-- files the call by hand. Without somewhere to record what they just learned,
-- the NEXT call from that same number is unmatched again, and the one after
-- that: the broker repeats the same correction forever and the system never
-- gets any better at its one job.
--
-- One row per number. The matcher searches here alongside lead.phone_e164 and
-- driver.phone_e164, so a number confirmed once by a human works from then on.
--
-- NOT A REPLACEMENT for those two columns. lead.phone is what the ad captured
-- and what the lead list displays; driver.phone is the applicant's own, on
-- their person record. This table is purely "we have also seen them on this
-- number" — additive, never authoritative, and safe to delete a row from
-- without losing anything the customer gave us.
create table lead_phone (
  id uuid primary key default gen_random_uuid(),

  lead_id uuid not null references lead(id) on delete cascade,

  -- Already normalised. Unlike lead.phone and driver.phone there is no raw
  -- form to preserve here: nobody displays this, it exists to be matched
  -- against. A number that will not normalise cannot be matched and so has no
  -- reason to be stored.
  phone_e164 text not null,

  -- Where this number came from.
  --   'confirmed' — a broker matched a call from it to this lead. The number
  --                 is only here BECAUSE a human vouched for it.
  --   'added'     — typed in directly (customer mentioned a second number on
  --                 a call, before it has ever been used).
  source text not null default 'confirmed'
    check (source in ('confirmed', 'added')),

  -- Who vouched for it. Not null: every row in this table exists because a
  -- person decided it should, and if a call ever files against the wrong
  -- customer this is who to ask.
  added_by uuid not null references broker(id),

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger trg_lead_phone_updated_at
  before update on lead_phone
  for each row execute function set_updated_at();

-- One number cannot be confirmed twice against the SAME lead. That is the only
-- true duplicate — the same fact recorded twice.
--
-- NOT globally unique, deliberately. An earlier draft was, on the reasoning
-- that one number should mean one lead. That was wrong once matching became
-- broker-scoped (see 0059): a household landline can legitimately belong to
-- two leads held by two different brokers, and each broker's calls from it
-- resolve correctly because the lookup is narrowed by broker. A global
-- constraint would have made the second broker's confirmation fail with
-- nothing wrong.
--
-- The residual case — one broker holding two leads that share a number — is
-- allowed here and surfaces as 'ambiguous' at match time, which is the honest
-- answer: those calls genuinely cannot be told apart by number alone.
create unique index idx_lead_phone_lead_number on lead_phone(lead_id, phone_e164);

-- The matcher's entry point: given a number, which leads have been seen on it.
-- Leads by number, then narrowed by broker against lead.assigned_broker_id.
create index idx_lead_phone_number on lead_phone(phone_e164);

-- The lead's own list of numbers, for showing them on the lead.
create index idx_lead_phone_lead_id on lead_phone(lead_id);

alter table lead_phone enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) reaches this
-- table, same rule as every other table in this schema.
