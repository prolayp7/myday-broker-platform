# Round-Robin Lead Allocation — Design

Status: **BUILT & DEPLOYED (2026-07-13).** Migrations 0018/0019/0020 applied
and verified against the live Supabase database. This document is the
pressure-tested design it was built from, kept as the reference for WHY each
piece works the way it does. It replaces the old "pool + broker claims a
lead" model entirely — leads are assigned **by the system**, never grabbed by
brokers.

All times are **Ontario time** (America/Toronto). The daily reset runs via an
hourly pg_cron tick; the function's own date-comparison guard makes it do real
work only on the FIRST tick after Ontario midnight (~00:00–01:00 local) and
no-op the rest of the day. Midnight-anchoring matches `eligible_from` (also
Ontario midnight); exact time-of-day was confirmed not to matter — brokers
start working at 9 AM.

---

## 1. The core model

A lead is assigned by the system in two tiers:

- **Group 1 — guaranteed delivery.** Round-robin across eligible brokers in the
  target `broker_group`, each up to their own `daily_cap`. Every broker is
  guaranteed their cap's worth of leads regardless of whether they actually
  work them — the cap is a **delivery ceiling, not a workload/performance gate**.
- **Gate.** Group 2 opens for a group only once **every eligible Group-1 broker
  has hit their `daily_cap`** for the day.
- **Group 2 — ready-for-more overtime.** Brokers who opted in (`ready_for_more`)
  receive additional leads beyond their daily_cap, up to their `max_cap`
  ceiling (so one eager broker can't gobble everything). Round-robin here too.

Assignment is **pull, not push**: one function runs both when a lead arrives
AND when a broker becomes newly eligible (unpaused, cap raised, etc.). Same
function, two triggers.

`availability_status` (Active/Busy/On Break/…) is the broker's own presence
signal and **never gates assignment**. Only the admin-hold fields do.

---

## 2. Eligibility — the single source of truth

A broker can receive a system lead **right now** only if ALL hold:

```
is_active = true                              -- not deactivated / not in recycle bin path
AND is_blocked_by_admin = false               -- not admin-paused
AND self_paused = false                       -- the broker stopped their own leads (0082)
AND role = 'broker'                           -- admins are not in the rotation (0074)
AND broker_group.is_recycle_bin = false       -- group is a real team, not the bin
AND (eligible_from IS NULL OR eligible_from <= now())   -- new/deferred broker has started
AND system_delivery_count_today < effective_daily_cap   -- under cap (Group 1)
```

For Group 2, replace the last line with `... < max_cap AND ready_for_more = true`,
and require the gate to be open (all Group-1 brokers at cap).

**Notes on the guards (each closes a real blocker):**

- **TWO PAUSES, SET BY DIFFERENT PEOPLE.** This section used to say
  `is_blocked_by_admin` was the only one and that a second flag would be
  redundant. Migration 0082 added `self_paused` anyway, and the reasoning that
  rejected it was the thing that turned out to be wrong: the two are not the
  same state.
  - `is_blocked_by_admin` — an ADMIN stopping a broker. Carries
    `blocked_reason`/`blocked_at`/`blocked_by`, and only an admin can lift it
    (next morning, via `scheduled_unpause_at`).
  - `self_paused` — the BROKER stopping themselves, because they are ill,
    away or already overloaded. Instant both ways, no reason recorded, and it
    **persists until a person clears it** (0105 — it used to be cleared for
    everyone overnight, which meant a broker on vacation was back in the
    rotation each morning and the switch undid itself before they returned).
    An admin can clear it on their behalf via
    `PATCH /brokers/{id}/resume-leads` — resume only, never set, so that this
    and `is_blocked_by_admin` stay tellable apart. `self_paused_at` records
    when the absence began, which is what the roster badge and the admin
    banner show; visibility is what replaced the nightly undo as the guard
    against a forgotten pause.

  Sharing one column would have let a broker clear their own admin block —
  precisely the capability an admin block exists to have — and would have made
  "why did this broker receive nothing" unanswerable.
- `self_paused` is checked at **four** sites inside `assign_lead`: Group 1,
  both halves of the gate, and Group 2. The gate ones are not optional — a
  paused broker left in the gate query would hold overtime shut for the whole
  group while waiting on a cap they cannot approach.
- Recycle-bin exclusion is **dual**: `is_active = true` AND
  `is_recycle_bin = false`. Both would have to be wrong before a live lead
  could reach a deactivated broker. Defense in depth on the one outcome we
  can never allow.

---

## 3. The daily-cap count — count immutable deliveries, not live rows

**Rule:** `system_delivery_count_today` = number of rows in the append-only
**`lead_delivery`** table for this broker since today's reset
(`broker_id = B AND delivered_at >= last_reset_at`).

We count **delivery rows**, which are append-only and never move — NOT current
lead ownership (`assigned_broker_id`), which moves on reassignment.

**Why a dedicated `lead_delivery` table and not `audit_log`:** the cap count is
**load-bearing for correctness** (it decides whether a broker is at cap), so it
must not depend on an observability table's write succeeding. `audit_log` stays
purely observational (the coupling risk flagged earlier). `lead_delivery` is
the correctness record: one row per system delivery, written in the same locked
assignment transaction, never rewritten. A reassignment is still logged to
`audit_log` for history — it just writes nothing to `lead_delivery`, so it can't
move the count.

Why: if we counted live lead rows, an admin reassigning a lead from A to B
would silently drop A's count (A goes under cap → gets an extra lead) and
inflate B's. Counting immutable audit events makes admin reassignment
**invisible to the cap for both brokers**:

- A (lost the lead): count does NOT drop — their morning delivery slot is
  **spent, does not return**. (Otherwise an admin off-loading A's leads would
  make the system pour fresh leads back at A — nonsensical.)
- B (gained the lead): count does NOT rise — an admin action isn't a system
  delivery, so it doesn't consume B's cap.

Reassignment writes a separate `lead_reassigned` / `actor = admin` event that
the cap query never counts. This is why `audit_log` is the **sole home of lead
history** — it's also the honest, drift-free source for the cap count.

---

## 4. Concurrency — one per-group advisory lock

All assignment work for a single group is serialized through
`pg_advisory_xact_lock(<group key>)`. This is primarily for **rotation
fairness** (two parallel webhooks must not both read "C is next" and both give
to C). The same lock also fixes the cap/gate race for free — see below.

**Boundary rule (must be exact):** take the group's advisory lock **FIRST**,
then — inside the same locked transaction — evaluate the gate, count the caps,
pick the next broker, and write the assignment. Release only after commit.
**Nothing that reads a count may run outside the lock.** If the gate is checked
before the lock is taken, the race is still open.

(The worst case if this were wrong is minor — a broker occasionally gets 1–2
leads over cap, nothing lost — but since the lock already exists for rotation,
putting the count check inside it is free correctness, not extra machinery.)

Lock key: use the two-arg `bigint` advisory lock form for ample key space
(only ~5–10 groups ever, so collisions are a non-issue; noted for completeness).

---

## 5. Scheduled changes — deferred, promoted by the reset job

Some changes are **deferred to the next day** so they can't corrupt today's
in-flight allocation. They're written to "pending" fields and promoted each
morning.

- **Pause (block a broker):** `is_blocked_by_admin = true` **immediately** —
  the next eligibility check skips them, so leads don't go to waste on someone
  who just left. Pausing is INSTANT.
- **Unpause:** deferred via `scheduled_unpause_at`. Promoted next reset.
  Leads start flowing again the next day, consistent with new-broker start.
- **New broker / deferred activation:** `eligible_from` (tomorrow). A broker
  who joins today starts receiving leads tomorrow.
- **Cap change (edit existing broker):** deferred via `pending_daily_cap` +
  `pending_daily_cap_effective_at`. See §6.

A calendar UI lets the admin set these effective dates in advance (pause,
unpause, cap change), showing only tomorrow-onward.

---

## 6. `daily_cap` is system-managed — one door for changes  (Blocker 3)

**Problem:** the broker table already has a live `daily_cap` column with a
direct edit path. If that stays open, an admin editing the broker mid-day
changes the cap **instantly** — bypassing the whole "defer to tomorrow" rule
(which exists so a cap lowered mid-day can't drop below leads already
delivered).

**Resolution — close the direct door:**

- Editing an **existing** broker's cap writes `pending_daily_cap` +
  `pending_daily_cap_effective_at` (next reset). It NEVER writes `daily_cap`
  directly.
- `daily_cap` is written by **exactly one writer**: the reset job's promotion
  step (§7, step 1). It is a system-managed column — humans propose, the system
  applies on schedule.
- The UI shows both honestly: e.g. "Current cap: 10 · Scheduled: 5, effective
  tomorrow."
- **Exception — onboarding a new broker:** set `daily_cap` directly at
  creation. Safe, because the broker isn't live until `eligible_from`
  (tomorrow) — there's no mid-day-lowering danger, and it avoids a weird
  "cap doesn't exist until tomorrow" state. The deferral only bites on editing
  an already-working broker.
- **`max_cap` (the Group-2 ceiling) follows the identical pattern**: editing
  an existing broker's `max_cap` writes `pending_max_cap` +
  `pending_max_cap_effective_at`, promoted by the same reset-job step. Same
  reasoning — a same-day lowering could otherwise drop below overtime leads
  already delivered today. New-broker onboarding sets `max_cap` directly, same
  exception as `daily_cap`.

---

## 7. The daily reset job — `daily_lead_allocation_reset()`

Scheduled hourly via pg_cron (`0 * * * *`, plain UTC — needs no privileged
timezone config). Guarded by a date-comparison idempotency check at the top:
if `last_reset_at` is already today (Ontario), do nothing. Net effect: real
work happens exactly once per Ontario calendar day, on the first tick after
Ontario midnight; the other ~23 ticks are a single no-op date comparison.

Steps, in order:

1. **Promote scheduled changes** (must come first, before the drain, so today's
    drain uses the new values):
    - `pending_daily_cap_effective_at <= now()` → copy `pending_daily_cap`
      into `daily_cap`, clear both pending fields.
    - `scheduled_unpause_at <= now()` → set `is_blocked_by_admin = false`,
      clear `scheduled_unpause_at`.
    - `eligible_from <= now()` → clear `eligible_from` (served its purpose).
    - Each promotion writes an `audit_log` row (`actor = system`).
2. **Advance the day** — `lead_allocation_state.last_reset_at = now()`.
3. **Clear the daily opt-in** — `ready_for_more = false` for every broker.
    This is the ONLY flag the reset clears. `self_paused` is deliberately left
    alone (0105): "I'll take extra work today" expires with the day, "I am
    away" does not.
4. **Drain each group's bucket** — loop every real `broker_group`, call the
    assignment function per group so any leftover overnight leads get
    distributed to whoever is now freshly eligible. A self-paused broker is
    still paused here and receives none of it; the backlog reaches them when
    they (or an admin) switch back on, which runs its own drain.

---

## 8. Data model changes

### New tables

- **`broker_group_rotation`** — remembers whose turn is next per group, and is
  the lockable row for §4 serialization.
  `group_id PK/FK→broker_group`, `last_assigned_broker_id FK→broker`, `updated_at`.
- **`lead_allocation_state`** — single row, the "when did today start" clock.
  `last_reset_at timestamptz`.
- **`lead_delivery`** — append-only record of system deliveries; the source of
  truth for the daily-cap count (§3). `lead_id`, `broker_id`, `delivered_at`.
  Written in the locked assignment txn; never rewritten by reassignment.

### `broker` — new columns

- `max_cap int` — Group 2 ceiling.
- `ready_for_more boolean` — Group 2 daily opt-in (cleared each reset — the
  only flag that is).
- `self_paused boolean` — the broker's own "not taking leads" (0082).
  **Persists**; NOT cleared by the reset (0105).
- `self_paused_at timestamptz` — when that pause began, for the admin's
  "paused · 6d" reading (0105). Null whenever `self_paused` is false, and
  sticky across a re-pause so an ongoing absence is never re-dated.
- `pending_daily_cap int`, `pending_daily_cap_effective_at timestamptz` —
  deferred cap change (§6).
- `pending_max_cap int`, `pending_max_cap_effective_at timestamptz` —
  deferred max_cap change, identical pattern (§6).
- `scheduled_unpause_at timestamptz` — deferred unblock (§5).
- `eligible_from timestamptz` — new/deferred activation (§5).

Reused (NOT new): `is_active`, `is_blocked_by_admin` (= pause), `daily_cap`
(now system-managed).

### `lead` — new columns

- `assigned_at timestamptz` — when currently-assigned broker got it. Use this
  for assignment reporting, NOT `updated_at` (the `set_updated_at` trigger
  bumps `updated_at` on every reassignment, so it no longer means "content
  last changed").
- `assigned_by text check (assigned_by in ('system','admin'))` — who assigned.
- `requested_at timestamptz` — Meta's `created_time` (when the customer
  actually requested), distinct from `created_at` (when we created the row).

`assigned_broker_id = NULL` now means only "the **system** hasn't placed it
yet" (gate closed / no eligible broker) — it waits for the system, never for a
broker to grab it.

### `audit_log` — reused, no schema change

Every assignment, reassignment, pause/unpause, cap promotion, and
unassigned-reason event is logged with a timestamp and `actor` (system or the
admin). Audit_log is the sole home of lead movement history and the source for
the cap count (§3).

---

## 9. Retired: the claim / pool mechanism  (Blocker 3, this pass' clarification)

The old model — lead lands unassigned in a "pool," broker clicks "claim,"
first-come-first-served — is **removed**. It's incompatible with auto-assign
(a system-assigned lead could still be claimed by a second broker → two brokers
on one customer; or a claim bypasses rotation + cap entirely).

- `PATCH /leads/{id}/claim` → removed.
- The pool query / pool endpoint → removed.
- **Admin reassignment** (move an assigned lead A→B, e.g. broker off sick) is a
  separate, **admin-only**, audited action stamped `assigned_by = 'admin'`.
  It is NOT broker self-claim, and it's invisible to the cap (§3).

### 9a. Manually created leads (added 2026-07-28, migration 0053)

A lead a broker **types in themselves** — a referral, a walk-in, a call to the
office — is assigned straight to its creator and stamped
`assigned_by = 'broker'`, `source = 'manual'`.

This is not the removed self-claim path, and does not reopen either failure it
was removed for. There is no existing lead for a second broker to take (the row
does not exist until the creator makes it), and nothing is bypassed: the
round-robin distributes **paid ad leads**, which this is not.

It is invisible to the daily cap for the same structural reason admin
reassignment is — nothing writes a `lead_delivery` row, because `assign_lead` is
never called. Nothing enforces this; it falls out of skipping the engine.

The group is the creator's own, read from their token. `POST /leads/manual` has
no `group_id` field, so a broker cannot file a customer into another's book.

---

## 10. Parked enhancements (deferred by decision)

- **Zero-active-broker guard for a group** — a rule that a group must have at
  least one active, unpaused broker to receive leads at all (enforced at the
  routing/group level). Parked to do last as an enhancement. Until it exists,
  `assign_lead` (0019) treats a group with zero eligible Group-1 brokers as
  **gate closed** (not open) — it does NOT fall through to Group 2 overtime,
  since that would silently mask a group with no guaranteed coverage. The lead
  is left unassigned and logged (`lead_unassigned`, reason
  `no_eligible_broker_in_either_tier`) until this guard exists or a broker
  becomes eligible again.
- Mid-day broker-join nuances — mostly resolved via the tomorrow-start deferral.

---

## Open blockers

None currently open. All blockers from the design passes are resolved or
consciously parked (§10). Ready to move from planning to writing the migration
when approved.
