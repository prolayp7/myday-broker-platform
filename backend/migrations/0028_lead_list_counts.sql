-- lead_list_counts — the 4 KPI numbers for the Admin › Leads header, in ONE
-- pass.
--
-- The admin leads screen shows four counts: All / Unassigned (in pool) /
-- Assigned / Arrived today. Computing them as four separate queries would be
-- four round-trips; this returns all four from a single scan via
-- count(*) FILTER (WHERE ...), so the whole header is one cheap call.
--
-- SLICE SEMANTICS (must match the row query in repositories/lead.py::list_leads
-- and the frontend AdminLeadFilters):
--   * The counts honor the SHARED filters — community, group, broker, stage,
--     search — so the KPIs describe the SAME slice the table shows.
--   * They deliberately IGNORE the assigned/unassigned facet itself: 'all' is
--     the whole slice, and unassigned/assigned are its two halves. This is why
--     toggling the Unassigned chip doesn't make the totals jump — the chip
--     filters the ROWS, not the COUNTS.
--
-- 'today' = created_at is today in Ontario time (America/Toronto), matching the
-- allocation day the rest of the system anchors to (see 0020). We use
-- created_at (when the lead entered the system), NOT requested_at — that column
-- is null on real leads.
--
-- Every parameter is nullable; a null means "don't filter on this". Community
-- is matched by NAME (the UI passes 'Dragon' etc.) via the group→community join.

create or replace function lead_list_counts(
  p_community text default null,
  p_group_id uuid default null,
  p_broker_id uuid default null,
  p_stage text default null,
  p_search text default null
)
returns table (
  all_count bigint,
  unassigned_count bigint,
  assigned_count bigint,
  today_count bigint
) as $$
  select
    count(*)                                                        as all_count,
    count(*) filter (where l.assigned_broker_id is null)            as unassigned_count,
    count(*) filter (where l.assigned_broker_id is not null)        as assigned_count,
    count(*) filter (
      where (l.created_at at time zone 'America/Toronto')::date
          = (now() at time zone 'America/Toronto')::date
    )                                                               as today_count
  from lead l
  join broker_group bg on bg.id = l.group_id
  left join community c on c.id = bg.community_id
  where (p_community is null or c.name = p_community)
    and (p_group_id  is null or l.group_id = p_group_id)
    and (p_broker_id is null or l.assigned_broker_id = p_broker_id)
    and (p_stage     is null or l.stage = p_stage)
    and (
      p_search is null
      or l.name  ilike '%' || p_search || '%'
      or l.phone ilike '%' || p_search || '%'
      or l.email ilike '%' || p_search || '%'
    );
$$ language sql stable;
