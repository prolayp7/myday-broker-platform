-- One row per person who logs in and works leads. Fields are grouped by
-- purpose (identity, licensing/compliance, operational, admin-block) per
-- the confirmed data model in meta-crm-architecture/src/dataModelTables.ts.
-- Enums are `check` constraints, not native Postgres enum types, so adding
-- a new allowed value later is a simple constraint change, not a type migration.
create table broker (
  id uuid primary key default gen_random_uuid(),
  group_id uuid not null references broker_group(id),

  -- Identity
  name text not null,
  email text not null unique,
  phone text not null,
  password_hash text not null,
  role text not null default 'broker' check (role in ('broker', 'manager', 'admin')),

  -- Personal (minimal identity verification only — no HR/payroll data)
  date_of_birth date,
  address text,

  -- Licensing / compliance
  ribo_license_number text,
  lines_of_authority text[] check (
    lines_of_authority <@ array['Auto', 'Travel', 'Business', 'Life']
  ),
  resident_province text not null default 'Ontario',
  license_expiry_date date,
  eo_start_date date,
  eo_expiry_date date,

  -- Operational
  is_active boolean not null default true,
  daily_cap int not null default 10,
  availability_status text not null default 'Logout'
    check (availability_status in ('Active', 'Busy', 'On Break', 'Not Available', 'Logout')),

  -- Admin-block: separate from is_active/availability_status — an admin
  -- hold distinct from the broker's own employment/presence state.
  is_blocked_by_admin boolean not null default false,
  blocked_reason text,
  blocked_at timestamptz,
  blocked_by uuid references broker(id),

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_broker_group_id on broker(group_id);
create index idx_broker_blocked_by on broker(blocked_by);

alter table broker enable row level security;
-- Deny-all: no policies defined. Only service_role (our FastAPI backend)
-- can access this table; direct anon/authenticated Supabase access is
-- fully blocked until a specific need arises.

create trigger trg_broker_updated_at
  before update on broker
  for each row
  execute function set_updated_at();
