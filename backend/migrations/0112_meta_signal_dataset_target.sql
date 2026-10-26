-- meta_signal grows a dataset_target, and the uniqueness guard stops leaking.
--
-- TWO CHANGES THAT HAVE TO SHIP TOGETHER. A second dataset is coming (a
-- "cream" one, trained only on premium customers), so a signal now needs to
-- say WHICH dataset it is for. And the existing unique index cannot survive
-- that addition unchanged, because it would treat one lead's common and cream
-- CompleteRegistration as the same row and silently drop one of them.
--
-- ── THE LEAK IN THE OLD INDEX ────────────────────────────────────────────
--
-- 0056:93 created:
--
--   create unique index idx_meta_signal_lead_event_active
--     on meta_signal(lead_id, event_name)
--     where status <> 'Failed';
--
-- The `where` clause was deliberate and is quoted in 0056's own comment: it
-- lets "a genuine failure still be retried, while a success can never be
-- duplicated." That reasoning holds for an ordinary failure. It does NOT hold
-- for the one case services/meta_signal.py:253-291 creates on purpose —
--
--   Meta ACCEPTED the event, and the database write recording that failed.
--
-- That row is marked Failed (correctly: the send worked, a retry cannot help,
-- and an admin needs to see it). But Failed rows sit OUTSIDE this index, so
-- the next stage re-entry inserts a SECOND row for the same lead and event,
-- with a new id — and meta_signal.id is what the sender passes to Meta as
-- event_id, the only thing Meta deduplicates on. Two ids means Meta counts
-- one customer's conversion twice, which corrupts the exact signal this table
-- exists to send.
--
-- Live data at the time of writing: 2 leads have reached Quote Done twice and
-- 9 have moved backwards through the pipeline. The hole is open; nothing has
-- fallen in yet. That is the whole reason this is being fixed before the
-- second dataset is introduced rather than after — a dataset built on a queue
-- that can double-count produces two wrong datasets instead of one.
--
-- A PARTIAL INDEX CANNOT FIX THIS, which was an early wrong answer worth
-- recording: the dangerous row is already Failed, so any predicate that
-- excludes Failed rows excludes the very row causing the duplicate.
-- Uniqueness has to span EVERY status. Retry then works by UPDATE on the
-- existing row (repositories/meta_signal.py::reset_to_pending, which keeps the
-- id and therefore the event_id) rather than by inserting a fresh one.
--
-- ── WHY A LOGICAL NAME, NOT THE META DATASET ID ──────────────────────────
--
-- dataset_target holds 'common' / 'cream', not the numeric id from Events
-- Manager. Three reasons, all of which were considered and none theoretical:
--
--   * NOT NULLABLE. Postgres permits unlimited NULLs in a unique index, so a
--     nullable column would silently weaken the guard it is part of — the one
--     failure mode this migration exists to close.
--   * IT SURVIVES A SWAP. Point the env var at a different dataset in Events
--     Manager and every historical row still says which ROLE it served.
--     payload.dataset_id records the numeric id actually used for that one
--     send, which is the right place for that detail and is already stored.
--   * IT IS RESOLVED AT SEND TIME through META_CAPI_DATASET_COMMON /
--     META_CAPI_DATASET_CREAM, so the mapping lives in configuration, not in
--     161 historical rows.
--
-- ── AND A CHECK, NOT JUST NOT NULL ───────────────────────────────────────
--
-- not null alone still admits 'creme'. A typo there inserts happily, then
-- falls outside every dataset filter on the dashboard AND resolves to no env
-- mapping at send time — a row that is simultaneously invisible and
-- unsendable. This column is the join between three separate things (the
-- trigger that writes it, the resolver that reads it, the reporting that
-- groups by it), so the database enforces its vocabulary rather than trusting
-- all three to agree forever. Same pattern as this table's existing
-- event_name and status checks (0056:49, 0056:67).
--
-- ── ORDER MATTERS, AND THIS IS THE ORDER ─────────────────────────────────
--
-- 1. add the column (default + check), then backfill
-- 2. verify no nulls remain
-- 3. drop the old partial index
-- 4. create the new full unique constraint
-- 5. triggers, LAST — and in a later migration
--
-- Dropping first would leave a window with no duplicate protection at all.
-- Creating the new constraint before the column is populated cannot work.
-- And the triggers are deliberately NOT touched here: nothing should start
-- writing a second row per lead until the constraint that makes those rows
-- idempotent already exists.
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.


-- ── PRE-FLIGHT. Run this FIRST, on its own, and read the result ──────────
--
-- Step 4 creates a unique constraint across ALL statuses, where the old index
-- only spanned non-Failed rows. If a duplicate pair already exists in the gap
-- between those two definitions, the constraint creation will fail — and
-- discovering that halfway through is worse than knowing now.
--
-- Expected: zero rows. (Measured before writing this: 151 leads, exactly one
-- signal each, zero duplicates.) If it returns anything, STOP and decide what
-- to do with those rows — do not let the constraint decide by failing.
--
--   select lead_id, event_name, count(*), array_agg(id), array_agg(status)
--   from meta_signal
--   group by lead_id, event_name
--   having count(*) > 1;


-- ── 1. The column ────────────────────────────────────────────────────────
--
-- default 'common' does double duty. New rows written by triggers that do not
-- yet know about datasets land in the common one, which is the correct
-- behaviour and not merely a placeholder. And it means the backfill below is
-- already true for every existing row the moment the column exists.
alter table meta_signal
  add column if not exists dataset_target text not null default 'common';

alter table meta_signal
  drop constraint if exists meta_signal_dataset_target_check;

alter table meta_signal
  add constraint meta_signal_dataset_target_check
  check (dataset_target in ('common', 'cream'));


-- ── 2. Backfill, and prove it ────────────────────────────────────────────
--
-- EVERY ROW SENT SO FAR WENT TO THE COMMON DATASET, because there has only
-- ever been one dataset (META_CAPI_DATASET_ID). So 'common' is not a guess
-- applied to unknown history — it is what actually happened. The default
-- above already covers existing rows; this is belt-and-braces for the case
-- where the column was added in an earlier partial run without the default.
update meta_signal
set dataset_target = 'common'
where dataset_target is null;

-- Fails loudly if anything is still unset, rather than letting step 4 build a
-- constraint over data that does not satisfy the assumption behind it.
do $$
declare
  v_unset int;
begin
  select count(*) into v_unset from meta_signal where dataset_target is null;
  if v_unset > 0 then
    raise exception
      'dataset_target is still null on % row(s) — backfill before indexing',
      v_unset;
  end if;
end $$;


-- ── 3. Drop the leaking index ────────────────────────────────────────────
--
-- Only now, with the replacement one statement away. The window where neither
-- exists is the length of this transaction.
drop index if exists idx_meta_signal_lead_event_active;


-- ── 4. The replacement: every status, plus the dataset ───────────────────
--
-- NO `where` CLAUSE. That absence is the entire fix — see the header. A Failed
-- row now occupies the slot for its (lead, event, dataset) triple, so a stage
-- re-entry cannot insert a second one alongside it, and the only way forward
-- is an UPDATE that preserves the id and therefore Meta's event_id.
--
-- dataset_target is part of the key so one lead can legitimately produce a
-- common CompleteRegistration AND a cream one — two different claims to two
-- different models about the same customer, which is the point of the second
-- dataset — while still being unable to produce two of either.
--
-- A CONSTRAINT rather than a bare index, unlike 0056: the triggers that write
-- these rows rely on `on conflict` to stay idempotent, and on conflict needs a
-- named constraint (or an exactly matching index predicate) to infer against.
-- A plain unique index works for inference too, but naming it makes the
-- trigger's intent explicit and survives someone adding a predicate later.
alter table meta_signal
  drop constraint if exists meta_signal_lead_event_dataset_key;

alter table meta_signal
  add constraint meta_signal_lead_event_dataset_key
  unique (lead_id, event_name, dataset_target);


-- ── 5. Triggers: NOT HERE ────────────────────────────────────────────────
--
-- fn_meta_signal_on_lead_arrival (0096) and fn_meta_signal_on_stage_history
-- (0057, amended by 0078/0092/0100) both still write exactly one row per
-- event, which is correct and safe under the new constraint — 'common' by
-- default, unchanged behaviour.
--
-- They change in a LATER migration, once the sender knows how to resolve a
-- dataset_target to a dataset id. Splitting it that way means this migration
-- is a pure structural change that alters no behaviour at all: apply it, and
-- the system does exactly what it did before, with the duplicate hole closed.


-- ── Reporting reads this column ──────────────────────────────────────────
--
-- send_quality and the per-dataset dashboard panels filter on dataset_target
-- (not on payload.dataset_id, which is a resolved numeric id that changes if a
-- dataset is swapped). Both are already-indexed-adjacent reads via the unique
-- constraint above for the (lead_id, event_name, dataset_target) prefix, but a
-- report groups by dataset_target ALONE — which that constraint cannot serve,
-- since lead_id leads it.
create index if not exists idx_meta_signal_dataset_target
  on meta_signal(dataset_target);
