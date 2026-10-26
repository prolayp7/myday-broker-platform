-- Make a failed lead ingestion actually recoverable.
--
-- THE PROMISE THAT WAS NOT KEPT. services/lead_ingestion.py's docstring says
-- a failed event is "always recoverable later ... never lost data", and the
-- raw payload really is stored forever. But ingest_meta_event has exactly ONE
-- caller — the webhook, for the lead it just received. Nothing sweeps failed
-- rows, and there is no replay route. "Recoverable" meant someone writing SQL
-- by hand.
--
-- Worse, Meta's own retries cannot rescue it either: record_meta_event dedups
-- on leadgen_id, so a redelivery of a FAILED event is discarded as a duplicate
-- before ingestion is reconsidered. The one mechanism that would naturally
-- retry is deliberately suppressed.
--
-- Found on the live database: an event had sat in status='failed' for nine
-- days with
--   "Could not fetch lead data from Meta: [WinError 10061] ..."
-- — a transient network failure that would have succeeded on any later
-- attempt, and that nothing would ever try again.
--
-- That particular event turned out to be a SIMULATOR artefact (leadgen_id
-- 'sim-...', from the local meta-lead-simulator), so no customer was lost. The
-- mechanism that would have lost one was real either way, and is what this
-- migration closes.
--
-- WHAT THIS ADDS. Two columns, so a background drain can tell "worth trying
-- again" from "tried enough":
--
--   attempt_count   how many ingestion attempts this event has had
--   next_retry_at   the earliest time to try again (exponential backoff)
--
-- The shape is copied from meta_signal (migration 0056 + services/
-- meta_signal.py), which already solves this exact problem for outbound CAPI
-- events: attempt counting, a capped number of tries, and a terminal state so
-- a genuinely broken row surfaces to a human instead of retrying forever.
--
-- WHY BACKOFF AND NOT A FIXED INTERVAL. The failures worth retrying are
-- transient (Meta 5xx, a network blip, a token being rotated); the ones that
-- are not (an unmapped form, a payload that does not match the leadgen shape)
-- will fail identically every time. Backoff costs almost nothing on the first
-- case and stops the second from hammering Meta on a loop until it hits the
-- attempt cap.
--
-- NOT A NEW STATUS. 'failed' still means "the last attempt failed". Whether it
-- will be tried again is next_retry_at's job, and giving up is attempt_count
-- reaching the cap. A fourth status would have to be kept in sync with both
-- and could disagree with them.

alter table webhook_event
  add column attempt_count int not null default 0;

-- Null means "not scheduled for retry" — which covers both a row that has
-- never failed and one that has given up. The drain reads
-- `status = 'failed' and next_retry_at <= now()`, so null is simply never
-- picked up, and there is no separate "exhausted" flag to keep consistent.
alter table webhook_event
  add column next_retry_at timestamptz;

-- The drain's exact query. Partial, because the vast majority of rows are
-- 'processed' and must never be scanned: a retry sweep should touch only the
-- handful of rows that are actually waiting.
create index idx_webhook_event_retry
  on webhook_event (next_retry_at)
  where status = 'failed' and next_retry_at is not null;

-- BACKFILL. Rows that failed before this migration have attempt_count 0 and a
-- null next_retry_at, so the drain would ignore them forever — including the
-- real stranded lead that prompted this. Schedule them all for an immediate
-- first retry.
--
-- attempt_count stays 0 deliberately: those earlier attempts were made by a
-- system that had no intention of trying again, so counting them would spend
-- part of the new budget on history. Each old row gets a full set of tries.
update webhook_event
set next_retry_at = now()
where status = 'failed';
