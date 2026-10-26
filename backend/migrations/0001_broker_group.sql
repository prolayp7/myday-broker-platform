-- The controlled list of valid community codenames (Dragon, Tiger, Nature, ...).
-- Admins add new communities here first; broker_group can then only
-- reference one that already exists.
create table community (
  id uuid primary key default gen_random_uuid(),
  name text not null unique,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

alter table community enable row level security;
-- Deny-all: no policies defined. Only service_role (our FastAPI backend)
-- can access this table; direct anon/authenticated Supabase access is
-- fully blocked until a specific need arises.

create trigger trg_community_updated_at
  before update on community
  for each row
  execute function set_updated_at();

insert into community (name) values ('Dragon'), ('Tiger'), ('Nature');

create table broker_group (
  id uuid primary key default gen_random_uuid(),
  community_id uuid not null references community(id),
  group_number int not null,
  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now(),
  unique (community_id, group_number)
);

create index idx_broker_group_community_id on broker_group(community_id);

-- Auto-assigns the next group_number for a community if the caller doesn't
-- supply one (e.g. inserting a second group for the same community just
-- needs community_id — group_number becomes 2 automatically).
create or replace function set_broker_group_number()
returns trigger as $$
begin
  if new.group_number is null then
    select coalesce(max(group_number), 0) + 1
    into new.group_number
    from broker_group
    where community_id = new.community_id;
  end if;
  return new;
end;
$$ language plpgsql;

create trigger trg_broker_group_number
  before insert on broker_group
  for each row
  execute function set_broker_group_number();

create trigger trg_broker_group_updated_at
  before update on broker_group
  for each row
  execute function set_updated_at();

alter table broker_group enable row level security;
-- Deny-all: no policies defined. Only service_role (our FastAPI backend)
-- can access this table; direct anon/authenticated Supabase access is
-- fully blocked until a specific need arises.
