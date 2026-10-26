-- One row per "Previous Inquiry" listed on the report — who else has pulled
-- this same DLN, and when. Thin by nature: DASH only ever prints a company
-- name and a date for this section. Zero rows when the report says "None" —
-- same empty-is-default pattern as dash_policy / dash_claim for a clean file.
--
-- Useful mainly as a competitive signal (someone else is also shopping /
-- quoting this same driver right now). Dates normalized to ISO by the
-- extraction step (DASH's DD/MM/YYYY).
create table dash_inquiry (
  id uuid primary key default gen_random_uuid(),
  dash_report_id uuid not null references dash_report(id),

  inquiring_company text,           -- e.g. "WellCare Insurance Corporation"
  inquiry_date date,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create index idx_dash_inquiry_dash_report_id on dash_inquiry(dash_report_id);

alter table dash_inquiry enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) can access
-- this table; direct anon/authenticated Supabase access is fully blocked.

create trigger trg_dash_inquiry_updated_at
  before update on dash_inquiry
  for each row
  execute function set_updated_at();
