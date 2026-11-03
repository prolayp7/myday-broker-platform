-- Submission Review: the DASH report, one per driver.
-- Run on MyDay STAGING only, after 0001.
--
-- Only what a check uses. Policies, gaps, vehicles and the KOL lines stay in
-- document.raw: policy gaps are not checked, and the four amounts below are
-- already the KOL lines added up by the rules copied from MyDay.

create table review.dash_report (
  id                  uuid primary key default gen_random_uuid(),
  document_id         uuid not null unique
                        references review.document(id) on delete cascade,
  licence_no          text not null,        -- no dashes, links to the driver
  name                text not null,        -- exactly as printed in the header
  -- DASH can print a year alone, so the printed text is kept and dob stays
  -- blank rather than getting a made-up month and day.
  dob_printed         text,
  dob                 date,
  gender              text,                 -- null when "Not available"
  marital             text,
  report_date         date not null,        -- the 8-year window counts back from here
  -- "Number of Claims in Last 6 Years": the only count DASH prints, so it
  -- cannot be compared with claims older than 6 years.
  printed_claims_6y   smallint,
  created_at          timestamptz not null default now()
);

create table review.dash_claim (
  id               uuid primary key default gen_random_uuid(),
  dash_report_id   uuid not null references review.dash_report(id) on delete cascade,
  entry_no         smallint not null,       -- order printed, "Claim #1"
  date_of_loss     date not null,
  at_fault_pct     numeric(5,2) check (at_fault_pct between 0 and 100), -- null = not printed, never 0
  -- A spouse's claim can sit on this driver's report, and one claim can print
  -- on both. The comparison goes by driver_licence + date_of_loss, not by
  -- whose report it was found on. report_driver is the driver in this
  -- report's header, whichever driver of the file that is.
  whose            text not null
                     check (whose in ('report_driver','policy_driver','not_on_policy','unknown')),
  driver_licence   text,                    -- no dashes
  driver_name      text,
  insurer          text,
  claim_number     text,
  tp_bi            numeric(12,2) not null default 0,
  tp_pd            numeric(12,2) not null default 0,
  ab               numeric(12,2) not null default 0,
  coll             numeric(12,2) not null default 0,
  -- As printed on the claim, for the Loss + Expense self-check.
  total_loss       numeric(12,2),
  total_expense    numeric(12,2),
  unique (dash_report_id, entry_no)
);

create index on review.dash_report (licence_no);
create index on review.dash_claim (driver_licence);

alter table review.dash_report enable row level security;
alter table review.dash_claim  enable row level security;

grant all on review.dash_report to service_role;
grant all on review.dash_claim  to service_role;
