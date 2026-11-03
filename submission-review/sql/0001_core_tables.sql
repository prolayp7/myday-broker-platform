-- Submission Review: the file tables, the application and the MVR.
-- Run on MyDay STAGING only. Everything sits in its own `review` schema so
-- nothing can collide with MyDay's tables (MyDay already has a `broker`).
--
-- Rules for the read-in tables:
--   * A Yes/No is a boolean and null means left blank. Most checks are
--     "blank is an error", so blank must never look like No.
--   * A year and month with no day stay in two columns, never a made-up date.
--   * Claude's full answer stays in document.raw. Columns exist only for
--     what a check uses, or what the form must keep.

create schema review;

create sequence review.submission_reference_seq start 1001;

-- 1. The file and its emails ------------------------------------------------

create table review.broker (
  id          uuid primary key default gen_random_uuid(),
  email       text not null unique check (email = lower(email)),
  name        text,
  created_at  timestamptz not null default now()
);

create table review.submission (
  id              uuid primary key default gen_random_uuid(),
  reference       text not null unique
                    default 'SR-' || nextval('review.submission_reference_seq'),
  broker_id       uuid not null references review.broker(id),
  -- Optional on purpose: the review works from documents alone. A missing or
  -- deleted lead must never stop or remove a review.
  lead_id         uuid references public.lead(id) on delete set null,
  customer_name   text,
  effective_date  date,
  -- No check constraint until the status flow is final.
  status          text not null default 'waiting_for_documents',
  created_at      timestamptz not null default now(),
  updated_at      timestamptz not null default now()
);

-- An email is saved before it is matched, so submission_id starts blank.
create table review.email (
  id                  uuid primary key default gen_random_uuid(),
  mailbox_message_id  text not null unique,   -- re-polling never duplicates
  thread_id           text,
  direction           text not null check (direction in ('in','out')),
  from_address        text not null,
  subject             text,
  occurred_at         timestamptz not null,
  submission_id       uuid references review.submission(id) on delete set null,
  created_at          timestamptz not null default now()
);

create table review.document (
  id             uuid primary key default gen_random_uuid(),
  email_id       uuid not null references review.email(id) on delete cascade,
  submission_id  uuid references review.submission(id) on delete set null,
  filename       text not null,
  storage_path   text,
  sha256         text not null,
  doc_type       text check (doc_type in ('application','quote','dash','mvr','other')),
  licence_no     text,                       -- whose DASH or MVR, no dashes
  -- A corrected document keeps the old row and turns this off.
  is_latest      boolean not null default true,
  read_status    text not null default 'pending'
                   check (read_status in ('pending','ok','failed')),
  read_error     text,
  raw            jsonb,                      -- Claude's full answer, as given
  created_at     timestamptz not null default now(),
  unique (submission_id, sha256)
);

-- 2. The application ---------------------------------------------------------

create table review.application (
  id                       uuid primary key default gen_random_uuid(),
  document_id              uuid not null unique
                             references review.document(id) on delete cascade,
  form_version             text,            -- 'ON1001e 202506'
  document_key             text,            -- SignatureMaster key
  policy_no                text,            -- 'QUOTE'
  is_new_policy            boolean,
  replacing_policy_no      text,
  billing                  text check (billing in ('company','broker','other')),
  language                 text check (language in ('en','fr')),
  insurer                  text,
  broker_name              text,
  broker_code              text,
  client_no                text,
  -- Section 1
  applicant_name           text,
  street                   text,
  city                     text,
  province                 text,
  postal_code              text,            -- stored without the space
  phone_home               text,            -- digits only
  phone_work               text,
  lessor_name              text,
  lessor_address           text,
  -- Section 2
  effective_date           date,
  effective_time           time,
  expiry_date              date,
  expiry_time              time,
  -- Section 3, once per application
  registered_owner         boolean,
  used_for_hire_or_hazard  boolean,
  household_autos          smallint,
  -- Section 4
  other_persons_licensed   boolean,
  -- Page 4
  prior_insurer            text,
  prior_policy_no          text,
  prior_expiry             date,
  q_suspended_6y           boolean,
  q_cancelled_3y           boolean,
  q_misrep_3y              boolean,
  q_fraud                  boolean,
  claims_none              boolean,         -- Section 5 says "None"
  convictions_none         boolean,         -- Section 6 says "None"
  -- Section 8
  opcf44r                  boolean,
  total_premium            numeric(10,2),   -- for the totals self-check
  -- Section 9: a suspension must be explained here
  remarks                  text,
  extra_sheets             boolean,
  created_at               timestamptz not null default now()
);

create table review.app_driver (
  application_id   uuid not null references review.application(id) on delete cascade,
  driver_no        smallint not null,
  name             text not null,
  licence_no       text,                    -- no dashes; can be blank on new business
  dob              date,
  gender           text check (gender in ('M','F','X')),
  marital          text,
  driver_training  boolean,
  first_lic_class  text,
  first_lic_year   smallint,
  first_lic_month  smallint check (first_lic_month between 1 and 12),
  other_lic_class  text,
  other_lic_year   smallint,
  other_lic_month  smallint check (other_lic_month between 1 and 12),
  retiree          boolean,
  primary key (application_id, driver_no)
);

create table review.app_vehicle (
  application_id      uuid not null references review.application(id) on delete cascade,
  auto_no             smallint not null,
  model_year          smallint,
  make_model          text,
  body_type           text,
  cylinders           text,
  gvwr                text,
  vin                 text,
  owned               boolean,
  leased              boolean,
  purchase_year       smallint,
  purchase_month      smallint check (purchase_month between 1 and 12),
  condition           text check (condition in ('new','used')),
  purchase_price      numeric(10,2),
  use_pleasure        boolean,
  commute_km          integer,
  business_pct        smallint check (business_pct between 0 and 100),
  use_farm            boolean,
  use_commercial      boolean,
  annual_km           integer,
  car_pool            boolean,
  car_pool_details    text,
  fuel                text,
  unrepaired_damage   boolean,
  modified            boolean,
  lienholder_name     text,
  lienholder_address  text,
  total_premium       numeric(10,2),        -- page 6, per vehicle
  primary key (application_id, auto_no)
);

-- "Percentage Use by Each Driver" is a grid, one column per vehicle.
create table review.app_driver_use (
  application_id  uuid not null,
  driver_no       smallint not null,
  auto_no         smallint not null,
  pct             smallint check (pct between 0 and 100),
  primary key (application_id, driver_no, auto_no),
  foreign key (application_id, driver_no)
    references review.app_driver(application_id, driver_no) on delete cascade,
  foreign key (application_id, auto_no)
    references review.app_vehicle(application_id, auto_no) on delete cascade
);

create table review.app_claim (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references review.application(id) on delete cascade,
  driver_no       smallint,
  auto_no         smallint,
  loss_date       date,
  coverages       text[],                   -- {'DCPD','Coll/AP'}
  amount          numeric(10,2),
  details         text,
  foreign key (application_id, driver_no)
    references review.app_driver(application_id, driver_no) on delete cascade,
  foreign key (application_id, auto_no)
    references review.app_vehicle(application_id, auto_no) on delete cascade
);

create table review.app_conviction (
  id              uuid primary key default gen_random_uuid(),
  application_id  uuid not null references review.application(id) on delete cascade,
  driver_no       smallint,
  convicted_date  date,
  details         text,
  foreign key (application_id, driver_no)
    references review.app_driver(application_id, driver_no) on delete cascade
);

-- Section 8, the page 6 endorsements and the discounts, per vehicle.
create table review.app_coverage (
  id                  uuid primary key default gen_random_uuid(),
  application_id      uuid not null,
  auto_no             smallint not null,
  kind                text not null check (kind in ('coverage','endorsement','discount')),
  code                text not null,        -- one standard name: 'all_perils', 'opcf_23a'
  printed_name        text,                 -- exactly as on the form
  selected            boolean,              -- the tick box
  limit_amount        numeric(12,2),        -- '2,000' on the form is 2000000
  limit_text          text,                 -- '0/2,000', 'As stated in Section 5'
  deductible          numeric(10,2),
  basis               text,                 -- 'Impairment'
  premium             numeric(10,2),
  occasional_premium  numeric(10,2),
  discount_pct        numeric(5,2),
  unique (application_id, auto_no, code),
  foreign key (application_id, auto_no)
    references review.app_vehicle(application_id, auto_no) on delete cascade
);

-- 3. The MVR -----------------------------------------------------------------

create table review.mvr_report (
  id                   uuid primary key default gen_random_uuid(),
  document_id          uuid not null unique
                         references review.document(id) on delete cascade,
  licence_no           text not null,       -- no dashes, links to the driver
  name                 text not null,       -- as printed: 'LAST,FIRST'
  dob                  date not null,
  gender               text check (gender in ('M','F','X')),
  street               text,
  city                 text,
  province             text,
  postal_code          text,                -- stored without the space
  -- Reinstatements are listed but not counted: on all 117 samples this equals
  -- the mvr_entry rows minus the reinstatement rows.
  printed_convictions  smallint not null,
  created_at           timestamptz not null default now()
);

create table review.mvr_entry (
  id             uuid primary key default gen_random_uuid(),
  mvr_report_id  uuid not null references review.mvr_report(id) on delete cascade,
  entry_no       smallint not null,         -- order printed
  entry_type     text not null
                   check (entry_type in ('conviction','suspension','reinstatement')),
  entry_date     date not null,             -- left DATE column, the date reported
  offence_date   date,                      -- blank on suspensions and reinstatements
  description    text not null,
  suspension_no  text,                      -- ties a reinstatement to its suspension
  unique (mvr_report_id, entry_no)
);

-- Indexes --------------------------------------------------------------------

create index on review.submission (broker_id);
create index on review.submission (lead_id);
create index on review.email (submission_id);
create index on review.document (email_id);
create index on review.document (submission_id);
create index on review.app_claim (application_id);
create index on review.app_conviction (application_id);
create index on review.mvr_report (licence_no);

-- MyDay's own trigger function, so updated_at behaves the same here.
create trigger submission_set_updated_at
  before update on review.submission
  for each row
  execute function public.set_updated_at();

-- Access: service role only, as in MyDay. RLS on with no policies, and the
-- browser roles never get the schema.
alter table review.broker          enable row level security;
alter table review.submission      enable row level security;
alter table review.email           enable row level security;
alter table review.document        enable row level security;
alter table review.application     enable row level security;
alter table review.app_driver      enable row level security;
alter table review.app_vehicle     enable row level security;
alter table review.app_driver_use  enable row level security;
alter table review.app_claim       enable row level security;
alter table review.app_conviction  enable row level security;
alter table review.app_coverage    enable row level security;
alter table review.mvr_report      enable row level security;
alter table review.mvr_entry       enable row level security;

grant usage on schema review to service_role;
grant all on all tables in schema review to service_role;
grant all on all sequences in schema review to service_role;
