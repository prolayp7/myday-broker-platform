-- The policyholder printed on a CLAIM, which DASH gives per claim and we were
-- dropping.
--
-- Every claim detail page opens with its own Policyholder Name / Address:
--
--   Claim #1  Date of Loss 2025-07-01  Allstate Insurance ...  At-Fault : 0%
--   Policyholder Name: Vallomkunnel,Francis        Coverage: DCPD
--   Policyholder Address: 5 FESTIVAL DR, , NORTH YORK ON M2R3T9
--
-- Not a duplicate of dash_policy.policyholder_name. A claim can cite a policy
-- that is not detailed anywhere in this report — third-party claims routinely
-- reference the OTHER party's insurer and policy — so dash_claim.dash_policy_id
-- is null for those, and the claim's own copy is the only record of who held
-- the policy it was made against.
--
-- Stored AS PRINTED like every other DASH value: '******' and 'Not available'
-- are real, meaningful states, not blanks.
alter table dash_claim
  add column policyholder_name text,
  add column policyholder_address text;

comment on column dash_claim.policyholder_name is
  'Policyholder on THIS claim, as printed. May differ from the linked '
  'policy''s, and is the only copy when the claim links to no policy.';
