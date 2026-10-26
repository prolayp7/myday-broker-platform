-- The three facts the qualification step records, and nothing else.
--
-- WHAT THIS IS FOR. Meta's CompleteRegistration is moving from Quote Done to In
-- Progress so the ad model hears about a qualified lead days earlier. At that
-- moment the broker answers a short set of questions, and a second "cream"
-- dataset (meta_signal.dataset_target, 0112) is trained only on premium
-- customers. These are the columns those two things read. Every other field the
-- flow needs already exists — insured_since, combined_policy_type, is_qualified,
-- stage/sub_stage, dataset_target, failure_alerted_at were all checked against
-- the live database before this was written.
--
-- COLUMNS ONLY, DELIBERATELY. No stage promotion, no trigger changes, no
-- classifier. In Progress is still a sub-stage of New here, exactly as it is
-- today, and nothing about signalling changes. Applying this file alters no
-- behaviour whatsoever — it only makes somewhere for the answers to go, so the
-- parsing and the dialog can be built against real columns instead of being
-- written and wired in the same breath.
--
-- ALL THREE NULLABLE, and that is not an oversight. A lead arrives before
-- anyone has judged it and long before a DASH report is pulled; 114 of 162 Meta
-- leads have no policy type today and every one of them is at New or Lost. A
-- missing answer must never block a lead from existing — the same rule 0108
-- states for licensed_in_canada, and meta_lead_parser for name/phone/email.
--
-- NULL MEANS UNANSWERED, NEVER ZERO. This is the distinction the whole cream
-- classifier rests on: "no at-fault claims" and "we have not looked" are
-- opposite facts, and a default of 0 would send an unvetted lead to the premium
-- dataset looking like a clean record. Hence no defaults anywhere below.
--
-- APPLY BY HAND in the Supabase SQL editor, like every migration in this repo.


-- ── driver.at_fault_claims_6y ────────────────────────────────────────────
--
-- Read from ONE line in the DASH report header:
--
--   Number of At-Fault Claims in Last 6 Years: 2
--
-- DASH'S OWN COUNT, not ours. The report also prints a Claims table listing each
-- claim with an at-fault percentage (0%, 50%, 100%) and including *THIRD PARTY*
-- rows that belong to other people — counting those rows would mean deciding
-- whether 50% is at-fault and whose claim is whose. The header has already made
-- both judgements.
--
-- MEASURED BEFORE BUILDING: 25 real reports out of the 137 in C:\Auto Insurance
-- were read; all 25 carried this line. Values seen were 0 (fifteen reports),
-- 1 (nine) and 3 (one).
--
-- On the DRIVER, not the lead: it is a fact about a person's record, and a lead
-- can carry several drivers. The classifier reads the principal.
alter table driver add column at_fault_claims_6y int
  check (at_fault_claims_6y >= 0);


-- ── driver.convictions_3y ────────────────────────────────────────────────
--
-- The broker's answer, not DASH's. Convictions sit on the MVR abstract rather
-- than the DASH report, and the broker has the customer on the phone at exactly
-- the moment this is asked — so it is typed, not parsed.
--
-- On the DRIVER for the same reason as at-fault: it describes the person.
alter table driver add column convictions_3y int
  check (convictions_3y >= 0);


-- ── lead.vehicle_count_declared ──────────────────────────────────────────
--
-- What the customer SAYS they have, which is why the name says "declared". It is
-- deliberately not the count of rows in `vehicle`: those are typed in later from
-- the DASH report, are often incomplete while a lead is still at In Progress,
-- and describe the cars on the previous policy rather than the ones the customer
-- wants covered. Storing the declared figure separately keeps the two from being
-- mistaken for each other.
--
-- On the LEAD, unlike the two above: a household's car count belongs to the
-- quote, not to any one driver on it.
--
-- RECORDED, NOT A GATE. The cream definition captures it but does not threshold
-- on it — see the note in the plan. It exists so the shape of the book can be
-- measured before any rule is written against it.
alter table lead add column vehicle_count_declared int
  check (vehicle_count_declared >= 0);


-- ── no indexes, on purpose ───────────────────────────────────────────────
--
-- Nothing filters or sorts on these yet. 0108 added an index with its column
-- because the admin lead list genuinely filters on that qualifier; here the
-- classifier reads them one lead at a time, by primary key, through a row it has
-- already fetched. An index on a column nothing queries costs write time on
-- every insert and buys nothing — add one when a screen or report needs it.


-- ── the CHECK constraints ────────────────────────────────────────────────
--
-- >= 0 on all three because a negative count is meaningless and there is no
-- backend guard yet: the dialog offers buttons rather than a free number box, so
-- a negative can only arrive from a crafted request or a future code path that
-- forgets. The database refusing it is one line, and it never drifts.
--
-- NO UPPER BOUND. "5+" in the dialog stores 5, but a broker correcting a record
-- later, or a DASH report on a genuinely dreadful history, must not hit a
-- ceiling this file invented.
