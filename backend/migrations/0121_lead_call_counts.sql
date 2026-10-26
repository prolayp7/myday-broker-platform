-- Two call counters on the lead: how many times someone picked up, and how
-- many times they did not.
--
-- THE QUESTION THESE ANSWER is asked of the WHOLE LIST at once — "who have I
-- not rung yet?" — not of one lead at a time. That is why they live here as
-- plain columns rather than being counted from contact_log on every read: the
-- broker's lead list paints every row on every poll, and MY_LEAD_SELECT
-- (repositories/lead.py) was deliberately stripped of its embeds after they
-- measured at ~60% of the query time (208ms -> 84ms). A per-lead aggregate
-- over another table would put that cost straight back.
--
-- contact_log (0060) still exists and still stores one row per contact with a
-- richer outcome vocabulary. The two are not in competition and not kept in
-- sync: these counters are a scanning aid, that table is history. Nothing
-- backfills these from it — every lead starts at zero the day this ships, so
-- a zero means "no calls recorded with these counters", NOT "nobody ever rang
-- this person".
--
-- THEY SURVIVE REASSIGNMENT, deliberately. A count describes the CUSTOMER —
-- "this person has been rung twice with no answer" is exactly what the next
-- broker needs to know. This is the opposite of open reminders, which
-- admin_assign_lead() moves to the new broker (0076), because a reminder is a
-- task someone owns and a call count is a fact about the lead.

begin;

alter table lead
  -- CHECKed, not just clamped in the API. The service refuses to go below
  -- zero, but two decrements racing each other would each read 0 and each
  -- write -1; this is the constraint that makes that impossible rather than
  -- unlikely. adjust_lead_call_count() below does the arithmetic in SQL for
  -- the same reason.
  add column answered_count  int not null default 0 check (answered_count  >= 0),
  add column no_answer_count int not null default 0 check (no_answer_count >= 0);

comment on column lead.answered_count is
  'Calls where the customer picked up. Scanning aid, not history — see contact_log. Survives reassignment.';
comment on column lead.no_answer_count is
  'Calls where the customer did not pick up. Scanning aid, not history — see contact_log. Survives reassignment.';

-- ONE STATEMENT, so two quick presses cannot lose a count.
--
-- The obvious implementation — read the count in Python, add one, write it
-- back — drops an increment whenever two requests overlap: both read 2, both
-- write 3. Doing the arithmetic inside the UPDATE makes each press a single
-- atomic read-modify-write that Postgres serialises for us.
--
-- greatest(0, ...) is what makes the decrement safe: pressing minus on a zero
-- counter is a no-op rather than a constraint violation, so the API never has
-- to pre-check and then race.
--
-- p_column is NOT interpolated from client input. The service maps its
-- validated outcome literal onto one of exactly two names, and anything else
-- raises here — a belt-and-braces guard so this function can never become an
-- injection point even if a future caller is careless.
create or replace function adjust_lead_call_count(
  p_lead_id uuid,
  p_column  text,
  p_delta   int
)
returns table (answered_count int, no_answer_count int) as $$
begin
  if p_column not in ('answered_count', 'no_answer_count') then
    raise exception 'adjust_lead_call_count: unknown column %', p_column;
  end if;

  if p_delta not in (1, -1) then
    raise exception 'adjust_lead_call_count: delta must be 1 or -1, got %', p_delta;
  end if;

  return query
  update lead
     set answered_count = case
           when p_column = 'answered_count'
           then greatest(0, lead.answered_count + p_delta)
           else lead.answered_count
         end,
         no_answer_count = case
           when p_column = 'no_answer_count'
           then greatest(0, lead.no_answer_count + p_delta)
           else lead.no_answer_count
         end
   where lead.id = p_lead_id
  returning lead.answered_count, lead.no_answer_count;
end;
$$ language plpgsql;

commit;
