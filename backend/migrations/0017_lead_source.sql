-- Which intake channel this lead actually came in on. A broker calling a
-- customer needs to know this ("we connected via our website" vs "you
-- messaged our Facebook ad"), and it's the foundation for per-channel
-- reporting later. Meta is the only channel actually wired today (see
-- services/lead_ingestion.py) — website and tiktok are real values the
-- column already accepts, so adding those intake paths later needs no
-- schema change, just a new ingestion function that sets source='website'
-- or 'tiktok'.
--
-- Deliberately NOT split into facebook/instagram: Meta's Lead Ads webhook,
-- the GET /{leadgen_id} lead object, and the ad/ad-set objects all lack any
-- per-lead placement field — an ad running on both placements (Meta's
-- default) produces identical data for every lead regardless of which
-- platform the person actually used. Only aggregate Ads Manager reporting
-- breaks down by publisher_platform, never a single lead. Confirmed against
-- Meta's own API docs and our real stored webhook_event payloads
-- (2026-07-12) before choosing 'meta' as one value rather than guessing at
-- a facebook/instagram split we can't actually deliver.
--
-- not null, no default relied upon by real inserts: every ingestion path
-- must decide its own source explicitly (see lead_ingestion.py) rather than
-- silently falling through to a guess. The default only backfills any
-- pre-existing rows at migration time.
alter table lead add column source text not null default 'meta' check (
  source in ('meta', 'website', 'tiktok')
);

alter table lead alter column source drop default;

create index idx_lead_source on lead(source);
