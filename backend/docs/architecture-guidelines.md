# Backend Architecture Guidelines

What this backend is, how its pieces fit together, and the conventions to
follow when adding to it. `README.md` documents the database (migrations,
house rules, endpoint list) in detail — this document covers the application
architecture around it: how a request flows, how background work runs, how
integrations are structured, and the security model. Read both; they overlap
deliberately at the edges (layering, house rules) rather than duplicate.

---

## 1. System shape

```
Browser (frontend, Vite dev server / static build)
        │  HTTPS, bearer JWT
        ▼
FastAPI app (main.py) — single uvicorn process, no --workers
        │
        ├── routes/     one router per table, registered in main.py
        ├── services/    business logic
        ├── repositories/ Supabase queries
        └── background asyncio tasks (started in lifespan)
        │
        ▼
Supabase (Postgres) — accessed via service_role key, RLS bypassed entirely
        │
        └── triggers / pg_cron (round-robin assignment, daily reset,
            meta_signal queuing) — DB-side logic the app never calls directly

External services, each behind its own root-level client module:
  meta_graph.py   Meta Graph API (fetch a lead's submitted answers)
  meta_capi.py    Meta Conversions API (report Lead/Quote/Sold back to Meta)
  zoom_client.py  Zoom Phone (call/recording metadata)
  gcs_client.py   Google Cloud Storage (durable copy of call recordings)
```

Two facts shape almost every other decision in this codebase:

- **Single process, single worker.** No `--workers` flag, no Redis, no
  distributed lock. In-memory state (the rate limiter in
  `auth_dependencies.py`, the background drain loops) is correct *because*
  there is exactly one process holding it. If the deploy ever moves to
  multiple workers or horizontal scaling, every one of these needs to move
  to shared state first — it is not a drop-in change.
- **Postgres triggers do real work.** Round-robin assignment, the daily cap
  reset, and `meta_signal` row creation all happen in the database via
  triggers and `pg_cron`, not in application code. The app's job for that
  data is to *read* what the database already decided, or to drain a queue
  the database already populated (see §5). Don't assume application code is
  the only place business rules live — check `migrations/` before
  reimplementing something a trigger might already do.

## 2. Layered architecture (per table)

Every table's code is split into four files, one per layer, named after the
table (`routes/broker.py`, `services/broker.py`, `repositories/broker.py`,
`schemas/broker.py`). A request flows top to bottom; each layer has exactly
one job and does not reach past its neighbor:

```
routes/        HTTP only. Parse the request, call the service, return.
                No DB calls. No error-to-status logic — that's the service's job.
services/      Business logic. Owns every HTTPException decision, calls
                db_error() to translate DB errors, hashes passwords, enforces
                rules (soft-delete, cap checks, etc.). Calls the repository.
repositories/  Supabase queries only. No HTTP concerns, no business rules.
                If the DB raises, the error bubbles up to the service to
                interpret — repositories never catch or translate.
schemas/       Pydantic request/response models. The validation shape, not
                the business rule (e.g. "product must be one of these four
                strings" lives here; "campaign has no group_id" lives in the
                service/DB).
```

**Why layered instead of routes calling the DB directly:** the planned
background workers (cap/cooldown checks, spend signals, Meta feedback loops)
need to run the *same* business logic that HTTP routes run. The service layer
is what lets that logic be shared between a request and a worker without
duplicating it. When you add a rule, ask whether anything other than an HTTP
request could ever need to trigger it — if yes, it belongs in `services/`,
never in `routes/`.

`routes/auth.py` follows this same shape today (thin routing into
`services/auth.py`) — `README.md` still describes it as an unlayered
placeholder from an earlier build stage; that line is stale. See §11.2.

## 3. Where non-table code goes

Not everything is a table. Three other kinds of module exist, each with a
specific home:

- **Shared cross-table helpers**, at the backend root: `db.py` (the Supabase
  client), `db_errors.py` (`db_error()` — Postgres error code → HTTPException
  translation), `security.py` (password hashing, JWT, TOTP encryption),
  `auth_dependencies.py` (`Depends()`-based request gates: session
  resolution, admin check, rate limiting).
- **Outbound integration clients**, also at the backend root, *not* under
  `services/`: `meta_graph.py`, `meta_capi.py`, `zoom_client.py`,
  `gcs_client.py`. These know nothing about leads, brokers, or contacts —
  they move bytes to and from an external API and back. Putting them at root
  rather than in `services/` signals that boundary: a `services/` file is
  allowed to import and call one of these clients, but the client itself
  never imports a repository or reasons about domain rules. If you add a new
  external integration (a new ad platform, a new comms provider), give it
  its own root-level client module rather than folding its HTTP calls into
  the service that uses it.
- **Ingestion / matching services**, under `services/`, that turn external
  data into domain rows but aren't tied to one table's CRUD:
  `lead_ingestion.py` (webhook event → `lead` row), `zoom_matching.py`
  (Zoom call → `lead` match), `zoom_ingestion.py`. These belong in
  `services/` because they *do* reason about domain rules (routing, matching
  logic) even though no single table owns them.

## 4. Security model

- **Auth tokens are staged JWTs**, not a single token type. `stage` is
  `"access"`, `"refresh"`, or `"pending_totp"` (`security.py`). A route
  gating a real session (`get_current_broker` in `auth_dependencies.py`)
  checks `stage == "access"` explicitly — a refresh token or a
  post-password-pre-TOTP token must never work as a session, even though
  they're signed with the same key.
- **The database row is the source of truth for session validity, not the
  JWT's claims.** `get_current_broker` re-fetches the broker on *every*
  request and checks `is_active` / `is_blocked_by_admin` / `is_archived`
  live. The role embedded in the access token is only a fast-path hint —
  `require_admin` still reads the freshly-fetched row's role, not the
  token's. This means blocking or archiving a broker takes effect on their
  very next request, not whenever their token happens to expire.
- **Authorization is a single equality check.** `require_admin` is the only
  authorization decision in the codebase — every admin route reaches it
  through the same function. There is deliberately no role hierarchy or
  membership check (a `manager` tier existed briefly and was removed,
  migration `0066`, because a tier that grants exactly what the tier above
  grants is a trap, not a feature). If you add a new role, do not default to
  making `require_admin`-style checks into membership tests — keep each
  gate a single, explicit equality unless there's a proven need for more.
- **Secrets are never returned via `SELECT *`.** Repositories that touch a
  table with a secret column (`broker.password_hash`) list safe columns
  explicitly (`repositories/broker.py`'s `SAFE_COLUMNS`) so a newly added
  column can never leak to a client by accident. The auth path uses a
  separate `AUTH_COLUMNS` shape internally, which callers outside
  `auth_dependencies.py` should not reuse.
- **Rate limiting is per-IP, in-memory, and intentionally not distributed**
  (`auth_dependencies.py`'s `rate_limit()`). Correct only because the app
  runs as one process — see §1.
- **Webhook signatures are verified over the raw request body**, before any
  JSON parsing, using `hmac.compare_digest` (constant-time comparison). See
  §5 — hashing a re-serialized/parsed body produces different bytes and
  always fails, which is a common way to accidentally build a webhook
  verifier that silently rejects everything.
- **JWT decoding pins `algorithms=["HS256"]` explicitly** — never trust
  whatever algorithm a token claims to use (the classic "alg confusion"
  vulnerability).

## 5. Webhook ingestion pattern

`routes/webhooks.py` → `services/lead_ingestion.py` is the reference shape
for any new inbound webhook (Meta, Zoom, or a future provider):

1. **Verify the signature over the raw body**, before parsing JSON.
2. **Store the event durably first** (`webhook_event` row), independent of
   whether processing it succeeds. The raw payload is never lost even if
   everything downstream fails.
3. **Deduplicate before processing.** A duplicate delivery of an
   already-handled event must not be reprocessed (Meta and most webhook
   providers retry on anything other than a fast `200`).
4. **Process synchronously, but never raise past the handler.** Every
   failure path records a `failure_reason` on the stored event row instead
   of throwing — the handler always returns `200`, because a `5xx` here
   tells the provider to retry a payload that's already durably stored,
   producing duplicate work instead of recovery. A "failed" event is always
   recoverable later (fix the underlying cause, re-run ingestion against the
   same stored row); it's never silently lost data.
5. **Ingestion failures are not audit events.** `audit_log` is for actions
   with a human actor; a webhook has none. The event row's own
   `status`/`failure_reason` columns are the permanent record.

## 6. Background workers

Two long-running `asyncio` tasks are started in `main.py`'s `lifespan` hook
and cancelled explicitly on shutdown: the Meta CAPI drain
(`services/meta_signal.py::run_drain_forever`) and the Zoom recording sweep
(`services/zoom_recording.py::run_sweep_forever`). Both exist because a
Postgres trigger can create a queue row but cannot make an HTTP call —
something in the app has to come along afterward and actually send/fetch.

Rules every such loop follows, worth copying for a new one:

- **Never let an exception escape the loop.** An uncaught exception kills
  the `asyncio.Task` silently — the app keeps looking healthy while the
  loop has actually stopped forever. Wrap each cycle's body in `try/except`,
  log, and let the loop continue to the next interval.
- **Run blocking work in a thread** (`asyncio.to_thread(...)`). The
  drain/sweep functions make blocking HTTP and Supabase calls; running them
  inline on the event loop would stall every other request the app is
  serving for the duration.
- **Cap retries with a terminal state**, don't retry forever. A genuine
  provider-side rejection (bad payload, expired token) won't fix itself by
  resending the same bytes — after `MAX_ATTEMPTS`, mark the row terminally
  failed so it surfaces to an admin instead of silently retrying forever.
- **Single-process only.** Like the rate limiter, these loops assume exactly
  one process. Multiple workers would each start their own copy and race
  over the same pending rows.

## 7. Error handling

`db_errors.py::db_error()` is the one place Postgres error codes get
translated into `HTTPException`s. A service calls it with the specific
`HTTPException`s that route can actually produce (e.g. a `409` for "still
referenced" on a foreign-key violation); anything unmapped is logged in full
server-side and returned to the client as a generic `500` — callers never
see raw database error text. When adding a new mutation that can hit a
constraint (a new unique column, a new FK), pass the matching `on_unique` /
`on_foreign_key` argument rather than letting the exception fall through to
the generic 500 — the generic path exists as a safety net, not a first
option.

## 8. Configuration

Every environment variable the app reads is documented in `.env.example`,
grouped by what breaks without it (required-to-start vs. required for a
specific integration vs. dev/testing-only). Required variables are read with
`os.environ[...]` (not `.get()`) at import time, so a missing one fails the
app at startup rather than surfacing as a confusing runtime error the first
time that code path runs. When you add a new environment variable:

- Add it to `.env.example` in the right section, with a comment on what
  breaks without it — not just its name.
- Use `os.environ[...]` if the app cannot reasonably run without it; use
  `os.environ.get(...)` with a documented default only if there's a sane
  fallback.
- `.env` is loaded independently in each module that needs it
  (`load_dotenv(...)` appears in `db.py`, `security.py`, `gcs_client.py`,
  `zoom_client.py`) rather than relying on import order — don't assume some
  other module has already loaded it for you.

## 9. Testing

Tests live in `tests/`, run with `pytest` (`pytest.ini` sets `pythonpath = .`
so root-level modules like `security.py` and `meta_capi.py` are importable
without a package install). Current coverage is deliberately focused on the
highest-consequence logic — authorization (`test_authorization.py`),
security primitives (`test_security.py`), the Meta CAPI payload building
(`test_meta_capi.py`), and premium math (`test_premium_math.py`) — rather
than CRUD-by-CRUD route coverage. When adding tests for new logic, prefer
this pattern: unit-test the service/pure-function layer directly rather than
spinning up the full FastAPI app, unless the thing under test is specifically
about request/response wiring.

## 10. Adding a new table or feature — checklist

1. Write the migration in `migrations/`, numbered after the last one.
   Apply it manually via the Supabase SQL Editor (see `README.md` for the
   full house rules — RLS deny-all, index every FK, attach the shared
   `set_updated_at()` trigger, don't store what a join can compute).
2. Add `schemas/<table>.py` — request/response Pydantic models.
3. Add `repositories/<table>.py` — Supabase queries only, no rules. Use an
   explicit safe-columns list if the table has anything secret.
4. Add `services/<table>.py` — business rules, calls the repository, raises
   `HTTPException`s (via `db_error()` for DB-originated ones).
5. Add `routes/<table>.py` — thin HTTP wiring, calls the service.
6. Register the router in `main.py`. `main.py` stays thin — no
   table-specific logic there, ever.
7. If the feature needs an outbound call to a third party, add a
   root-level client module (§3) rather than embedding HTTP calls in the
   service.
8. If the feature needs to run outside a request (a queue drain, a sweep),
   follow the background-worker rules in §6 and wire it into `main.py`'s
   `lifespan`.
9. Add `.env.example` entries for any new configuration (§8).
10. Add tests for the service-layer logic, following the pattern in §9.

## 11. Known gaps & recommended changes

Two concrete issues surfaced while writing this guideline. Kept here,
attached to the sections they relate to, rather than in a separate report
that's easy to lose track of.

### 11.1 HIGH — Meta CAPI access token leaks into logs (unfixed)

Relates to §4 (security model) and §6 (background workers) — `send_event()`
is what the CAPI drain calls on every cycle.

**Where:** `meta_capi.py`, `send_event()`, around line 114.

```python
response = httpx.post(
    f"{CAPI_API_BASE}/{dataset_id}/events",
    params={"access_token": access_token},   # <-- leaks
    json=body,
    timeout=10.0,
)
```

**Why it's real:** httpx logs the full request URL, including the query
string, at `INFO` level. `main.py:15` sets root logging to
`logging.basicConfig(level=logging.INFO)` with no override for the `httpx`
logger, so every call writes the live `META_CAPI_ACCESS_TOKEN` to stdout in
cleartext. This isn't hypothetical — `meta_graph.py`'s `_get_object()`
already avoids the identical trap by sending its own token via an
`Authorization` header instead of a query param, with a comment explaining
why. `send_event()` is the one call site that didn't follow that pattern.
Full writeup: `docs/meta-capi-token-leak-fix.md` (status there is still
"proposed, not applied").

**Fix — two changes:**

1. `meta_capi.py`: move the token into the `Authorization` header, matching
   `meta_graph.py`'s existing pattern. Meta's Conversions API accepts the
   token either way, so this changes nothing on Meta's side.

   ```python
   response = httpx.post(
       f"{CAPI_API_BASE}/{dataset_id}/events",
       headers={"Authorization": f"Bearer {access_token}"},
       json=body,
       timeout=10.0,
   )
   ```

2. `main.py`: defense in depth, next to the existing `basicConfig` call, so
   a *future* accidental `params=` token doesn't leak the same way:

   ```python
   logging.basicConfig(level=logging.INFO)
   logging.getLogger("httpx").setLevel(logging.WARNING)
   ```

**Operational follow-up (not a code change):** rotate
`META_CAPI_ACCESS_TOKEN` once the fix ships. The current token has been
written to logs on every send since deploy and must be treated as already
exposed — the code fix stops future leakage, it doesn't undo past exposure.

### 11.2 MEDIUM — `README.md`'s `/auth` documentation is stale

Relates to §2 (layering) and §4 (security model).

`README.md`'s endpoint table still describes `/auth/login` as: *"Fake/
placeholder. Checks a hardcoded in-memory list, not the real `broker`
table. Not yet layered; needs the real auth flow (password-hash check +
TOTP + session) before production."* That's no longer accurate — reading
`routes/auth.py` and `services/auth.py` shows a real, fully layered,
two-step (password → TOTP) login flow already exists: DB-backed credential
checks, account lockout after repeated failures, a timing-attack mitigation
on the no-such-broker path, and the staged-JWT session model described in
§4 of this document.

**Risk:** a reader trusting `README.md` will assume auth still needs
building, either duplicating work or misjudging how much of the security
model is already real and load-bearing.

**Suggested change** (in `README.md`, not this file): replace the
`/auth/login` row with the real behavior, add rows for
`/auth/login/verify`, `/auth/refresh`, and `/auth/change-password`, and
drop the "routes/auth.py is the one file not yet layered" callout from the
Architecture section.

### 11.3 MEDIUM/HIGH — `X-Forwarded-For` spoofing likely bypasses the login rate limiter

Relates to §4 (security model) — `rate_limit()` is the module's stated
defense against "one IP hammering MANY accounts" on `/auth/login`,
`/auth/login/verify`, and `/auth/change-password`.

**Where:** `auth_dependencies.py`, `_client_ip()`, lines 51-60.

```python
def _client_ip(request: Request) -> str:
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"
```

**Why it's real:** this trusts the *first* value in `X-Forwarded-For`
unconditionally, with no limit on how many hops are trusted. The existing
comment argues a spoofed header "at worst lets an attacker rate-limit
themselves into more buckets" — that reasoning only holds if the proxy in
front of this app **overwrites** any client-supplied `X-Forwarded-For`
before forwarding. Standard proxy behavior (per the `X-Forwarded-For` spec,
and most platforms including Railway) is to **append** the real client IP
to whatever header the client already sent, not replace it. If that's how
this deployment behaves, a request arriving with `X-Forwarded-For:
1.2.3.4` already set gets `1.2.3.4, <real-ip>` by the time it reaches this
app — `.split(",")[0]` returns the attacker-chosen value, not the real
one. An attacker who sends a fresh fake first-hop value on every request
gets a fresh rate-limit bucket every time, defeating the limiter entirely
on the exact threat it exists to stop (the per-account lockout in
`services/auth.py` is a separate, narrower protection that still holds,
but distributed credential stuffing across many accounts from one real IP
would not be caught).

**Verify before fixing:** confirm whether Railway's edge strips/overwrites
inbound `X-Forwarded-For` or appends to it. If it strips (sets the header
fresh, ignoring anything the client sent), the current code is already
safe and this finding downgrades to "no proxy-hop-count guard, fragile if
the topology ever changes" rather than an active bypass.

**Fix, if the proxy appends (the safe general pattern — trust only the
hop your known proxy count adds, never a client-suppliable one):**

```python
def _client_ip(request: Request) -> str:
    # Trust only the LAST hop of X-Forwarded-For — the one appended by our
    # own edge proxy — never the first. The first hop (and everything but
    # the last) is client-suppliable and must not be trusted for bucketing.
    forwarded = request.headers.get("X-Forwarded-For")
    if forwarded:
        hops = [h.strip() for h in forwarded.split(",") if h.strip()]
        if hops:
            return hops[-1]
    return request.client.host if request.client else "unknown"
```

If more than one trusted proxy hop can ever sit in front of the app,
harden further by trusting a *fixed count* of trailing hops (e.g. the
second-to-last if there are two proxies) rather than always `[-1]` —
whatever matches the real deployment topology.

### 11.4 MEDIUM — `_rate_hits` grows without bound, amplified by 11.3

Relates to §1 (single-process assumption) and §4.

**Where:** `auth_dependencies.py`, `rate_limit()`'s inner `dependency()`,
lines 70-88.

```python
with _rate_lock:
    hits = [t for t in _rate_hits.get(ip, []) if t > cutoff]
    if len(hits) >= max_requests:
        ...
    hits.append(now)
    _rate_hits[ip] = hits
```

**Why it's real:** every call writes `_rate_hits[ip] = hits`, even when
`hits` is empty after pruning — the key itself is never deleted, so the
dict only grows for the life of the process. On its own this is a slow
leak (bounded by how many distinct real IPs ever hit the auth routes). But
if 11.3 is exploitable, an attacker rotating a fake `X-Forwarded-For`
value per request also forces one new permanent dict entry per fake
value — turning a rate-limit bypass into a memory-exhaustion path against
the single uvicorn process at the same time (no `--workers`, so no
process-level isolation cushions it either).

**Fix:** delete the key instead of storing an empty list, so idle/one-off
IPs don't accumulate:

```python
with _rate_lock:
    hits = [t for t in _rate_hits.get(ip, []) if t > cutoff]
    if len(hits) >= max_requests:
        retry_after = int(hits[0] + window_seconds - now) + 1
        _rate_hits[ip] = hits
        raise HTTPException(
            status_code=429,
            detail="Too many attempts — slow down and try again shortly",
            headers={"Retry-After": str(retry_after)},
        )
    hits.append(now)
    _rate_hits[ip] = hits  # always non-empty here (just appended), so no
                            # extra branch needed on this path
```

The branch that needs the change is the pruning step itself — when
`hits` comes back empty after filtering and the IP isn't about to be
rate-limited, drop the key rather than writing `[]` back:

```python
    hits = [t for t in _rate_hits.get(ip, []) if t > cutoff]
    if not hits:
        _rate_hits.pop(ip, None)
```

Fixing 11.3 (an unspoofable bucketing key) removes the attacker-controlled
growth vector entirely; evicting empty keys closes the remaining slow leak
from legitimate one-off callers. For extra safety, a periodic sweep of
`_rate_hits` (dropping any key whose newest timestamp is older than
`window_seconds`) could run on a timer alongside the other background
loops in §6, so a bug in the eviction logic can't silently reintroduce the
leak.

### 11.5 LOW — `rate_limit()`'s bucket store isn't scoped per limiter instance

Relates to §4.

**Where:** `auth_dependencies.py`, lines 47-48 and 63-88.

```python
_rate_lock = threading.Lock()
_rate_hits: dict[str, list[float]] = {}


def rate_limit(max_requests: int, window_seconds: int):
    def dependency(request: Request) -> None:
        ...
        hits = [t for t in _rate_hits.get(ip, []) if t > cutoff]
```

**Why it's real:** `_rate_hits` is a single module-level dict keyed only
by client IP. Every call to `rate_limit(...)` closes over that same dict,
so two *different* limiter instances (different `max_requests`/
`window_seconds`) created for two different routes would silently share
bucket state for the same IP — traffic on one route counts against the
other's threshold. Harmless today because there's exactly one call site
(`routes/auth.py`'s `_LOGIN_RATE_LIMIT`, reused unchanged across three
routes, which is the intended shared budget). But the function's own
docstring advertises itself as a general, per-route composable dependency
(`@router.post("/login", dependencies=[Depends(rate_limit(10, 60))])`) —
the next route that adds its own differently-tuned `rate_limit(...)` call
will get incorrect enforcement without any error to signal it.

**Fix:** namespace the store by limiter identity, not just IP, so future
reuse is safe by construction:

```python
def rate_limit(max_requests: int, window_seconds: int):
    # Fresh per-limiter store — different rate_limit(...) calls never
    # share bucket state, even for the same client IP.
    hits_by_ip: dict[str, list[float]] = {}

    def dependency(request: Request) -> None:
        now = time.monotonic()
        ip = _client_ip(request)
        cutoff = now - window_seconds

        with _rate_lock:
            hits = [t for t in hits_by_ip.get(ip, []) if t > cutoff]
            if len(hits) >= max_requests:
                retry_after = int(hits[0] + window_seconds - now) + 1
                hits_by_ip[ip] = hits
                raise HTTPException(
                    status_code=429,
                    detail="Too many attempts — slow down and try again shortly",
                    headers={"Retry-After": str(retry_after)},
                )
            hits.append(now)
            hits_by_ip[ip] = hits

    return dependency
```
This also makes the fix in 11.4 (empty-entry eviction) apply per-limiter
rather than to one shared global dict.

## Related documents

- `README.md` — database house rules, full migration history, verified
  endpoint list.
- `docs/round-robin-allocation-design.md` — the lead-assignment engine's
  design (eligibility rules, Group 1/Group 2 tiers, daily reset).
- `docs/meta-capi-token-leak-fix.md` — a specific security fix to the Meta
  CAPI integration; read alongside §4/§6 for how the CAPI drain is secured.
