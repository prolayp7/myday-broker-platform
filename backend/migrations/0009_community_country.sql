-- Countries a community represents (e.g. Dragon -> China, Taiwan). A
-- community can map to multiple countries, and in principle a country could
-- be referenced by more than one community, so this is a many-to-many join,
-- not a single column on community.

create table country (
  id uuid primary key default gen_random_uuid(),
  name text not null,
  created_at timestamptz not null default now()
);

create unique index country_name_lower_idx on country (lower(name));

-- Top 10 countries of birth among Canada's immigrant population,
-- 2021 Census (Statistics Canada) — largest source countries first.
insert into country (name) values
  ('India'), ('Philippines'), ('China'), ('United Kingdom'), ('United States'),
  ('Pakistan'), ('Hong Kong'), ('Italy'), ('Iran'), ('Vietnam');

alter table country enable row level security;
-- Deny-all: no policies defined. Only service_role (our FastAPI backend)
-- can access this table; direct anon/authenticated Supabase access is
-- fully blocked until a specific need arises.

create table community_country (
  community_id uuid not null references community(id) on delete cascade,
  country_id uuid not null references country(id),
  primary key (community_id, country_id)
);

create index idx_community_country_country_id on community_country(country_id);

alter table community_country enable row level security;
-- Deny-all: no policies defined. Only service_role (our FastAPI backend)
-- can access this table; direct anon/authenticated Supabase access is
-- fully blocked until a specific need arises.
