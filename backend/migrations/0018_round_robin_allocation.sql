-- Round-robin lead allocation — SCHEMA.
--
-- Full design: backend/docs/round-robin-allocation-design.md. This migration
-- is the data-model foundation only (§8); the assignment function (0019) and
-- daily reset job (0020) build on top of it.
--
-- Model in one line: the system assigns every lead. Group 1 = guaranteed
-- delivery via round-robin up to each broker's daily_cap; once every eligible
-- Group-1 broker is at cap (the gate), Group 2 = ready-for-more brokers get
-- overtime up to their max_cap. Brokers never claim leads themselves.
--
-- All scheduling times are Ontario time (America/Toronto); the reset job in
-- 0020 anchors "today".

-- =====================================================================
-- NEW TABLES
-- =====================================================================

-- Remembers whose turn is next per group, AND is the row every assignment for
-- a group locks on (pg_advisory_xact_lock) so concurrent webhooks serialize
-- rather than both handing the "next" lead to the same broker. One row per
-- real broker_group, created lazily the first time a group assigns a lead.
create table broker_group_rotation (
  group_id uuid primary key references broker_group(id),
  -- The broker who most recently received a Group-1 lead in this group. The
  -- assignment function starts scanning from the broker AFTER this one, which
  -- is what makes the rotation fair. Null before the group's first assignment.
  last_assigned_broker_id uuid references broker(id),
  updated_at timestamptz not null default now()
);

alter table broker_group_rotation enable row level security;
-- Deny-all: no policies defined. Only service_role (our FastAPI backend)
-- can access this table; direct anon/authenticated Supabase access is
-- fully blocked until a specific need arises.

create trigger trg_broker_group_rotation_updated_at
  before update on broker_group_rotation
  for each row
  execute function set_updated_at();

-- The single "when did today start" clock. Exactly one row, ever. The daily
-- reset job (0020) advances last_reset_at each morning; every cap-count query
-- reads it to know the window for "leads delivered today". A single-row table
-- (not a per-broker column) because the reset is global — one clock for the
-- whole system, in Ontario time.
create table lead_allocation_state (
  -- Fixed single-row guard: the id is always this constant, so a second insert
  -- collides on the primary key. There is only ever one allocation clock.
  id boolean primary key default true check (id),
  last_reset_at timestamptz not null default now()
);

alter table lead_allocation_state enable row level security;
-- Deny-all, as above.

insert into lead_allocation_state (id) values (true);

-- The immutable record of SYSTEM deliveries, and the source of truth for the
-- daily-cap count. One row per lead the round-robin engine hands to a broker,
-- written in the SAME locked transaction as the assignment. Crucially it is
-- APPEND-ONLY: an admin reassigning a lead A -> B writes NOTHING here, so A's
-- delivery slot stays spent (doesn't come back) and B's cap is untouched — the
-- cap count can never silently shift because the rows it counts never move.
--
-- Why a dedicated table and not audit_log: this count is load-bearing for
-- correctness (it decides whether a broker is at cap), so it must not depend on
-- an observability table's write succeeding. audit_log stays purely
-- observational; this table is the correctness record. A reassignment is still
-- logged to audit_log for history — that just doesn't affect the count here.
create table lead_delivery (
  id uuid primary key default gen_random_uuid(),
  lead_id uuid not null references lead(id),
  -- The broker the SYSTEM delivered this lead to. Never updated — if the lead
  -- is later reassigned, that's a separate fact recorded elsewhere, not a
  -- rewrite of this row.
  broker_id uuid not null references broker(id),
  delivered_at timestamptz not null default now()
);

-- The cap count is "deliveries to broker B since today's reset" — index the
-- pair it filters and groups on.
create index idx_lead_delivery_broker_at on lead_delivery(broker_id, delivered_at);
-- One system delivery per lead: the round-robin engine assigns a given lead
-- exactly once. (Reassignment doesn't insert here, so this stays true.)
create unique index idx_lead_delivery_lead on lead_delivery(lead_id);

alter table lead_delivery enable row level security;
-- Deny-all, as above.

-- =====================================================================
-- BROKER — new allocation columns
-- =====================================================================
-- Reused (NOT added here): is_active (deactivated/recycle-bin path),
-- is_blocked_by_admin (= the admin PAUSE — no separate flag), daily_cap
-- (existing, but now SYSTEM-MANAGED: only the reset job writes it; see below).

-- Group 2 ceiling. A broker on ready_for_more keeps receiving overtime leads
-- only up to max_cap, so one eager broker can't gallop every overnight lead.
-- Defaults equal to daily_cap's default so "no overtime beyond the normal cap"
-- is the safe out-of-the-box behavior until an admin raises it.
alter table broker add column max_cap int not null default 10
  check (max_cap >= 0);

-- Group 2 daily opt-in: "I'm ready for more than my cap today." Cleared to
-- false every morning by the reset job — it's a per-day choice, not a standing
-- setting, so a broker re-opts-in each day they want overtime.
alter table broker add column ready_for_more boolean not null default false;

-- Deferred cap change. Editing an existing broker's cap NEVER writes daily_cap
-- directly (that would take effect mid-day and could drop below leads already
-- delivered). Instead it writes here; the reset job promotes it into daily_cap
-- once pending_daily_cap_effective_at has arrived. daily_cap is therefore
-- system-managed: humans propose via these fields, the system applies on
-- schedule. (Onboarding a NEW broker still sets daily_cap directly — safe,
-- since they aren't live until eligible_from.)
alter table broker add column pending_daily_cap int
  check (pending_daily_cap is null or pending_daily_cap >= 0);
alter table broker add column pending_daily_cap_effective_at timestamptz;

-- Deferred max_cap change — same reasoning and pattern as pending_daily_cap
-- immediately above: max_cap is the Group-2 ceiling, and a same-day lowering
-- could drop below overtime leads already delivered today, the same mid-day-
-- cut problem daily_cap's deferral exists to prevent. (Onboarding a new
-- broker still sets max_cap directly — same "not live until eligible_from"
-- reasoning as daily_cap.)
alter table broker add column pending_max_cap int
  check (pending_max_cap is null or pending_max_cap >= 0);
alter table broker add column pending_max_cap_effective_at timestamptz;

-- Deferred unblock. Pausing (is_blocked_by_admin = true) is INSTANT so leads
-- stop immediately and don't go to waste on someone who just left. Unpausing
-- is deferred: set this, and the reset job flips is_blocked_by_admin back to
-- false once it arrives, so leads resume the NEXT day (same regime as a new
-- broker's first day).
alter table broker add column scheduled_unpause_at timestamptz;

-- New/deferred activation. A broker who joins today starts receiving leads
-- tomorrow: eligible_from is set to tomorrow's reset. While eligible_from is in
-- the future the eligibility query excludes them; the reset job clears it once
-- it arrives. Null = eligible now (no pending activation).
alter table broker add column eligible_from timestamptz;

-- =====================================================================
-- LEAD — new allocation columns
-- =====================================================================
-- assigned_broker_id already exists (0008). Under this model, NULL now means
-- exactly "the SYSTEM hasn't placed it yet" (gate closed / no eligible broker)
-- — it waits for the system, never for a broker to claim it.

-- When the CURRENTLY-assigned broker got this lead. Use this for assignment
-- reporting, NOT updated_at: the set_updated_at trigger bumps updated_at on
-- every edit/reassignment, so updated_at no longer means "content last
-- changed". Null while unassigned.
alter table lead add column assigned_at timestamptz;

-- Who CURRENTLY owns it: the round-robin engine ('system') or a human admin
-- who reassigned it ('admin'). This describes the lead's current ownership for
-- display/reporting — it is NOT the cap source (the cap counts lead_delivery
-- rows, which never move, so an admin reassignment can't shift a cap). Null
-- while unassigned.
alter table lead add column assigned_by text
  check (assigned_by is null or assigned_by in ('system', 'admin'));

-- When the CUSTOMER actually requested the lead — Meta's created_time from the
-- webhook — distinct from created_at (when WE created the row, which can lag
-- ingestion retries). Captures the true start of the lead's timeline.
alter table lead add column requested_at timestamptz;
