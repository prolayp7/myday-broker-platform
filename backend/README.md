# Meta CRM Backend

FastAPI backend for the Meta CRM (broker lead-management system). Data lives
in Supabase (Postgres). See the `meta-crm-architecture` project for the full
data model diagrams and operational-flow documentation
(`/data-model`, `/data-model/campaign`, `/data-model/broker_group`).

## Supabase project

- Organization: **PolicyScanner** (Free plan)
- Project: **MyDay Broker**
- Connection: `db.py` reads `SUPABASE_URL` and `SUPABASE_SERVICE_ROLE_KEY`
  from `.env` (see `.env.example` for the template — never commit `.env`).
- The backend always connects using the `service_role` key, which bypasses
  Row Level Security entirely. RLS policies only affect direct `anon` /
  `authenticated` access, which nothing uses today.

## House rules (apply to every table, not just the ones already built)

1. **RLS is enabled on every table, deny-all by default.** No policies are
   written unless a real, specific need for direct (non-backend) Supabase
   access shows up. Adding a scoped policy later is easy — starting locked
   down is the safe default.
2. **Every foreign key column gets an index.** Postgres does not auto-index
   foreign keys, and unindexed FKs cause slow joins and slow cascades.
3. **`updated_at` is kept current via the shared trigger**, not by
   application code. The reusable function lives in
   `migrations/0000_shared_functions.sql` (`set_updated_at()`) — every new
   table attaches its own `before update` trigger calling it.
4. **Denormalized/derived labels are not stored** if they can be computed
   cheaply on read (e.g. a join). Storing a copy of data that lives
   elsewhere requires a sync mechanism, and any gap in that sync produces
   permanently stale data. At this project's scale, a join is effectively
   free — see `broker_group`'s display label for a worked example.
5. **One migration file per table (or tightly related group of tables)**,
   numbered in order (`0000_...`, `0001_...`, ...), committed to
   `migrations/`. Applied manually via the Supabase SQL Editor for now (no
   GitHub auto-deploy connected — deliberate, so nothing applies to the live
   database without a manual review step first).
6. **Layered architecture, one file per table per layer** (see Architecture
   below). `main.py` stays thin — it only creates the app and wires in each
   router via `app.include_router(...)`. No table-specific logic lives in
   `main.py`.
7. **`password_hash` (and any secret column) is never selected with `*`.**
   Repositories list safe columns explicitly (see `repositories/broker.py`'s
   `SAFE_COLUMNS`) so a secret can never be returned to a client by accident,
   even after new columns are added to the table.
8. **People are soft-deleted, not hard-deleted.** A `broker` is deactivated
   (`is_active = false`), never removed — deleting the row would destroy its
   attendance / lead-assignment history. Only pure lookup rows with no history
   (community, broker_group) get real `DELETE`.

## Architecture (layered)

Each table's code is split into four layers, one file per table per layer.
A request flows top to bottom; each layer has exactly one job:

```
routes/       HTTP only — parse the request, call the service, return.
              No DB calls, no error-to-status logic.
services/     Business logic — owns every HTTPException decision, calls
              db_error to translate DB errors, hashes passwords, enforces
              rules (soft-delete, etc.). Calls the repository.
repositories/ Supabase queries only — no HTTP concerns, no rules. If the DB
              raises, the error bubbles up to the service to interpret.
schemas/      Pydantic request/response models (validation shapes).
```

Shared helpers: `db.py` (Supabase client), `db_errors.py` (`db_error` — maps
Postgres error codes like `23503`/`23505` to clean HTTPExceptions and logs
anything unmapped), `security.py` (bcrypt `hash_password` / `verify_password`).

Why layered and not routes-call-DB-directly: the planned background workers
(cap/cooldown, spend signal, Meta feedback — see the architecture diagrams)
will call the same business logic as the HTTP routes. The service layer is
what lets that logic be shared between a request and a worker without
duplication. `routes/auth.py` is the one file not yet layered — deliberately,
until the real auth flow is built.

## Migrations applied so far

| File | What it creates |
|---|---|
| `0000_shared_functions.sql` | `set_updated_at()` — shared trigger function reused by every table. |
| `0001_broker_group.sql` | `community` (admin-managed codename list: Dragon, Tiger, Nature seeded) and `broker_group` (`community_id` FK + auto-numbered `group_number`). Both tables: RLS deny-all, indexed FK, `updated_at` trigger attached. |
| `0002_community_name_case_insensitive.sql` | Replaces `community.name`'s case-sensitive unique constraint with a case-insensitive unique index (`lower(name)`), so "Dragon" and "dragon" can't both exist. |
| `0003_broker.sql` | `broker` (one row per person who logs in and works leads): identity + `password_hash`, licensing/compliance, operational, and admin-block fields. Enums as `check` constraints. `group_id` FK (indexed) → `broker_group`, `blocked_by` self-FK (indexed). `email` unique. RLS deny-all, `updated_at` trigger. |
| `0004_campaign.sql` | `ad_platform` (admin-managed list: meta, tiktok, website seeded — a lookup table, not a `check` constraint, since new ad platforms can be added later with a plain insert) and `campaign` (root of the ad hierarchy: `source_id` FK → `ad_platform`, `product` as a `check` constraint matching `broker.lines_of_authority`'s vocabulary, `group_id` FK → `broker_group`). Both tables: RLS deny-all, indexed FKs, `updated_at` trigger. |
| `0005_ad_group_and_ad.sql` | `ad_group` (child of `campaign`; `platform_ad_group_id` unique — the future webhook's find-or-create lookup relies on this) and `ad` (child of `ad_group`; `ad_group_id` nullable because Meta's real leadgen webhook doesn't guarantee full context at ingestion, `platform_ad_id` unique for the same find-or-create reason). Both tables: RLS deny-all, indexed FKs, `updated_at` trigger. |
| `0006_lead_form_routing.sql` | **Routing pivot.** Drops `campaign.group_id` — campaign is now reporting/attribution only. Adds `lead_form` (`platform_form_id` unique FK-target for the ingestion webhook, `group_id` FK → `broker_group`): the actual routing mechanism, since Meta's real leadgen webhook carries `form_id` but never `campaign_id` (confirmed against Meta's docs and industry practice — HubSpot/Zapier/dedicated Lead Ads connectors all route by form, not campaign). RLS deny-all, indexed FK, `updated_at` trigger. |

> ⚠️ **This table stops at `0006` and the database is on `0105`.** It was not
> kept up, and the ~99 migrations in between are not described here. Read
> `migrations/` in numeric order for the real history — each file carries its
> own reasoning in a header comment. The endpoint table further down is stale
> in the same way (it still calls `/auth/login` a placeholder; the real
> two-step TOTP flow has been live since `0012`). Trust the code, not these
> two tables.
>
> Recent, because it changes behaviour somebody may remember differently:
>
> | File | What it changes |
> |---|---|
> | `0105_self_pause_persists.sql` | **`self_paused` no longer resets nightly.** Removes STEP 3a from `daily_lead_allocation_reset()`, so a broker who stops their own leads stays stopped until they — or an admin, via the new `PATCH /brokers/{id}/resume-leads` — switch them back on. `ready_for_more` still clears each night; it is genuinely a per-day opt-in. Adds `broker.self_paused_at` (sticky across a re-pause) so the admin screens can show how long an absence has run, which is what replaced the nightly undo as the guard against a forgotten pause. |

### How to apply a new migration

1. Write the `.sql` file in `migrations/`, numbered after the last one.
2. Open the Supabase dashboard → **MyDay Broker** project → **SQL Editor** →
   **New query**.
3. Paste the file's contents, review it, click **Run**.
4. Update the table above.

## API endpoints (verified working)

Interactive docs (auto-generated, lets you try every endpoint in the browser):
`http://127.0.0.1:8000/docs`

Each endpoint's logic lives in `routes/<table>.py` → `services/<table>.py` →
`repositories/<table>.py` (see Architecture). The `File` column below names
the route entry point.

| Method | Path | File | What it does |
|---|---|---|---|
| GET | `/` | `main.py` | Health check. |
| GET | `/communities` | `routes/community.py` | List all communities, alphabetically. |
| POST | `/communities` | `routes/community.py` | Create a community. Name is trimmed, 1–100 chars; case-insensitively unique (`409` on duplicate). |
| DELETE | `/communities/{id}` | `routes/community.py` | Delete a community. `409` if a broker group still references it, `404` if not found. |
| GET | `/broker-groups` | `routes/broker_group.py` | List all groups, oldest first, with `community.name` joined in — the label is never stored, only composed on read. |
| POST | `/broker-groups` | `routes/broker_group.py` | Create a group for a `community_id`. `group_number` is auto-assigned by the DB trigger — never send it. `400` if the community doesn't exist. |
| DELETE | `/broker-groups/{id}` | `routes/broker_group.py` | Delete a group. `409` if a broker/campaign/lead still references it, `404` if not found. |
| GET | `/broker-groups/{group_id}/brokers` | `routes/broker.py` | List brokers in a group. `password_hash` is never included. |
| POST | `/broker-groups/{group_id}/brokers` | `routes/broker.py` | Onboard a broker. Password is bcrypt-hashed before storage (min 8 chars). `409` on duplicate email, `400` if the group doesn't exist. |
| PATCH | `/brokers/{id}/deactivate` | `routes/broker.py` | Soft-delete a broker (`is_active = false`). `404` if not found. Brokers are never hard-deleted (would destroy history). |
| PATCH | `/brokers/{id}/activate` | `routes/broker.py` | Reverse a deactivation (`is_active = true`). Reversible on/off toggle only — NOT the same as permanent offboarding (which needs a reassign-leads workflow once `lead` exists). |
| GET | `/ad-platforms` | `routes/ad_platform.py` | List all ad platforms (meta, tiktok, website, ...), alphabetically. List-only — new platforms are rare enough to add via the Supabase SQL Editor directly. |
| GET | `/campaigns` | `routes/campaign.py` | List all campaigns, oldest first, with `ad_platform.name` joined in. Reporting/attribution only — see `/lead-forms` for actual routing. |
| POST | `/campaigns` | `routes/campaign.py` | Create a campaign. `400` if `source_id` doesn't exist. `product` is restricted to `Auto`/`Travel`/`Business`/`Life` at both the Pydantic and DB (`check` constraint) level. No `group_id` — campaign does not route. |
| DELETE | `/campaigns/{id}` | `routes/campaign.py` | Delete a campaign. `404` if not found. (No FK-conflict handling yet — nothing references `campaign` until `ad_group`/`lead` exist.) |
| GET | `/ad-groups` | `routes/ad_group.py` | List all ad groups, oldest first, with `campaign.name` joined in. |
| POST | `/ad-groups` | `routes/ad_group.py` | Create an ad group under a campaign. `400` if the campaign doesn't exist, `409` if `platform_ad_group_id` is already used by another row. |
| DELETE | `/ad-groups/{id}` | `routes/ad_group.py` | Delete an ad group. `404` if not found. |
| GET | `/ads` | `routes/ad.py` | List all ads, oldest first, with `ad_group.name` joined in (may be null). |
| POST | `/ads` | `routes/ad.py` | Create an ad. `ad_group_id` is optional — matches the real Meta leadgen webhook, which only sends `leadgen_id`/`form_id`/`adgroup_id`/`ad_id` and requires a separate follow-up call for full context. `409` if `platform_ad_id` is already used. |
| DELETE | `/ads/{id}` | `routes/ad.py` | Delete an ad. `404` if not found. |
| GET | `/lead-forms` | `routes/lead_form.py` | List all form→group routing mappings, oldest first, with the `broker_group`'s community label joined in. **This is the real routing mechanism** — see below. |
| POST | `/lead-forms` | `routes/lead_form.py` | Map a real Meta/TikTok `form_id` to the broker group its leads should route to. `409` if that `form_id` is already mapped, `400` if the group doesn't exist. |
| DELETE | `/lead-forms/{id}` | `routes/lead_form.py` | Remove a form→group mapping. `404` if not found. |
| POST | `/auth/login` | `routes/auth.py` | **Fake/placeholder.** Checks a hardcoded in-memory list, not the real `broker` table. Not yet layered; needs the real auth flow (password-hash check + TOTP + session) before production. |

### Why routing lives on `lead_form`, not `campaign`

Meta's real leadgen webhook payload only ever carries `leadgen_id`, `form_id`,
`adgroup_id`, `ad_id`, `created_time` — **never `campaign_id`**. Resolving
`campaign_id` from `adgroup_id` needs a separate, non-guaranteed follow-up
Graph API call, and lead routing must never block on that. Verified against
real integration practice (HubSpot, Zapier, and dedicated Meta Lead Ads
connectors all route by `form_id`, never `campaign_id`) before making this
change — `campaign.group_id` existed briefly, was tested, then removed in
favor of `lead_form` once this was confirmed. `lead_form` also gives real
flexibility campaign-level routing couldn't: two form variants under the
same split test can route to two different broker groups.

Tested and confirmed: auto-numbering (per community), the community-name
join, foreign-key rejection of a bad `community_id` (`23503`), UUID path
validation (`422` before the DB is touched), case-insensitive community
uniqueness, bcrypt password hashing (stored as `$2b$...`, capped at 72 bytes,
verifiable, never returned by any endpoint), broker soft-delete + reactivate,
the campaign `product` check constraint rejecting an invalid value at the
database level (`23514`), independent of the API's own validation, the
`ad_group`/`ad` unique constraints on their platform-id columns (including
that two `NULL` platform ids never conflict with each other, per Postgres
semantics — needed since an `ad` can legitimately be created before its
`ad_group_id` is known), and `lead_form`'s routing flexibility — two
different `form_id`s mapped to two different `broker_group`s, both
succeeding, confirming campaign-level routing's one-group-per-campaign
limitation no longer applies.

Given `lead_form`, `broker_group`, `campaign` (reporting only), and
`ad`/`ad_group` (webhook-populated, not admin-facing), the remaining pieces
for a fully working lead pipeline are the `lead` table itself and the
webhook/ingestion endpoint — not yet built.

## Running the backend locally

```
venv\Scripts\activate
pip install -r requirements.txt
uvicorn main:app --reload
```
