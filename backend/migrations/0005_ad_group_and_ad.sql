-- Child of campaign (one campaign -> many ad groups). Renamed from an
-- earlier "ad_set": Meta's real webhook field is "adgroup_id" (Meta's UI
-- says "Ad Set", its API does not), and TikTok's API uses "adgroup_id" for
-- the same structural level — one shared table/column name matching both
-- platforms' actual APIs, not a UI-only label.
create table ad_group (
  id uuid primary key default gen_random_uuid(),
  campaign_id uuid not null references campaign(id),
  -- Unique (not just indexed): the webhook ingestion path looks up an
  -- existing ad_group by this platform id BEFORE creating a new one ("first
  -- lead seen from that ad_group" per the operational-flow diagram) — a
  -- unique constraint makes that find-or-create safe against duplicate/retry
  -- webhook deliveries, not just app-level discipline. Nullable + unique is
  -- fine in Postgres: multiple NULLs are allowed to coexist.
  platform_ad_group_id text unique,
  name text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_ad_group_campaign_id on ad_group(campaign_id);

alter table ad_group enable row level security;
-- Deny-all: no policies defined. Only service_role (our FastAPI backend)
-- can access this table; direct anon/authenticated Supabase access is
-- fully blocked until a specific need arises.

create trigger trg_ad_group_updated_at
  before update on ad_group
  for each row
  execute function set_updated_at();

-- Child of ad_group (one ad_group -> many ads). This is the level a lead's
-- originating webhook event actually references.
create table ad (
  id uuid primary key default gen_random_uuid(),
  -- Nullable — verified against Meta's real leadgen webhook: it only sends
  -- leadgen_id, form_id, adgroup_id, ad_id, created_time. Full campaign/
  -- ad_group context requires a SEPARATE follow-up API call, so this can't
  -- be guaranteed at the moment of ingestion — filled in by an enrichment
  -- step shortly after.
  ad_group_id uuid references ad_group(id),
  -- Unique for the same find-or-create reason as ad_group.platform_ad_group_id.
  platform_ad_id text unique,
  platform_leadgen_id text,
  form_id text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_ad_ad_group_id on ad(ad_group_id);

alter table ad enable row level security;
-- Deny-all: no policies defined. Only service_role (our FastAPI backend)
-- can access this table; direct anon/authenticated Supabase access is
-- fully blocked until a specific need arises.

create trigger trg_ad_updated_at
  before update on ad
  for each row
  execute function set_updated_at();
