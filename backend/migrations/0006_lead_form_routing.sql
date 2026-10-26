-- Routing moves from campaign to form. Per industry research (HubSpot,
-- Zapier, and dedicated Meta Lead Ads integrations all confirmed to route
-- this way): Meta's real leadgen webhook only ever carries leadgen_id,
-- form_id, adgroup_id, ad_id — never campaign_id directly. Getting campaign
-- context requires a SEPARATE, non-guaranteed follow-up API call. Routing
-- must never block on that call, so campaign can no longer be the routing
-- key. form_id is present in every webhook payload with zero extra calls,
-- so it becomes the actual routing anchor. campaign stays for
-- reporting/attribution only.
alter table campaign drop column group_id;

-- One row per real Meta/TikTok lead form the admin has mapped. Multiple
-- forms can point at the same group (no need to split), or different form
-- variants (e.g. a split test) can point at different groups — this is the
-- flexibility campaign-level routing couldn't offer, since campaign.group_id
-- forced every ad/ad_group under one campaign to share a single group.
create table lead_form (
  id uuid primary key default gen_random_uuid(),
  -- Meta/TikTok's own form_id — not scoped to a single ad_group/ad row,
  -- since the same real form can be reused across multiple ads. Unique so
  -- the ingestion webhook can look up "do we already have a mapping for
  -- this form_id" with a single, unambiguous match.
  platform_form_id text not null unique,
  group_id uuid not null references broker_group(id),
  -- Free-text admin label so the mapping list is readable without having
  -- to recognize a raw platform form_id (e.g. "Auto Quick Quote (Winter)").
  name text not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_lead_form_group_id on lead_form(group_id);

alter table lead_form enable row level security;
-- Deny-all: no policies defined. Only service_role (our FastAPI backend)
-- can access this table; direct anon/authenticated Supabase access is
-- fully blocked until a specific need arises.

create trigger trg_lead_form_updated_at
  before update on lead_form
  for each row
  execute function set_updated_at();
