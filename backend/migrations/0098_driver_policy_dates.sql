-- The three dates a broker reads off the DASH Policies summary.
--
-- DASH prints a policy history, newest first:
--
--   #1  2024-02-16 to 2026-02-16   Allstate Insurance Company   Active
--   #2  2018-02-16 to 2019-02-17   AVIVA General Insurance      Cancelled
--   #3  2016-02-16 to 2018-02-16   AVIVA General Insurance      Expired
--
-- All three of these columns are corner values of that table:
--
--   renewal_date           end date of the FIRST row    2026-02-16
--   insured_since          start date of the LAST row   2016-02-16
--   current_insurer_since  start date of the FIRST row  2024-02-16
--
-- WHY THEY ARE STORED AND NOT JUST DISPLAYED. The broker copies them into a
-- rating screen, and a value that vanishes on refresh is one they have to go
-- back to the PDF for. Stored also means correctable: the same blank-only rule
-- every other DASH field follows, so a broker's edit survives a re-parse.
--
-- NO GAP INTERPRETATION. The sample above has a five-year hole between #2 and
-- #1, which DASH marks *POSSIBLE GAP* — and its own header then reports "Years
-- of Continuous Insurance: 1", not the ten years the earliest start date would
-- suggest. These columns record the dates as the summary prints them; whether
-- a gap breaks continuity is a judgement the broker makes with the report in
-- front of them, and encoding a guess here would put a number in a quote that
-- nobody could trace back to anything on the page.
--
-- `date`, NOT text. They are dates, the broker corrects them with the same
-- picker as the licensing ones, and a text column would accept "next Feb".
--
-- Nullable and not backfilled: most drivers have no DASH report, and null
-- reads correctly as "not known yet" rather than as a date of zero.

alter table driver add column if not exists renewal_date          date;
alter table driver add column if not exists insured_since         date;
alter table driver add column if not exists current_insurer_since date;

comment on column driver.renewal_date is
  'End date of the most recent policy on the DASH summary — when the customer''s current cover runs out.';
comment on column driver.insured_since is
  'Start date of the OLDEST policy on the DASH summary. Printed as-is: gaps are not interpreted, see 0098.';
comment on column driver.current_insurer_since is
  'Start date of the most recent policy — when the customer joined the insurer they are with now.';
