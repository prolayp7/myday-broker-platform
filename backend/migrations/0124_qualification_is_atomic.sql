-- The qualification answer and its conversion rows commit together, or not at
-- all.
--
-- WHY THIS EXISTS. 0123 made ensure_conversion_signals the producer, but the
-- answer is still written by a plain column update in
-- repositories/lead.py::set_qualification. Calling the function afterwards from
-- Python would leave this gap:
--
--     broker taps "Qualified"
--       -> UPDATE lead SET is_qualified = true    COMMITTED
--       -> Python calls ensure_conversion_signals()
--       -> network error / restart / Supabase blip
--       -> the lead reads Qualified forever, and NO conversion exists
--
-- Nothing repairs that. There is no sweep by design, the broker sees a success,
-- and Meta's 7-day limit closes the window within a week. One transaction
-- removes the failure mode rather than mitigating it: if the signal cannot be
-- written, the answer is not stored either, the broker sees an error, and they
-- tap again.
--
-- THE SAME SHAPE THE CREAM MODULE ASKED FOR AND COULD NOT HAVE.
-- services/meta_signal_cream.py says a swallowed failure "loses a conversion
-- permanently with nothing recording it, and the row can only be recovered by
-- someone knowing to re-run this". That was the best a post-commit Python call
-- could do. This is the fix it was describing.
--
-- WHY A FUNCTION RATHER THAN A SUPABASE TRANSACTION. PostgREST gives no
-- multi-statement transaction over the REST API — two calls are two
-- transactions, whatever order they are made in. A function is the only way to
-- put both writes inside one, and update_lead_stage (0086) already establishes
-- the pattern in this repo.
--
-- WHAT IT DOES NOT DO. No ownership check, no source check, no 404. Those stay
-- in services/lead.py::set_qualification where the HTTP status codes live —
-- this repo's four-layer rule puts every HTTPException decision in the service.
-- The function is the storage half only, and its own source gate (inside
-- ensure_conversion_signals) is a second line rather than the first.
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.

begin;


-- ── set_lead_qualification ──────────────────────────────────────────────
--
-- THE JUDGEMENT ONLY. Checked against schemas/lead.py before writing this:
-- SetQualificationRequest carries `is_qualified` and nothing else. The dialog's
-- other two answers — convictions and the licence question — are facts about the
-- PERSON, so they patch the driver through PATCH /drivers/{id} and are picked up
-- by the driver trigger in a later step. Accepting them here as well would have
-- created a second write path for columns that already have one.
--
-- NOT NULL, ALWAYS. The schema's validator already refuses
-- `{"is_qualified": null}` with a 422 — clearing a judgement is not something
-- that endpoint does — and an omitted field never reaches here because the model
-- is dumped with exclude_unset. So this function is only ever called with true
-- or false, and needs no coalesce.
create or replace function set_lead_qualification(
  p_lead_id      uuid,
  p_is_qualified boolean
)
returns void as $$
begin
  update lead
     set is_qualified = p_is_qualified
   where id = p_lead_id;

  -- ── THE WHOLE POINT OF THIS FUNCTION ──────────────────────────────────
  --
  -- Same transaction as the write above. A failure here rolls back the answer
  -- as well, which is the correct outcome: a lead that reads Qualified with no
  -- conversion is a silent permanent loss, while a failed tap is a retry.
  --
  -- CALLED FOR `false` TOO, deliberately. It queues nothing for an unqualified
  -- lead, but a broker who answers false after answering true must still have
  -- the cream rows reconciled against the new answer — and the call is one
  -- indexed read when there is nothing to do.
  perform ensure_conversion_signals(p_lead_id);
end;
$$ language plpgsql;

comment on function set_lead_qualification(uuid, boolean) is
  'Writes the qualification judgement and queues its conversion rows in the SAME transaction. A signalling failure rolls the answer back rather than leaving a qualified lead Meta was never told about.';


commit;


-- ── After applying, these should hold ───────────────────────────────────
--
-- A qualified Meta lead gets both common rows, and a second call adds nothing:
--
--   select set_lead_qualification('<a meta lead id>', true, 0, true);
--   select set_lead_qualification('<the same lead>',  true, 0, true);
--   select event_name, dataset_target, count(*)
--   from meta_signal where lead_id = '<that lead>'
--   group by 1, 2;              -- every count must be 1
--
-- Answering FALSE stores the answer and queues nothing:
--
--   select set_lead_qualification('<another meta lead>', false);
--   select count(*) from meta_signal
--   where lead_id = '<that lead>' and event_name = 'CompleteRegistration';
--     -- 0, and lead.is_qualified is false
--
-- A manual lead stores the answer and still queues nothing — the source gate
-- inside ensure_conversion_signals, not this function:
--
--   select set_lead_qualification('<a manual lead id>', true);
--   select count(*) from meta_signal where lead_id = '<that lead>';   -- 0
--
-- THE ATOMICITY ITSELF cannot be proven from the SQL editor without forcing a
-- failure; tests/test_meta_capi.py covers it by making the insert raise and
-- asserting is_qualified is unchanged.
