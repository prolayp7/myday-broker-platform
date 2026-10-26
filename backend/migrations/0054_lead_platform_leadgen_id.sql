-- Gives lead a place to remember Meta's own id for the ONE form submission it
-- came from. Meta's leadgen webhook carries this id, and lead_ingestion
-- already reads it to call the Graph API for the submitted answers — it was
-- simply never kept afterward. This is step 1 of Meta Conversions API (CAPI):
-- platform_leadgen_id is the single strongest match signal Meta accepts for a
-- lead-ads CRM event (an exact match, where email/phone are only
-- probabilistic), and the one user_data field that must be sent UNHASHED.
--
-- Nullable: a manually created lead (services/lead.py::create_manual_lead)
-- has no Meta submission and never will.
alter table lead add column platform_leadgen_id text;

-- One leadgen_id is one submission — two leads claiming the same one is a
-- bug the database should refuse outright, not trust application code to
-- prevent. Partial (WHERE ... is not null) so the many leads that have none
-- (every manual lead, and every lead created before this migration) don't
-- collide with each other.
create unique index idx_lead_platform_leadgen_id
  on lead(platform_leadgen_id)
  where platform_leadgen_id is not null;
