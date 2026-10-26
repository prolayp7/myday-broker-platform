-- The broker's share of commission: one percentage, the same for everyone.
--
-- 0084 holds the two rates a policy's commission is worked out from (auto, and
-- home/condo/rental/tenant). This adds what the BROKER keeps of it, so My Day
-- can show a broker their own commission earned:
--
--   commission = new_auto_premium_annual x auto_rate_pct %
--              + (new_homeowner_premium_annual + new_tenant_premium_annual)
--                                        x property_rate_pct %
--   broker earns broker_share_pct % of that
--
--   $2,400 auto x 12.5% = $300 commission; at 55% the broker earns $165.
--
-- ONE SPLIT FOR EVERY BROKER, decided by the owner on 2026-10-25. The old
-- per-broker override columns went with the commission module in 0083, and
-- are not coming back with this: a setting nobody has to maintain per person
-- is the simple version, and an override can be added later without touching
-- this one.
--
-- Same rules as the two rates beside it: worked out when a screen opens,
-- nothing stored per policy, a change applies to every month shown.
--
-- SAFE TO RE-RUN. APPLY BY HAND in the Supabase SQL editor, staging first.

begin;

alter table commission_settings
  add column if not exists broker_share_pct numeric not null default 55
  check (broker_share_pct between 0 and 100);

comment on column commission_settings.broker_share_pct is
  'Percent of a policy''s commission the broker keeps (55 = 55%). The same for every broker; set on Admin > Commission rates.';

commit;

-- After applying:
--
--   select auto_rate_pct, property_rate_pct, broker_share_pct from commission_settings;
--     production today: 12.5 / 20 / 55
