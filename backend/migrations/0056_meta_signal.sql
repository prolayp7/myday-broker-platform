-- The notebook: one row per conversion event we intend to tell Meta about
-- (Lead, CompleteRegistration, Purchase). This is what turns a broker's
-- pipeline work into ad-targeting signal, so Meta optimises for people who
-- actually BUY rather than people who merely fill in a form.
--
-- Rows are inserted by DATABASE TRIGGERS (0057), not by application code —
-- 'Lead' from an insert on lead_delivery, the other two from an insert on
-- lead_stage_history. That is deliberate: assign_lead is a SQL function
-- (migrations/0019) and leads are also handed out by the SQL-only morning
-- reset drain (migrations/0020) — a trigger written in Python would silently
-- miss every drain-assigned lead.
--
-- The triggers only ever WRITE A ROW here. Nothing calls Meta inside that
-- same transaction — an httpx timeout to an external API must never be able
-- to fail a lead's creation or a broker's stage change. Sending is a
-- separate pass (built later) that reads status = 'Pending' rows.
--
-- NO CUSTOMER PII IS STORED HERE. The sender reaches the customer through
-- lead_id at send time (lead.email/phone/platform_leadgen_id, the principal
-- driver's date_of_birth/gender/city/province/postal_code,
-- premium_history.total_new_premium). The only snapshot kept is `payload`,
-- and only AFTER hashing — so this table never holds a second copy of
-- anyone's personal data.
create table meta_signal (
  id uuid primary key default gen_random_uuid(),

  -- Which lead this signal is about, and how the sender finds everything
  -- else it needs at send time.
  lead_id uuid not null references lead(id) on delete cascade,

  -- Which ad platform this signal was sent to. Only Meta is built today;
  -- TikTok is in the enum because the data model always planned for both.
  platform text not null check (platform in ('Meta', 'TikTok')),

  -- Meta STANDARD event names, not custom ones.
  --   'Lead'                 -> a real broker now holds it (fires from
  --                            lead_delivery)
  --   'CompleteRegistration' -> the qualified-lead signal, fired at stage
  --                            'Quote Done' — the highest-leverage event of
  --                            the three: a quote proves the person was real
  --                            and reachable, and it fires far more often
  --                            than a sale, so Meta's model learns faster.
  --   'Purchase'             -> fired once BOTH stage = 'Sold' AND the
  --                            broker has explicitly confirmed the sale are
  --                            true, in either order (see 0057). A broker
  --                            sale is the real transaction for us — we do
  --                            not wait on the insurer's own paperwork
  --                            (stage 'Issued'), which can lag by weeks.
  event_name text not null check (event_name in ('Lead', 'CompleteRegistration', 'Purchase')),

  -- Which broker's action caused this signal, taken from
  -- lead_stage_history.broker_id (whoever moved the stage) or the sale
  -- confirmation action. Null for 'Lead', which the round-robin engine
  -- fires with no human actor.
  triggered_by uuid references broker(id),

  -- Exactly what was POSTed to Meta, stored AFTER hashing. Kept because a
  -- hashing or normalisation mistake is otherwise INVISIBLE: Meta accepts
  -- the event, matches nobody, and still reports success. This column plus
  -- Meta's own Event Match Quality diagnostics (Events Manager) are the only
  -- way to find that class of bug. Null until a send is attempted.
  payload jsonb,

  -- 'Pending' the instant the trigger writes the row — it records that a
  -- signal is DUE, it does not send it. The sender (built later) moves this
  -- to 'Sent' or 'Failed'.
  status text not null default 'Pending' check (status in ('Pending', 'Sent', 'Failed')),

  -- Meta's own rejection text, if status = 'Failed'. The failure most worth
  -- watching for: event_time older than 7 days, which Meta refuses outright
  -- — that means a lead whose stage was marked too late for its signal to
  -- ever count.
  error_message text,

  -- When the POST actually ran. Null while Pending.
  sent_at timestamptz,

  -- Incremented on every send attempt. Makes a stuck row visible to an
  -- admin instead of retried forever in silence.
  attempt_count int not null default 0,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- THE GUARD THAT MATTERS. A broker moving Quote Done -> Pending -> Quote
-- Done would otherwise fire CompleteRegistration twice, and Meta would count
-- two qualified leads from one customer — corrupting the exact signal this
-- table exists to send. Enforced by the database because no
-- application-level check can win that race. 'Failed' rows are excluded so a
-- genuine failure can still be retried, while a success can never be
-- duplicated.
create unique index idx_meta_signal_lead_event_active
  on meta_signal(lead_id, event_name)
  where status <> 'Failed';

-- "Every Pending/Failed row due for a send attempt" — the sender's own read
-- pattern.
create index idx_meta_signal_status on meta_signal(status);
create index idx_meta_signal_lead_id on meta_signal(lead_id);

alter table meta_signal enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) reaches
-- this table, same rule as every other table in this schema.
