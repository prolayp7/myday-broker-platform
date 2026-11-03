-- Submission Review: review runs, findings, notifications, manual review.
-- Run on MyDay STAGING only, after 0001 and 0002.

-- Stop if the application tables hold rows: their Yes/No columns are replaced
-- below, and a boolean cannot say what "unclear" says.
do $$
begin
  if exists (select 1 from review.application) then
    raise exception 'review.application is not empty';
  end if;
end $$;

-- A Yes/No as read. Blank and unclear are never turned into No.
create domain review.tick as text
  check (value in ('yes','no','blank','unclear'));

-- 1. Yes/No columns ---------------------------------------------------------

alter table review.application
  alter column is_new_policy           type review.tick using null,
  alter column registered_owner        type review.tick using null,
  alter column used_for_hire_or_hazard type review.tick using null,
  alter column other_persons_licensed  type review.tick using null,
  alter column q_suspended_6y          type review.tick using null,
  alter column q_cancelled_3y          type review.tick using null,
  alter column q_misrep_3y             type review.tick using null,
  alter column q_fraud                 type review.tick using null,
  alter column claims_none             type review.tick using null,
  alter column convictions_none        type review.tick using null,
  alter column opcf44r                 type review.tick using null,
  alter column extra_sheets            type review.tick using null;

alter table review.app_driver
  alter column driver_training type review.tick using null,
  alter column retiree         type review.tick using null;

alter table review.app_vehicle
  alter column owned             type review.tick using null,
  alter column leased            type review.tick using null,
  alter column use_pleasure      type review.tick using null,
  alter column use_farm          type review.tick using null,
  alter column use_commercial    type review.tick using null,
  alter column car_pool          type review.tick using null,
  alter column unrepaired_damage type review.tick using null,
  alter column modified          type review.tick using null;

alter table review.app_vehicle drop constraint app_vehicle_condition_check;
alter table review.app_vehicle add constraint app_vehicle_condition_check
  check (condition in ('new','used','blank','unclear'));

alter table review.app_coverage
  alter column selected type review.tick using null;

-- 2. Worker progress --------------------------------------------------------

alter table review.email
  add column process_status text not null default 'pending'
    check (process_status in ('pending','done','failed')),
  add column attempts   smallint not null default 0,
  add column last_error text;

alter table review.document
  add column match_status text not null default 'pending'
    check (match_status in ('pending','matched','held','manual_review')),
  add column process_status text not null default 'pending'
    check (process_status in ('pending','stored','read','matched','done','failed')),
  add column attempts   smallint not null default 0,
  add column last_error text;

alter table review.submission
  add column needs_review boolean not null default false,
  add constraint submission_status_check
    check (status in ('waiting_for_documents','needs_manual_review','review_complete'));

-- 3. Results ----------------------------------------------------------------

create table review.review_run (
  id             uuid primary key default gen_random_uuid(),
  submission_id  uuid not null references review.submission(id) on delete cascade,
  document_ids   uuid[] not null,            -- the active documents it covered
  status         text not null default 'running'
                   check (status in ('running','complete','failed')),
  outdated       boolean not null default false,
  started_at     timestamptz not null default now(),
  finished_at    timestamptz
);

create table review.finding (
  id                  uuid primary key default gen_random_uuid(),
  review_run_id       uuid not null references review.review_run(id) on delete cascade,
  rule_no             text not null,         -- '9', or 'filled' for the filled check
  section             text,
  severity            text not null check (severity in ('error','warning')),
  driver_no           smallint,
  auto_no             smallint,
  application_value   text,
  other_value         text,
  source_document_id  uuid references review.document(id) on delete set null,
  message             text not null,
  cleared_by          text,
  cleared_at          timestamptz
);

create table review.notification (
  id             uuid primary key default gen_random_uuid(),
  submission_id  uuid references review.submission(id) on delete set null,
  review_run_id  uuid references review.review_run(id) on delete set null,
  kind           text not null check (kind in ('missing_documents','which_file','result','manual_review')),
  to_address     text not null,
  subject        text not null,
  body           text not null,
  fingerprint    text,                       -- the missing list, so a changed list sends once
  status         text not null default 'pending' check (status in ('pending','sent')),
  sent_at        timestamptz,
  created_at     timestamptz not null default now()
);

-- Starts before any submission may exist, so every link is optional.
create table review.manual_review (
  id             uuid primary key default gen_random_uuid(),
  email_id       uuid references review.email(id) on delete set null,
  document_id    uuid references review.document(id) on delete set null,
  submission_id  uuid references review.submission(id) on delete set null,
  reason         text not null,
  status         text not null default 'open' check (status in ('open','resolved')),
  action         text,                       -- assign, set or retry
  resolved_by    text,
  resolved_at    timestamptz,
  created_at     timestamptz not null default now()
);

-- Indexes -------------------------------------------------------------------

create index on review.review_run (submission_id);
create index on review.finding (review_run_id);
create index on review.notification (status);
create index on review.manual_review (status);
create unique index notification_one_result_per_run
  on review.notification (review_run_id) where kind = 'result';
create unique index notification_one_per_missing_list
  on review.notification (submission_id, fingerprint) where kind = 'missing_documents';

-- Access: service role only, as in 0001.
alter table review.review_run    enable row level security;
alter table review.finding       enable row level security;
alter table review.notification  enable row level security;
alter table review.manual_review enable row level security;

grant all on review.review_run, review.finding, review.notification,
  review.manual_review to service_role;
