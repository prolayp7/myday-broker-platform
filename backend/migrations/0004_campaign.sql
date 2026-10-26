-- The controlled list of ad platforms a campaign can run on (Meta, TikTok,
-- Website). A lookup table, not a check constraint, because this list is
-- driven by external ad-platform integrations that can grow over time
-- (e.g. Google Ads, LinkedIn) — adding a new one should be a plain insert,
-- not a schema migration. Same pattern as `community`.
create table ad_platform (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table ad_platform enable row level security;
-- Deny-all: no policies defined. Only service_role (our FastAPI backend)
-- can access this table; direct anon/authenticated Supabase access is
-- fully blocked until a specific need arises.

create trigger trg_ad_platform_updated_at
  before update on ad_platform
  for each row
  execute function set_updated_at();

insert into ad_platform (name) values ('meta'), ('tiktok'), ('website');

-- Top of the real Meta/TikTok ad hierarchy (Campaign -> Ad Group -> Ad).
-- Root of the ad-side chain — every ad_group, ad, and lead eventually
-- traces back to one campaign row.
create table campaign (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  source_id uuid not null references ad_platform(id),
  -- Fixed business taxonomy (the 4 real lines of business we sell), NOT a
  -- lookup table: this vocabulary must stay identical to
  -- broker.lines_of_authority for broker-to-campaign matching to work, and
  -- it does not change often — a check constraint is the right tool here.
  product text not null check (product in ('Auto', 'Travel', 'Business', 'Life')),
  group_id uuid not null references broker_group(id),
  -- Meta's and TikTok's real ad-platform APIs both call this level
  -- "Campaign" — nullable because a Website campaign has no platform ID.
  platform_campaign_id text,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_campaign_source_id on campaign(source_id);
create index idx_campaign_group_id on campaign(group_id);

alter table campaign enable row level security;
-- Deny-all: no policies defined. Only service_role (our FastAPI backend)
-- can access this table; direct anon/authenticated Supabase access is
-- fully blocked until a specific need arises.

create trigger trg_campaign_updated_at
  before update on campaign
  for each row
  execute function set_updated_at();
