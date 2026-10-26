-- A failed signal tells someone ONCE.
--
-- THE PROBLEM THIS SOLVES. A Failed meta_signal row sits on an admin screen
-- nobody has a reason to open. The CAPI dashboard exists and is good, but
-- nothing prompts anyone to look at it — so the failure that matters (a dead
-- token, silently dropping every conversion queued behind it) is discovered
-- days later by noticing the campaign has gone quiet. That is how the WhatsApp
-- channel went 12 days unnoticed with 77 consecutive failures.
--
-- WHY A COLUMN AND NOT A QUERY. "Alert when failed rows exist" is
-- level-triggered: it is true again on the very next drain pass, and the pass
-- after that, forever, until someone fixes the row. Fifteen minutes apart. The
-- recipient mutes the sender within a day and then misses the one that
-- mattered, which is strictly worse than never having alerted at all.
--
-- Migration 0111 made exactly this argument about the WhatsApp channel and
-- acted on it: "a channel that sends noise is a channel people mute. Losing the
-- real alerts to habituation is a far worse outcome than missing this one."
-- Same reasoning, different channel.
--
-- So the alert is EDGE-TRIGGERED: it fires for rows that have NEWLY become
-- terminal. This column is the memory that makes "newly" answerable. Without
-- stored state the sender cannot distinguish a failure it has already reported
-- from one it has not, because the drain holds nothing across passes and the
-- process restarts on every deploy.
--
-- WHY NOT A SEPARATE TABLE. One nullable timestamp answers the only question
-- ever asked of it ("has this row been reported?"), it is read in the same
-- query that finds the failures, and it cannot drift out of step with the row
-- it describes. A side table would need its own cleanup when a signal is
-- deleted; this cannot, because it IS the row.
--
-- WHY IT IS CLEARED ON RE-QUEUE, not just set. A row repaired and then failing
-- again is NEW news — a token replaced on Monday and dying again on Friday is
-- two incidents, not one. Both retry paths (reset_to_pending for a single row,
-- reset_code_190_to_pending for the bulk fix) clear this, so the next failure
-- alerts again. Left uncleared, the second incident would be silent, which is
-- the same invisibility in a subtler form.
--
-- A RATE-LIMITED ROW NEVER REACHES THIS AT ALL. Under the throttle rule in
-- services/meta_signal.py (_RATE_LIMIT_CODES), HTTP 429 and Meta codes 4/17/32
-- stay Pending regardless of attempt_count — they are not terminal, so there is
-- nothing to report. Being throttled is not a failure to tell anyone about.
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.
-- Apply 0112 FIRST: it is the structural change this one assumes.


-- Null means "this failure has not been reported yet", which is the correct
-- state for every row that exists today — including the rows already Failed.
-- Those will produce ONE alert on the next pass after this ships, naming
-- failures that are real and currently unreported. That is the right outcome,
-- not a backfill candidate: silencing them would mean this feature's first act
-- is to hide the exact failures it was built to surface.
alter table meta_signal
  add column if not exists failure_alerted_at timestamptz;


-- The alert query is "terminal AND not yet reported", so it reads Failed rows
-- with a null mark. Partial on both conditions because that is the whole access
-- pattern and it stays tiny — a handful of rows at most, against a table that
-- grows with every lead.
create index if not exists idx_meta_signal_unalerted_failures
  on meta_signal(created_at)
  where status = 'Failed' and failure_alerted_at is null;


-- NOTE FOR WHOEVER ADDS THE SEND. The Python side must, in this order:
--
--   1. read the unalerted terminal rows,
--   2. send ONE message describing all of them together,
--   3. stamp failure_alerted_at on exactly the rows it just described.
--
-- Stamping BEFORE a successful send loses the alert if the send fails; sending
-- per row reintroduces the noise this column exists to prevent; and stamping
-- rows it did not describe (a blanket update) silences failures that arrived
-- between the read and the write. Same three-step shape as the WhatsApp drain,
-- and the same reasons.
