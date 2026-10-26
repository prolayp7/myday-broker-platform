-- 'NA' joins the relationship vocabulary, for drivers a DASH report added.
--
-- WHY A NEW VALUE RATHER THAN NULL. The column is already nullable, so an
-- imported driver could simply have no relationship — but null here means "the
-- row was created before this question was asked", which is the applicant's
-- own case (repositories/driver.py seeds it as 'Applicant'). An imported
-- operator is a different state: a person we KNOW is on the policy, whose
-- relationship to our applicant nobody has stated yet. 'NA' says that out
-- loud, and the New Policy row can render it as a prompt rather than as an
-- empty control that looks unfinished for no stated reason.
--
-- IT IS ALSO WHAT DASH ITSELF PRINTS. Every operator block in the real report
-- this was built against reads "Relationship to Policyholder: NA" — and note
-- POLICYHOLDER, not applicant. The two are different people whenever the
-- customer is not the one who owns the policy, so the report's value is not a
-- source for our field even when it is filled in. 'NA' here means "not yet
-- established", which is true regardless of what DASH said.
--
-- NOT OFFERED IN THE ADD DRIVER DIALOG. A broker adding a spouse by hand knows
-- who they are; the choice exists only so an import has something honest to
-- write. The dialog's list (frontend AddDriverDialog.tsx) deliberately stays
-- as it is.
--
-- DROP THEN ADD, in that order. A CHECK constraint is validated per statement,
-- so there is no way to widen one in place — and the same lesson is written
-- into 0067, whose first attempt failed exactly here. Dropped BY NAME because
-- 0035 declared it inline and Postgres named it predictably; `if exists` keeps
-- this re-runnable.
alter table driver drop constraint if exists driver_relationship_to_applicant_check;

alter table driver add constraint driver_relationship_to_applicant_check check (
  relationship_to_applicant in (
    'Applicant', 'Spouse', 'Common Law', 'Same Sex Partner',
    'Co-habitant', 'Child', 'Sibling', 'Parent',
    -- Added here. Last in the list because it is not a relationship — it is
    -- the absence of one having been chosen.
    'NA'
  )
);
