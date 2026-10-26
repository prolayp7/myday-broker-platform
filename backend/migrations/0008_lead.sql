-- The single common table every lead lands in, regardless of source (Meta /
-- TikTok / Website / organic). Core routing + pipeline fields only for now —
-- billing fields (company_cost, broker_charge, payment_status, etc.) are
-- deliberately deferred to their own migration, together with pricing_rule,
-- once that table exists. This keeps this migration testable end-to-end
-- (webhook -> lead -> pool -> claim) before layering billing on top.
create table lead (
  id uuid primary key default gen_random_uuid(),
  -- Nullable: a truly organic lead (SEO, generic website form — no ad at
  -- all) has no ad/ad_group hierarchy, attaches straight to a group with
  -- no ad reference.
  ad_id uuid references ad(id),
  -- The field the routing/pooling logic actually reads to decide which
  -- brokers can see this lead. NOT NULL: every lead has a routing target,
  -- either via lead_form.group_id (ad-driven, form_id matched) or
  -- website_lead_rotation (truly organic, no form_id match) — the ingestion
  -- code decides which source applies, but a lead is never created without
  -- resolving one.
  group_id uuid not null references broker_group(id),
  -- WHO IS CURRENTLY WORKING this lead. Null while unclaimed in the pool;
  -- set on first claim; moved on every reassignment. The pool itself is not
  -- a separate table — it's just this query: every lead where
  -- assigned_broker_id is null AND group_id = the broker's own group.
  assigned_broker_id uuid references broker(id),
  stage text not null default 'New' check (
    stage in (
      'New', 'Quote Ready', 'Under Review', 'Awaiting Decision',
      'Sold Pending Docs', 'Policy Submitted', 'Issued', 'Lost'
    )
  ),
  -- Fixed core fields, always present regardless of source.
  name text not null,
  phone text not null,
  email text not null,
  -- Whatever extra questions THIS form asked (renewal date, vehicle type,
  -- etc.) — flexible column for data that varies unpredictably per form,
  -- instead of a fixed column per possible question.
  extra_data jsonb,
  -- Whether auto is bundled with a property policy. 'Standalone' = auto
  -- only. Any other value means a whole separate property domain applies
  -- to this lead (its own future tables) — not built here.
  combined_policy_type text check (
    combined_policy_type in ('Standalone', 'Homeowners', 'Tenants', 'Condo')
  ),
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_lead_ad_id on lead(ad_id);
create index idx_lead_group_id on lead(group_id);
create index idx_lead_assigned_broker_id on lead(assigned_broker_id);
-- The pool query (group_id = X, assigned_broker_id is null) is the single
-- most important read pattern here — index the pair so it stays cheap as
-- lead volume grows.
create index idx_lead_pool on lead(group_id, assigned_broker_id);

alter table lead enable row level security;
-- Deny-all: no policies defined. Only service_role (our FastAPI backend)
-- can access this table; direct anon/authenticated Supabase access is
-- fully blocked until a specific need arises.

create trigger trg_lead_updated_at
  before update on lead
  for each row
  execute function set_updated_at();
