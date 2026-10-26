-- The money's permanent home — ONE ROW PER YEAR a customer stays with us.
-- Genuinely one-to-many off lead, same pattern as attendance/pricing_rule.
--
-- WHY THIS TABLE EXISTS NOW: today the four premium figures a broker types
-- on New Policy live only in React state (see NewPolicyPage.tsx's Draft
-- interface) — refreshing the page loses them. Storing them as single
-- mutable columns on lead would fix the refresh problem but would still
-- silently lose PRIOR years' numbers the moment a renewal overwrites them.
-- One row per policy_year is what keeps every year's figures exactly as they
-- were quoted, forever.
--
-- ALSO step 2 of Meta CAPI (see 0054): total_new_premium is what gets sent
-- to Meta as the Purchase event's `value` — the field Meta's own docs mark
-- REQUIRED on every Purchase event, not optional.
create table premium_history (
  id uuid primary key default gen_random_uuid(),

  -- Which lead/customer this year's premium record belongs to.
  lead_id uuid not null references lead(id) on delete cascade,

  -- Which year of the relationship this row represents (e.g. 2026 for the
  -- first year, 2027 for the first renewal).
  policy_year int not null,

  -- What the customer was paying with their PRIOR insurer that year — one
  -- total figure, not broken out per vehicle. Nullable: a broker fills these
  -- in over several conversations, not all at once, same rule as vehicle and
  -- property.
  current_auto_premium numeric,
  -- What we quoted/charged for auto that year.
  new_auto_premium numeric,
  -- Same as current_auto_premium, for property. Only relevant when
  -- lead.combined_policy_type isn't 'Standalone'.
  current_property_premium numeric,
  -- Same as new_auto_premium, for property.
  new_property_premium numeric,

  -- current_auto_premium + current_property_premium, STORED (not computed
  -- live) so this year's total is locked in permanently even if a later
  -- year's premiums change.
  total_current_premium numeric,
  -- new_auto_premium + new_property_premium, stored the same way. This is
  -- the exact number CAPI reads for a Purchase event's `value`.
  total_new_premium numeric,
  -- total_current_premium - total_new_premium, stored. The customer-facing
  -- "how much we saved you" figure for this specific year.
  savings numeric,

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

-- ONE row per customer per year, enforced by the database rather than
-- trusted to code. Without this a double-save creates two rows for the same
-- year and total_new_premium becomes ambiguous — which matters because that
-- column is what CAPI sends Meta as a Purchase event's value. Two candidate
-- values means no reliable value to send.
create unique index idx_premium_history_lead_year
  on premium_history(lead_id, policy_year);

create index idx_premium_history_lead_id on premium_history(lead_id);

alter table premium_history enable row level security;
-- Deny-all: no policies. Only service_role (our FastAPI backend) reaches
-- this table, same rule as every other table in this schema.
