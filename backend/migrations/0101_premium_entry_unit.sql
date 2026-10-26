-- HOW THE BROKER TYPED IT, so the number can be read back unambiguously.
--
-- The premium boxes store exactly what is typed and convert nothing (4c963fe,
-- 43aad35). That is deliberate: every attempt to convert WHILE TYPING failed
-- — four rewrites in eight hours, one of which silently divided stored figures
-- and one of which multiplied by twelve twice.
--
-- Conversion happens once, at the move to Quote Done, which is the only moment
-- the annual figure is consumed (fn_meta_signal_on_stage_history reads
-- total_new_premium_annual the instant the stage row is written, and Meta
-- deduplicates on event_id — so a figure corrected afterwards sends nothing).
--
-- WHY A COLUMN AND NOT A REQUEST FIELD. Until now the unit lived only in React
-- state, so reloading the page lost whether 150 meant $150 a month or $150 a
-- year. A figure whose unit is unknown is worse than no figure: it cannot be
-- corrected, because nobody can tell whether it is wrong. That ambiguity is
-- the root of every premium bug this feature has had.
--
-- TWO COLUMNS, NOT ONE. Current and Proposed are read off different documents
-- — the customer's existing policy and our own quote — and a broker can
-- genuinely have one in each unit.
--
-- THEY ALSO MAKE THE CONVERSION IDEMPOTENT, which matters because the premium
-- write and the stage write are NOT in one transaction (update_stage is a
-- separate RPC). If conversion succeeds and the stage write then fails, the
-- row is already annual — and without a flag a retry would multiply it again.
-- The service writes the six converted figures AND both units back to 'year'
-- in the same single UPDATE, so a retry reads 'year' and multiplies by
-- nothing.
--
-- DEFAULT 'year', which is not a guess: every figure stored before this
-- migration is annual by definition (0099 made annual the only stored unit),
-- so all existing rows are already correct under the default.

alter table premium_history
  add column current_premium_unit text not null default 'year'
    check (current_premium_unit in ('year', 'month')),
  add column new_premium_unit text not null default 'year'
    check (new_premium_unit in ('year', 'month'));

comment on column premium_history.current_premium_unit is
  'How the broker typed the three current_* figures. Reset to year when the lead reaches Quote Done and the figures are converted.';

comment on column premium_history.new_premium_unit is
  'How the broker typed the three new_* figures. Reset to year when the lead reaches Quote Done and the figures are converted.';
