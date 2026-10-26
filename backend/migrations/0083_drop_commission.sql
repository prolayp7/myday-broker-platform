-- Drop the commission module entirely.
--
-- It was the most complicated thing in this codebase and it never produced a
-- single row: ~2,400 lines across 11 backend files, 3 migrations and 2
-- screens, holding eleven concepts — per-carrier rates, a house split,
-- per-broker overrides, rates copied onto each row, three generated payout
-- columns, delete-then-rebuild recalculation, Ontario bucketing — to compute
-- money that is in fact paid outside this system entirely.
--
-- The real cost was not its size. Commission was not a screen you visited; it
-- was a SIDE EFFECT on three unrelated user actions, firing from five call
-- sites in services/lead.py and services/premium_history.py. Every "money
-- risk" a recent audit found came from that fan-out.
--
-- It will be redesigned from a blank page: one auto rate, one property rate,
-- annual premium x rate. Nothing here is reused.
--
-- PREMIUMS ARE NOT PART OF THIS. premium_history keeps every column, because
-- total_new_premium_annual is what Meta's Purchase event sends as its `value`
-- (meta_capi.py, read by the trigger in 0078). Without it Meta's optimisation
-- cannot see revenue, which is the reason CAPI exists at all.
--
-- THE INSURER TABLE GOES TOO. An earlier plan kept it as a carrier name list,
-- arguing that "which carrier wrote the policy is hard to reconstruct later".
-- The live data did not support that: of three premium rows, ONE carried an
-- insurer_id (a test row), and no policy number or effective date had ever
-- been entered. There was no history to preserve — only a table, a CRUD
-- stack, a dropdown and an embed kept alive for one test row.
--
-- CODE FIRST, THEN THIS. Deploy the application change before applying this
-- file. repositories/broker.py::SAFE_COLUMNS listed the three share columns
-- and is read by EVERY broker query, so code naming a dropped column breaks
-- broker lists, detail and profile — not merely commission.

begin;

-- 1. commission FIRST: it has foreign keys to lead, broker AND insurer, so
-- nothing below can be dropped while it exists. Takes its three indexes, its
-- updated_at trigger, its three generated payout columns and every CHECK with
-- it.
drop table commission;

-- 2. The house split. Its updated_at trigger and both CHECKs (the id = 1
-- singleton guard and the shares-total-100 constraint) go with the table.
drop table brokerage_settings;

-- 3. The per-broker override. Null on all three meant "use the house split",
-- which was the ordinary case for every broker who ever existed.
alter table broker
  drop column broker_share_pct,
  drop column brokerage_share_pct,
  drop column principal_share_pct;

-- 4. Before the insurer table itself — this is the foreign key holding it.
alter table premium_history drop column insurer_id;

-- 5. LAST, now that nothing references it. The 20 seeded carriers go with it;
-- they were never anything but a place to hang three rate columns.
drop table insurer;

commit;

-- NO CASCADE ANYWHERE, deliberately. The order above resolves every
-- dependency explicitly, so a dependency this analysis missed fails loudly
-- here rather than being silently dropped along with whatever else referenced
-- it. On a destructive migration that is the behaviour you want.
--
-- Historical migrations are NOT edited: 0055, 0067 and 0069 stay exactly as
-- they were. They are the record of what the schema used to be, and rewriting
-- them would make the history lie.
