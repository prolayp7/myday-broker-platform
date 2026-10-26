-- The Website leads screen: the board says which community a broker is in and
-- when they start, and a change to many brokers is one call.
--
-- WHY THE BOARD GROWS. The screen now filters and sorts by community, and says
-- in words why a switched-on broker is getting nothing. Two things it needed
-- were not on the board:
--
--   * the community. The screen was joining it in the browser from a second
--     request; when that request failed the column showed a dash and the
--     Community filter quietly matched nobody.
--   * eligible_from. The drain (0128) skips a broker who has not started yet,
--     but the screen could not see that, so it called them "Taking leads" —
--     and its "nobody is free" warning stayed silent while leads waited.
--
-- WHY A BULK FUNCTION. "Switch on everyone shown" was one request per broker,
-- in a loop in the browser. A session that expired halfway left some brokers
-- changed and some not, with nothing on screen saying which. One call, one
-- lock, one transaction: every broker listed changes, or none does.
--
-- NOT TOUCHED: the drain (0128) and the single-broker function
-- set_website_broker_setting (0127). The row-by-row switch and limit box on
-- the screen still use the latter.
--
-- ORDER: either side may go first. The backend's response model treats the
-- three new board fields as optional, so an old board still validates; the
-- bulk button simply fails until this is applied.
--
-- APPLY BY HAND in the Supabase SQL editor. Staging, then production.

begin;


-- ── 1. What the Website leads screen shows ────────────────────────────────
--
-- 0127's body, plus community_id, community_name and eligible_from per broker.
-- Same signature, so this replaces in place.
create or replace function website_lead_board()
returns jsonb as $$
declare
  v_last_reset_at timestamptz;
begin
  select last_reset_at into v_last_reset_at from lead_allocation_state;

  return jsonb_build_object(
    'waiting', (select count(*) from webhook_event
                where source = 'website' and status = 'waiting'),
    'failed',  (select count(*) from webhook_event
                where source = 'website' and status = 'failed'),
    'brokers', coalesce((
      select jsonb_agg(row_to_json(r) order by r.community_name, r.group_name, r.name)
      from (
        select
          b.id as broker_id,
          b.name,
          bg.id as group_id,
          bg.name as group_name,
          c.id as community_id,
          c.name as community_name,
          coalesce(ws.is_on, false) as is_on,
          coalesce(ws.daily_cap, 0) as daily_cap,
          (select count(*) from website_lead_delivery d
           where d.broker_id = b.id and d.delivered_at >= v_last_reset_at) as today,
          b.self_paused,
          b.is_blocked_by_admin,
          b.is_active,
          -- Sent as stored. The screen compares it with its own clock, which
          -- is the comparison the drain makes with now().
          b.eligible_from
        from broker b
        join broker_group bg on bg.id = b.group_id
        join community c on c.id = bg.community_id
        left join website_broker_setting ws on ws.broker_id = b.id
        where b.role = 'broker'
          and b.is_archived = false
          and bg.is_recycle_bin = false
      ) r
    ), '[]'::jsonb)
  );
end;
$$ language plpgsql stable;


-- ── 2. Changing many brokers at once ──────────────────────────────────────
--
-- The same rule as set_website_broker_setting (0127), for a list: null leaves
-- that field as it is, a broker with no row yet starts off / 0.
--
-- UNDER THE DRAIN'S LOCK, taken once. A bulk switch-off waits for any run
-- already holding the lock, then commits for everyone listed together; no
-- run after that gives any of them a new website lead.
--
-- ALL OR NOTHING. An id that is not a broker fails the foreign key and the
-- whole statement with it — nobody is changed.
--
-- distinct: ON CONFLICT cannot touch the same row twice in one statement, so
-- a repeated id would otherwise be an error rather than a no-op.
create or replace function set_website_broker_settings(
  p_broker_ids uuid[],
  p_is_on boolean,
  p_daily_cap int
)
returns setof website_broker_setting as $$
begin
  perform pg_advisory_xact_lock(hashtext('website_rotation'));

  return query
  insert into website_broker_setting as ws (broker_id, is_on, daily_cap)
  select distinct t.id, coalesce(p_is_on, false), coalesce(p_daily_cap, 0)
  from unnest(p_broker_ids) as t(id)
  on conflict (broker_id) do update
    set is_on = coalesce(p_is_on, ws.is_on),
        daily_cap = coalesce(p_daily_cap, ws.daily_cap)
  returning ws.*;
end;
$$ language plpgsql;

commit;


-- ── Check after applying ──────────────────────────────────────────────────
--
--   select website_lead_board() -> 'brokers' -> 0;
--       -> one broker, with community_id, community_name and eligible_from.
--
--   select * from set_website_broker_settings('{}'::uuid[], true, null);
--       -> no rows, no error.
