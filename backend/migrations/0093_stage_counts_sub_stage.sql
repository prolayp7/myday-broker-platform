-- In Progress and Junk get their own counts in the rail.
--
-- THE STAGE VOCABULARY IS NOT TOUCHED. lead_stage_check still allows exactly
-- the seven stages of 0051, and no lead's `stage` column changes. This adds
-- two COUNTS to the function the rail reads, so the chips can show a real
-- number that came from the whole book rather than from the page currently
-- fetched.
--
-- WHY SERVER-SIDE. The rail's other counts are computed here across every lead
-- the broker holds. Counting In Progress in the client would count only the
-- rows on screen, so the chip would read 3 on page one and 0 on page two while
-- its neighbours stayed correct — a number that disagrees with the ones beside
-- it is worse than no number.
--
-- 'New' NOW MEANS UNTOUCHED, not "every New lead". New and In Progress are
-- disjoint, so the chips still sum to `all` and the rail keeps its arithmetic:
-- a lead is in exactly one chip, the way it was when there were seven.
-- 'Lost' and 'Junk' split the same way, for the same reason.
--
-- Safe to re-run: create or replace, and nothing else in the file.

create or replace function broker_stage_counts(
  p_broker_id uuid,
  p_search text default null
)
returns jsonb as $$
  select jsonb_build_object(
    'all',               count(*),
    -- Untouched New leads only — the ones nobody has started. A lead marked
    -- In Progress is counted below instead, never in both.
    'New',               count(*) filter (where stage = 'New' and sub_stage is distinct from 'In Progress'),
    'In Progress',       count(*) filter (where stage = 'New' and sub_stage = 'In Progress'),
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
