-- 'In Progress' stops being a label and becomes a stage.
--
-- WHAT 0092 DECIDED, AND WHY IT IS BEING REVERSED. That migration added
-- lead.sub_stage rather than an eighth stage, and its reasoning was sound:
-- `stage` is wired into the CHECK constraint, broker_stage_counts,
-- update_lead_stage, both Meta trigger functions, the Pydantic Stage literal,
-- the premium and qualification guards, both UIs and the tests — "every one of
-- those would have had to change together, and a half-applied stage taxonomy
-- fails silently in opposite directions."
--
-- All of that is still true. What changed is what the label is now asked to do.
--
-- THE DEFECT THAT FORCES THIS. A New -> In Progress move writes NO
-- lead_stage_history row at all.
--
-- update_lead_stage (0086) writes history only when the stage changes. Setting
-- the label is a SEPARATE update afterwards (repositories/lead.py::set_sub_stage,
-- called from services/lead.py), outside that function's transaction, with no
-- ownership guard and a bare except that logs and carries on.
--
-- Meta's CompleteRegistration trigger fires `after insert on
-- lead_stage_history`. So the event it would listen for does not exist. Moving
-- the conversion signal earlier in the funnel is not an edit to the trigger's
-- `if` — this promotion is what creates the row in the first place.
--
-- It also collapses two writes into one guarded transaction, and removes the
-- stale-label failure mode the current code accepts in a comment: a lead marked
-- Junk and reopened keeping its label because the second write failed.
--
-- THE 50 ROWS GET NO HISTORY ROW. They moved before the stage existed, and
-- inserting synthetic rows would fire the after-insert trigger and queue 50
-- spurious CompleteRegistration events to Meta — which deduplicates on
-- event_id, so they could never be recalled. Their journey has a gap. A gap is
-- recoverable; a wrong conversion is not.
--
-- THE TRIGGER IS NOT TOUCHED HERE. It still fires only on Quote Done, exactly
-- as today, so applying this file changes no Meta behaviour whatsoever. Widening
-- it waits for 0116, which ships with the five-year qualification guard — a lead
-- insured under five years is automatically not qualified, and firing before
-- that protection exists would send irreversible conversions for leads that
-- should never have been reported.
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.
--
-- NOT ATOMIC WITH THE APPLICATION. Either order breaks while the two disagree:
-- migration first and the running frontend still sends
-- stage='New', sub_stage='In Progress', which step 3 now rejects; application
-- first and it sends stage='In Progress', which step 1 has not yet allowed. Pause
-- broker access, apply this, deploy both halves, smoke-test, reopen.

begin;


-- ── 1. The stage vocabulary ─────────────────────────────────────────────
--
-- Named explicitly since 0051, so it can be dropped by name rather than by
-- guessing what Postgres generated for an inline constraint.
--
-- In Progress sits after New because the list is pipeline order — it is read
-- top-to-bottom in the rail, and a stage out of sequence there reads as a bug.
alter table lead drop constraint if exists lead_stage_check;
alter table lead add constraint lead_stage_check check (
  stage in (
    'New', 'In Progress', 'Quote Done', 'Pending',
    'Sold', 'Submitted', 'Issued', 'Lost'
  )
);


-- ── 2. Move the 50 leads ────────────────────────────────────────────────
--
-- Measured immediately before writing: 50 rows at ('New', 'In Progress') and 14
-- at ('Lost', 'Junk').
--
-- sub_stage IS CLEARED in the same statement. Leaving it set would satisfy the
-- narrowed constraint below only by accident of ordering, and would leave every
-- migrated lead carrying a label that now duplicates its own stage.
--
-- JUNK IS UNTOUCHED, and the `stage = 'New'` predicate is what guarantees it: a
-- Lost/Junk row cannot match. Stated because "update ... where sub_stage is not
-- null" would have been the obvious shortcut and would have destroyed all 14.
update lead
   set stage = 'In Progress',
       sub_stage = null
 where stage = 'New'
   and sub_stage = 'In Progress';


-- ── 3. Narrow the label vocabulary ──────────────────────────────────────
--
-- 'In Progress' is no longer a legal label, so a stale one cannot be written
-- back by an old client, a missed deploy, or a hand-run update. Junk remains,
-- and remains the only one.
--
-- THE PAIRING IS STILL NOT ENFORCED HERE, as 0092 explained: Postgres cannot
-- express "Junk only with Lost" as a CHECK without a trigger, and
-- services/lead.py::update_stage owns that rule.
alter table lead drop constraint if exists lead_sub_stage_check;
alter table lead add constraint lead_sub_stage_check check (
  sub_stage is null or sub_stage = 'Junk'
);


-- ── 4. The rail's counts ────────────────────────────────────────────────
--
-- 'In Progress' becomes an ordinary stage filter, and 'New' reverts to a plain
-- count — the `sub_stage is distinct from 'In Progress'` exclusion 0093 needed
-- to keep the two disjoint is now handled by them being different stages.
--
-- THE LOST/JUNK SPLIT IS UNCHANGED, byte for byte. Junk is still a label, so it
-- still needs the same treatment it has had since 0093.
--
-- The contract is the one 0033 set and 0051 and 0093 kept: one jsonb object,
-- every key always present, 'all' for the total, one scan rather than N counts.
-- A chip is exactly one bucket, so the chips still sum to 'all'.
create or replace function broker_stage_counts(
  p_broker_id uuid,
  p_search text default null
)
returns jsonb as $$
  select jsonb_build_object(
    'all',               count(*),
    'New',               count(*) filter (where stage = 'New'),
    'In Progress',       count(*) filter (where stage = 'In Progress'),
    'Quote Done',        count(*) filter (where stage = 'Quote Done'),
    'Pending',           count(*) filter (where stage = 'Pending'),
    'Sold',              count(*) filter (where stage = 'Sold'),
    'Submitted',         count(*) filter (where stage = 'Submitted'),
    'Issued',            count(*) filter (where stage = 'Issued'),
    -- Genuine lost opportunities, with spam and duplicates counted separately
    -- — the question "how many did we actually lose" is the reason sub_stage
    -- exists (0092).
    'Lost',              count(*) filter (where stage = 'Lost' and sub_stage is distinct from 'Junk'),
    'Junk',              count(*) filter (where stage = 'Lost' and sub_stage = 'Junk')
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


commit;


-- ── After applying, these should hold ───────────────────────────────────
--
--   select stage, sub_stage, count(*) from lead group by 1, 2 order by 3 desc;
--
--     'In Progress', null  -> 50
--     'Lost',        'Junk' -> 14      (unchanged)
--     'New',         null  -> 63       (unchanged)
--
-- and this must return zero rows — nothing may carry the old label:
--
--   select id from lead where sub_stage = 'In Progress';
