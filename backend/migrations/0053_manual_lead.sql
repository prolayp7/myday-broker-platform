-- Leads a broker types in themselves — a referral, a walk-in, someone who
-- called the office.
--
-- NO NEW TABLE. A manual lead is an ordinary lead that happened to arrive by
-- phone rather than through an ad: it is worked, staged, quoted, reassigned and
-- reported on exactly like any other. `lead.source` already exists to say where
-- a lead came from, so this widens three CHECK constraints and nothing else.
--
-- A separate table would have to be UNIONed into every list, count and filter
-- in the system — and worse, driver/vehicle/property all carry a foreign key to
-- lead(id), so a manual lead living elsewhere could not have a driver, and
-- therefore could not be quoted at all.
--
-- THE DAILY CAP NEEDS NO CHANGE. It counts rows in lead_delivery, which only
-- assign_lead writes (0019). A manual lead goes straight to the broker who took
-- the call without passing through the round-robin engine, so no delivery row
-- exists and the lead is invisible to the cap. That is the behaviour we want
-- and it is already true — the cap governs the distribution of paid ad leads,
-- not calls a broker handled personally.

-- ── 1. Where the lead came from ─────────────────────────────────────────
-- 'manual' joins meta/website/tiktok (0017). Those three are channels the
-- system ingests from; this one is a person typing.
alter table lead drop constraint if exists lead_source_check;
alter table lead add constraint lead_source_check check (
  source in ('meta', 'website', 'tiktok', 'manual')
);

-- ── 2. Who decided the assignment ───────────────────────────────────────
-- 'system' is the round-robin engine, 'admin' is a hand-out from the pool
-- (0018). Neither describes a broker creating a lead already on the phone to
-- the customer, so 'broker' is its own answer rather than being folded into
-- one of the others.
alter table lead drop constraint if exists lead_assigned_by_check;
alter table lead add constraint lead_assigned_by_check check (
  assigned_by is null or assigned_by in ('system', 'admin', 'broker')
);

-- ── 3. Why this custody row exists ──────────────────────────────────────
-- Every lead gets a lead_assignment row saying how it reached its broker
-- (0023). 'Initial Claim' means the engine chose them; 'Manual Entry' means
-- there was nothing to choose — the broker who typed the lead is the broker who
-- was speaking to the customer. Reusing 'Initial Claim' would file a
-- hand-created lead as an engine decision and quietly corrupt any later
-- reporting on how leads actually arrive.
alter table lead_assignment drop constraint if exists lead_assignment_reason_check;
alter table lead_assignment add constraint lead_assignment_reason_check check (
  reason in ('Initial Claim', 'Admin Claim', 'Admin Reassign', 'Manual Entry')
);
