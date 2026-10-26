-- The commission projection: two rates, and nothing else.
--
-- 0083 deleted the old module — ~2,400 lines, 11 backend files, 3 migrations
-- and 2 screens — and said its replacement would be designed from a blank
-- page. This is that page. Nothing here is carried over.
--
-- THE WHOLE FEATURE:
--
--   auto commission     = new_auto_premium_annual        x auto_rate_pct %
--   property commission = (new_homeowner_premium_annual
--                        + new_tenant_premium_annual)    x property_rate_pct %
--
-- computed WHEN A REPORT IS OPENED. No commission is stored anywhere, so
-- there is nothing that can fall out of date and nothing to recalculate.
--
-- That is the difference from what was deleted. The old module stored a
-- commission ROW PER PRODUCT, which is why every premium edit, every stage
-- change and every policy-type switch had to rebuild it — five call sites,
-- and every "money risk" a later audit found came from that fan-out. Multiply
-- on read and the fan-out does not exist.
--
-- WHAT THIS MEANS IN PRACTICE, both accepted deliberately:
--   * a broker corrects a premium  -> the projection follows it. Correct.
--   * an admin changes a rate      -> every projection uses the new one,
--                                     including months already reported.
--
-- The second one was nearly solved by stamping the two rates onto each policy
-- at issue, so a past month would freeze. It was dropped: two more columns,
-- two more things written at Issued, and a rule to explain, to protect
-- figures nobody is paid from. These are reference numbers a broker reads,
-- not a payment record.
--
-- NO NEW COLUMN ON premium_history. The premiums are already there, and
-- issued_at has been there since 0067 — what changes about it is in Python
-- (the broker now confirms the date instead of it being today automatically),
-- not in the schema.

create table commission_settings (
  -- A FIXED KEY, not a uuid. The CHECK is what makes a second row impossible,
  -- and it gives the service a stable id to update against without a lookup
  -- first. Same shape lead_allocation_state (0018) uses for the same reason.
  id int primary key default 1 check (id = 1),

  -- Percentages, not fractions: 15 means 15%. Stored the way an admin types
  -- them and the way the screen shows them, so nothing has to remember which
  -- form it is holding.
  --
  -- The upper bound is not pedantry. A rate typed as 1500 instead of 15 would
  -- report a projection a hundred times too large, and a broker would believe
  -- it before they questioned it.
  auto_rate_pct numeric not null check (auto_rate_pct between 0 and 100),

  -- ONE PROPERTY RATE for homeowner AND tenant — and condo, which has no
  -- premium columns of its own and rides on the homeowner pair (see
  -- PRODUCTS_FOR in schemas/lead.py). The deleted design had three separate
  -- property rates and they were never once set to different values.
  property_rate_pct numeric not null check (property_rate_pct between 0 and 100),

  created_at timestamptz not null default now(),
  updated_at timestamptz not null default now()
);

create trigger trg_commission_settings_updated_at
  before update on commission_settings
  for each row execute function set_updated_at();

-- Deny-all, like every other table here: no policies, so only the service
-- role reaches it.
alter table commission_settings enable row level security;

-- SEEDED WITH REAL NUMBERS, not nulls. The calculation has to answer on its
-- first day, and a null rate would need special-casing in every read for a
-- state that exists only until an admin first opens the screen.
insert into commission_settings (auto_rate_pct, property_rate_pct)
values (15, 15);
