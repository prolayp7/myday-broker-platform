-- A finer distinction INSIDE a stage, without touching the seven stages.
--
-- Two things the pipeline could not record, both real:
--
--   * A New lead nobody has started looks exactly like one being worked. On
--     the live data when this was written, 3 of 21 New leads had real detail
--     filled in (date of birth, gender, address) and 15 carried only the
--     name/phone/email they were seeded with. The difference existed; nothing
--     showed it.
--   * A spam or duplicate lead has to be marked Lost, where it sits beside a
--     genuine opportunity that did not convert — which makes "how many did we
--     actually lose" unanswerable.
--
-- WHY NOT NEW STAGES. Adding 'In Progress' and 'Junk' to lead.stage was the
-- obvious move and was rejected deliberately. `stage` is wired into the CHECK
-- constraint (0051), broker_stage_counts (0051), update_lead_stage (0085),
-- both Meta trigger functions, the Pydantic Stage literal, the premium and
-- qualification guards, both UIs and the tests. Every one of those would have
-- had to change together, and a half-applied stage taxonomy fails silently in
-- opposite directions — the same failure class 0074 documents for its own
-- four-place predicate. One nullable column touches none of them.
--
-- ONE COLUMN FOR BOTH, not one per stage. They are the same idea — a label
-- within a stage — and two columns would mean two of everything downstream
-- for a distinction the reader does not have.
--
-- NULL IS THE ORDINARY CASE and means the plain stage: plain New, plain Lost.
-- Not a default of 'In Progress', which would claim work nobody had done.
--
-- NOT BACKFILLED. 'In Progress' is a forward-looking marker a broker sets;
-- inferring it retroactively from which fields happen to be filled would put
-- every part-completed lead into a state nobody chose, and would be wrong the
-- moment a broker cleared a field.
alter table lead add column sub_stage text;

-- The vocabulary, constrained for the same reason gender and marital_status
-- are (0035): free text means one broker writes "junk" and another "Junk
-- lead", and the two can never be counted together. Named explicitly, the way
-- 0051 renamed lead_stage_check, so a later migration can drop it by name
-- rather than guessing what Postgres generated.
--
-- THE PAIRING IS NOT ENFORCED HERE — 'Junk' only with Lost, 'In Progress'
-- only with New. Postgres cannot express that as a CHECK without a trigger,
-- and a trigger is more machinery than this earns. services/lead.py's
-- update_stage owns the pairing and clears this column on every stage move,
-- so a lead marked Junk and later reopened cannot keep a stale label. This
-- constraint's job is only to stop a value nobody recognises being stored.
alter table lead add constraint lead_sub_stage_check check (
  sub_stage is null or sub_stage in ('In Progress', 'Junk')
);
