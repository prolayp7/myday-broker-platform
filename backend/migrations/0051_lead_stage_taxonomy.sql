-- Lead stage taxonomy: 8 stages -> 7, with three renamed.
--
--   New                  New         (unchanged)
--   Quote Ready       -> Quote Done
--   Under Review      -> (removed)
--   Awaiting Decision -> Pending
--   Sold Pending Docs -> Sold
--   Policy Submitted  -> Submitted
--   Issued               Issued      (unchanged)
--   Lost                 Lost        (unchanged)
--
-- WHY: the rail carried eight stages where a broker only ever needs to say
-- which of a few things is true. "Under Review" and "Quote Ready" described the
-- same moment from two angles (the quote exists; someone is looking at it), and
-- the longer names read as sentences in a 14rem rail. The surviving "waiting on
-- the customer" state is KEPT — it is the one that means "I am chasing this",
-- which is the question a broker asks their own list most often — but renamed
-- to plain "Pending", which says the same thing in one short word.
--
-- SAFE TO RUN IN ONE PASS: the CHECK constraint and broker_stage_counts() are
-- changed together in this file, on purpose. Apply them separately and there is
-- a window where the rail asks for stage keys the database will not accept
-- (or vice versa). They are one change.
--
-- The UPDATEs below run BEFORE the new constraint so existing rows are already
-- legal when it is applied. They are written to be idempotent-safe: rows
-- already holding a new name simply match nothing.

-- ── 1. Rewrite existing data to the new names ────────────────────────────
-- lead.stage is the live value the app reads.
update lead set stage = 'Quote Done' where stage = 'Quote Ready';
update lead set stage = 'Pending'    where stage = 'Awaiting Decision';
update lead set stage = 'Sold'       where stage = 'Sold Pending Docs';
update lead set stage = 'Submitted'  where stage = 'Policy Submitted';
-- 'Under Review' is being removed outright. Its nearest surviving meaning is
-- "the quote is done and out" — not 'Pending', which asserts the customer has
-- it and is deciding, something an Under Review lead may not have reached.
-- Fold it back to Quote Done rather than claim more than we know.
update lead set stage = 'Quote Done' where stage = 'Under Review';

-- lead_stage_history records what was true AT THE TIME, so renaming its rows is
-- rewriting history. But these are the same events under new names, not
-- different events — a lead that reached 'Quote Ready' did reach what is now
-- called 'Quote Done'. Leaving the old strings would make the history
-- unjoinable to the current vocabulary for no gain. There is no CHECK
-- constraint on this table; these updates exist for consistency, not validity.
update lead_stage_history set from_stage = 'Quote Done' where from_stage = 'Quote Ready';
update lead_stage_history set to_stage   = 'Quote Done' where to_stage   = 'Quote Ready';
update lead_stage_history set from_stage = 'Pending'    where from_stage = 'Awaiting Decision';
update lead_stage_history set to_stage   = 'Pending'    where to_stage   = 'Awaiting Decision';
update lead_stage_history set from_stage = 'Sold'       where from_stage = 'Sold Pending Docs';
update lead_stage_history set to_stage   = 'Sold'       where to_stage   = 'Sold Pending Docs';
update lead_stage_history set from_stage = 'Submitted'  where from_stage = 'Policy Submitted';
update lead_stage_history set to_stage   = 'Submitted'  where to_stage   = 'Policy Submitted';
update lead_stage_history set from_stage = 'Quote Done' where from_stage = 'Under Review';
update lead_stage_history set to_stage   = 'Quote Done' where to_stage   = 'Under Review';

-- ── 2. Swap the CHECK constraint ─────────────────────────────────────────
-- Named explicitly (0008 let Postgres generate lead_stage_check) so this and
-- any future migration can drop it by name rather than by guessing.
alter table lead drop constraint if exists lead_stage_check;
alter table lead add constraint lead_stage_check check (
  stage in (
    'New', 'Quote Done', 'Pending',
    'Sold', 'Submitted', 'Issued', 'Lost'
  )
);

-- ── 3. Rebuild the rail's count function ─────────────────────────────────
-- Same contract as 0033 (one jsonb object, every stage always present, 'all'
-- for the total) — only the key list changes. See 0033 for why this is one
-- scan rather than N counts, and why it takes no p_stage.
create or replace function broker_stage_counts(
  p_broker_id uuid,
  p_search text default null
)
returns jsonb as $$
  select jsonb_build_object(
    'all',               count(*),
    'New',               count(*) filter (where stage = 'New'),
    'Quote Done',        count(*) filter (where stage = 'Quote Done'),
    'Pending',           count(*) filter (where stage = 'Pending'),
    'Sold',              count(*) filter (where stage = 'Sold'),
    'Submitted',         count(*) filter (where stage = 'Submitted'),
    'Issued',            count(*) filter (where stage = 'Issued'),
    'Lost',              count(*) filter (where stage = 'Lost')
  )
  from lead
  where assigned_broker_id = p_broker_id
    and (
      p_search is null
      or name  ilike '%' || p_search || '%'
      or phone ilike '%' || p_search || '%'
      or email ilike '%' || p_search || '%'
    );
$$ language sql stable;
