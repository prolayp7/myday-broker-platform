-- Every MVR a broker pulls, kept as the MTO compliance log.
--
-- WHY. The Authorized Requester Agreement makes the brokerage keep a "Records
-- of Licensed Information" register: one line per driver abstract pulled, with
-- the date, the reason, what was searched, the ARIS order number, who did it,
-- and when the information must be destroyed. Brokers kept it by hand in an
-- Excel template. A broker now drops the MVR PDF on the MVR Log page and the
-- row is read out of it (services/mvr_parse.py).
--
-- THE PDF IS NOT KEPT. Only the register line is. Holding a second copy of
-- someone's driving record is the thing the destruction date exists to limit.
--
-- WHAT IS NOT STORED, and why:
--   use completed  = requested_on, by the brokerage's own rule
--   destruction    = requested_on + 1 year
--   masked licence = derived from licence_number
-- All three are fixed functions of a stored column. Storing them would let a
-- corrected request date leave a stale destruction date behind.
--
-- completed_by IS the broker's name AS TEXT, alongside broker_id. The register
-- is a record of who did it at the time; a later rename must not rewrite it.

create table mvr_pull (
  id uuid primary key default gen_random_uuid(),
  broker_id uuid not null references broker(id),
  completed_by text not null,
  driver_name text,
  licence_number text not null,
  requested_on date not null,
  -- The MVR's "Receipt Number". Unique, so the same PDF dropped twice is
  -- refused rather than counted twice in the register.
  aris_order_number text not null unique,
  business_reason text not null default 'new_business'
    check (business_reason in ('new_business', 'adding_driver')),
  info_retrieved text not null default 'mvr'
    check (info_retrieved in ('mvr', 'predictor')),
  created_at timestamptz not null default now()
);

-- The page reads one month at a time, optionally for one broker.
create index idx_mvr_pull_month on mvr_pull (requested_on, broker_id);

alter table mvr_pull enable row level security;


-- The head-office report's password. ONE ROW, enforced by the boolean key:
-- there is one report and one password for it.
--
-- updated_at doubles as the password's VERSION. An unlock token carries it,
-- and a token whose version no longer matches is refused — so changing the
-- password locks out everyone holding the old one, immediately.
create table mvr_report_settings (
  id boolean primary key default true check (id),
  password_hash text not null,
  updated_at timestamptz not null default now()
);

alter table mvr_report_settings enable row level security;

-- The initial password, agreed with the brokerage 2026-10-20. Only the bcrypt
-- hash is here; an admin changes it from the MVR Log page.
insert into mvr_report_settings (id, password_hash)
values (true, '$2b$12$5mlWPIbO7.gdeVtKw2FLbuo28W95pLF9rhJp4fQeDfebvruPE4D2q');
