-- ONE ROW PER DRIVER — the whole DASH (Driver & Auto Search History, IBC)
-- report for that driver's license number. driver_id is UNIQUE: a driver has
-- at most one DASH report, ever. Re-uploading a new DASH for the same driver
-- OVERWRITES — the service layer deletes every child row (dash_policy /
-- dash_claim / dash_vehicle / dash_claim_kol / dash_inquiry) under this report
-- and re-inserts fresh, then updates this row. No history of prior pulls is
-- kept — deliberately NOT append-only, unlike lead_assignment /
-- lead_stage_history. If a re-upload fails partway, the broker simply
-- re-uploads (accepted — see docs/dash-integration-design.md).
--
-- Every DATA field here is directly and only about the report's own subject
-- (the driver whose license was searched) — confirmed field-by-field against
-- real DASH PDFs. Continuously editable by the broker after parsing (plain
-- overwrite on save) — no AI-vs-broker dual-state, no review-confirmed flag,
-- same as every other editable field in this CRM.
--
-- Source values are stored AS PRINTED (source wording preserved) — e.g.
-- subject_name stays "FRANCIS,MILLAT F" in DASH's own Last,First format, not
-- reformatted. Dates ARE normalized to ISO before storage (the extraction step
-- converts DASH's DD/MM/YYYY — see the date-format guardrail in
-- docs/dash-integration-design.md §1.5).
--
-- Coverage GAPS are NOT stored — they are computed live from dash_policy dates
-- (an earlier printed gap/overlap flag could not be reliably confirmed across
-- samples). years_continuous_insurance below is DASH's own stated figure, kept
-- as a cross-check against that computed analysis.
create table dash_report (
  id uuid primary key default gen_random_uuid(),

  -- UNIQUE — one report per driver, overwritten (not versioned) on re-upload.
  driver_id uuid not null unique references driver(id),

  -- === Ingestion / async-parse state (implementation, not from the PDF) ===
  -- The parse runs in the background; the broker's screen polls this. State
  -- lives in the DB (NOT server memory) so it survives a restart and is
  -- correct no matter which backend worker answers the poll. 'parsing' is set
  -- the moment the row is created (before Document AI runs), flipped to
  -- 'complete' when the middleware finishes writing the children, or 'failed'
  -- if extraction/mapping errors out (the broker then re-pulls or uploads).
  parse_status text not null default 'parsing' check (
    parse_status in ('parsing', 'complete', 'failed')
  ),
  parse_error text,
  -- Where the ORIGINAL fetched PDF is stored in Supabase Storage, keyed by
  -- driver so a re-upload overwrites it (stable path, no orphaned old PDFs).
  -- Fetched server-side from the short-lived signed URL immediately on submit
  -- (within its ~2-min window), BEFORE the slow parse — so the parse works off
  -- our stored copy and the URL expiry can never race it. Kept permanently as
  -- the source-of-truth document (re-parse without re-pulling; compliance;
  -- broker verification against the extracted fields).
  source_pdf_path text,

  -- === Report metadata (about the pull itself) ===
  report_date timestamptz,          -- when DASH generated it, e.g. 2026-01-29 10:07:50 EST
  requestor text,                   -- who pulled it, e.g. "Sujith Sudesan"
  company text,                     -- e.g. "KMI Brokers Inc."
  comment text,                     -- DASH's own Comment field — usually "Not available"
  last_data_update date,            -- when DASH's underlying data was last refreshed
  years_of_data int,                -- how far back DASH searched, e.g. 25

  -- === Subject identity ===
  subject_name text,                -- AS PRINTED, e.g. "FRANCIS,MILLAT F" (Last,First)
  dln text,                         -- searched license #, as printed; should match driver.license_number
  -- Kept as text: DASH sometimes prints only a year ("1997"), sometimes a full
  -- date — not reliably a full date, so not forced to a date type.
  date_of_birth text,

  -- === Subject profile ===
  address text,
  policies_at_address text,         -- "Number of Policies at this Address" — usually "Not available"
  gender text,
  marital_status text,
  years_licensed int,
  years_continuous_insurance int,   -- DASH's own figure; cross-checks the computed gap analysis
  years_claims_free int,
  driver_training text,             -- e.g. "Yes" / "NA"

  -- === Six-year rolled-up risk counters (DASH's own numbers) ===
  claims_last_6y int,
  at_fault_claims_last_6y int,
  comprehensive_losses_last_6y int,
  dcpd_claims_last_6y int,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- driver_id is already UNIQUE (a unique constraint creates its own index), so
-- the foreign-key lookup is covered — no separate index needed here.

alter table dash_report enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) can access
-- this table; direct anon/authenticated Supabase access is fully blocked.

create trigger trg_dash_report_updated_at
  before update on dash_report
  for each row
  execute function set_updated_at();
