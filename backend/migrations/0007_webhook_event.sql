-- Raw event log for incoming webhooks (Meta today, TikTok later). This is
-- the production-grade "receive and store" layer described by real webhook
-- best practice (Stripe's own docs recommend the same pattern): acknowledge
-- fast, persist the raw payload immediately and atomically, process
-- downstream separately. Deliberately NOT the same table as `lead` — this
-- table's job is dedup + audit trail, not business logic. Full lead
-- ingestion (Graph API enrichment, campaign/lead_form lookup, routing) reads
-- FROM this table later; it does not write here.
create table webhook_event (
  id uuid primary key default gen_random_uuid(),
  source text not null check (source in ('meta', 'tiktok')),
  -- Meta's leadgen_id (or an equivalent stable id from another platform).
  -- Unique so a redelivered webhook (Meta explicitly documents at-least-once
  -- delivery with retries) is a no-op on the second insert, not a duplicate
  -- row — the ON CONFLICT DO NOTHING in the app layer relies on this
  -- constraint, not on an app-level "check then insert" (which would race).
  dedup_key text not null unique,
  raw_payload jsonb not null,
  signature_valid boolean not null,
  -- Downstream processing status. 'received' = stored only, nothing else
  -- done yet — correct today, since there's no lead table to process into.
  status text not null default 'received' check (status in ('received', 'processed', 'failed')),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_webhook_event_status on webhook_event(status);

alter table webhook_event enable row level security;
-- Deny-all: no policies defined. Only service_role (our FastAPI backend)
-- can access this table; direct anon/authenticated Supabase access is
-- fully blocked until a specific need arises.

create trigger trg_webhook_event_updated_at
  before update on webhook_event
  for each row
  execute function set_updated_at();
