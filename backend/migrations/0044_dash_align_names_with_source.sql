-- Rename DASH columns to match the labels DASH itself prints on the PDF.
--
-- WHY (so this isn't re-litigated later): every DASH column is filled by
-- reading a labelled line off the report. When the column name and the printed
-- label differ, there's a silent translation step in between — and that's
-- exactly where a mapping error hides, because nothing fails loudly when the
-- wrong source line is wired to a plausibly-named column. Naming the column
-- after the label the extraction reads removes that step: "End of the Latest
-- Term" -> end_of_latest_term, and there is nothing left to get wrong.
--
-- This matters most for the two term-date pairs, which are genuinely different
-- data that reads as interchangeable if you only see the old names:
--
--   * effective_date / expiry_date       <- the POLICY's own start and end,
--                                            from the policy header line.
--   * start_of_earliest_term /           <- when the SUBJECT was added to, and
--     end_of_latest_term                    removed from, that policy. Often a
--                                            narrower range than the policy's.
--
-- Under the old names (earliest_term_start / latest_term_end) the distinction
-- was carried only by a comment. Under DASH's own labels it is self-evident.
--
-- The first/third-party claim fields get the same treatment: DASH labels them
-- "First Party Driver DLN", "Third Party Driver At-Fault", etc. — the "Driver"
-- was being dropped, leaving first_party_dln, which reads like a property of
-- the claim's first party rather than of its driver.
--
-- Safe to rename in place: DASH parsing was never wired up (the extraction
-- stubs still raise NotImplementedError), so these tables hold no data.

-- === dash_policy: the subject's own term on the policy ===
alter table dash_policy rename column earliest_term_start to start_of_earliest_term;
alter table dash_policy rename column latest_term_end   to end_of_latest_term;

-- === dash_report: match "Years of Continuous Insurance" ===
alter table dash_report rename column years_continuous_insurance to years_of_continuous_insurance;

-- === dash_claim: DASH labels these "<N> Party Driver <field>" ===
alter table dash_claim rename column first_party_dln              to first_party_driver_dln;
alter table dash_claim rename column first_party_dln_province     to first_party_driver_dln_province;
alter table dash_claim rename column first_party_date_of_birth    to first_party_driver_date_of_birth;
alter table dash_claim rename column first_party_listed_on_policy to first_party_driver_listed_on_policy;
alter table dash_claim rename column first_party_excluded         to first_party_driver_excluded;
alter table dash_claim rename column first_party_at_fault         to first_party_driver_at_fault;

alter table dash_claim rename column third_party_dln          to third_party_driver_dln;
alter table dash_claim rename column third_party_dln_province to third_party_driver_dln_province;
alter table dash_claim rename column third_party_insurer_name to third_party_driver_insurer_name;
alter table dash_claim rename column third_party_policy_number to third_party_driver_policy_number;
alter table dash_claim rename column third_party_at_fault     to third_party_driver_at_fault;

-- NOT renamed, deliberately:
--   * claims_last_6y and the other three 6-year counters — the exact labels
--     ("Number of At-Fault Claims in Last 6 Years") make for 40+ character
--     column names, and the short forms are already unambiguous.
--   * type_of_use_vehicle_class — the label is "Type of Use / Vehicle Class";
--     the slash can't appear in a column name, so an exact match isn't
--     possible here either way.
--   * first_party_driver / third_party_driver already match their labels.
