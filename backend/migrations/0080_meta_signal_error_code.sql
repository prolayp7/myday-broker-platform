-- Meta's own numeric error code on a failed signal.
--
-- WHY. error_message already holds Meta's text, which a person can read — but
-- the dashboard needs to tell two very different failures apart WITHOUT
-- reading prose:
--
--   code 190   the ACCESS TOKEN expired or was revoked. Nothing is wrong with
--              the event. Replace the token and the same rows send fine.
--   anything   the payload itself was rejected — a stale event_time, a bad
--   else       hash. Resending the identical body cannot start working.
--
-- That distinction is the whole point of the admin screen. A token expiring
-- overnight fails EVERY signal queued in that window, and until now they were
-- marked Failed with no way back: list_pending reads only 'Pending', so a
-- Failed row was never picked up again by anything. Those conversions were
-- simply lost, and nothing anywhere said so.
--
-- INT, not text. Meta documents these as numbers, and the screen groups by
-- them ("4 signals failed with code 190") — which a free-text column makes
-- awkward for no benefit.
--
-- NULLABLE, and no backfill. A timeout or a DNS failure has no Meta code at
-- all, so null means "this did not come from Meta's API". Every existing row
-- predates the column and stays null, which is honest: we did not record it.
alter table meta_signal add column error_code int;

-- The dashboard's own read: "show me what failed, worst first". Partial,
-- because Sent rows are the overwhelming majority in normal operation and none
-- of them has an error code to index.
create index idx_meta_signal_error_code
  on meta_signal(error_code)
  where error_code is not null;
