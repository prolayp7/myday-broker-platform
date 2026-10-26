-- broker_stage_counts — per-stage lead counts for ONE broker's Leads screen.
--
-- Drives the broker Leads screen's left rail ("All 18 · New 4 · Quote Ready 2
-- · ..."). Returns a single jsonb object keyed by stage name, so the frontend
-- reads counts->>'New' directly. Computed in one scan via
-- count(*) FILTER (WHERE stage = ...), same one-pass approach as
-- lead_list_counts (0028) — not 8 separate count queries.
--
-- Scoped to the calling broker (p_broker_id) — the route passes the
-- authenticated broker's own id, never a caller-supplied one, so a broker can
-- only ever count their own leads. Honors an optional p_search (name/phone/
-- email) so the rail's counts describe the SAME slice the search box narrows to —
-- but deliberately does NOT take a p_stage filter: the rail must always show
-- the full per-stage breakdown regardless of which stage is currently
-- selected (clicking 'New' filters the list, not the rail).
--
-- Every one of the 8 real stages is always present in the output (0 when the
-- broker has none at that stage) so the rail renders a stable, complete list
-- — never a missing row just because a count is zero. 'all' is the total
-- across every stage.

create or replace function broker_stage_counts(
  p_broker_id uuid,
  p_search text default null
)
returns jsonb as $$
  select jsonb_build_object(
    'all',               count(*),
    'New',               count(*) filter (where stage = 'New'),
    'Quote Ready',       count(*) filter (where stage = 'Quote Ready'),
    'Under Review',      count(*) filter (where stage = 'Under Review'),
    'Awaiting Decision', count(*) filter (where stage = 'Awaiting Decision'),
    'Sold Pending Docs', count(*) filter (where stage = 'Sold Pending Docs'),
    'Policy Submitted',  count(*) filter (where stage = 'Policy Submitted'),
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
