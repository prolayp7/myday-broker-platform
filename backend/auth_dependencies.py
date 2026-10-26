"""Request-gate dependencies: session/role checks for admin routes, and
per-IP rate limiting for the public auth routes. Both are cross-cutting
"can this request proceed at all" gates, applied uniformly via Depends(),
the same idiom the whole codebase uses for route protection.

SECURITY (see the auth security reviews, 2026-07-10/11):
- get_current_broker does NOT trust the role/claims embedded in the JWT
  alone — it re-fetches the broker row on every single request. This is
  deliberate: a broker who gets blocked or archived mid-session must lose
  access on their VERY NEXT request, not whenever their 1-hour access token
  happens to expire. The embedded JWT claims are only ever a fast-path hint;
  the database row is the source of truth for whether a session is still
  valid.
- Only a token with stage="access" is accepted here. A refresh token or a
  pending_totp token must never work as a session — decode_token doesn't
  distinguish stages, so this dependency does.
- rate_limit() guards the PUBLIC auth routes (login, verify, change-password)
  against brute force at the request layer. The per-account lockout in
  services/auth.py stops one account being hammered; this stops one IP
  hammering MANY accounts (credential stuffing / enumeration at scale),
  which lockout alone can't see.
"""

import threading
import time

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from jwt import PyJWTError

import security
from repositories import broker as repo

_bearer = HTTPBearer(auto_error=False)


# ── Rate limiting ────────────────────────────────────────────────────────
# In-memory sliding window, keyed by client IP. In-memory is correct here
# precisely because the app runs as a SINGLE uvicorn process (see Procfile:
# no --workers) — there's one shared dict, no cross-process state to sync,
# so no Redis. If the deploy ever moves to multiple workers, this must move
# to a shared store (Redis) or it'll under-count across processes.
#
# FastAPI runs sync dependencies in a threadpool, so the shared store is
# guarded by a lock. Old timestamps are pruned on every access, which also
# keeps memory naturally bounded — idle IPs age out to empty and get dropped.
_rate_lock = threading.Lock()
_rate_hits: dict[str, list[float]] = {}


def _client_ip(request: Request) -> str:
    """Which bucket this request's rate-limit counter belongs in.

    Reads the resolved client, NOT the X-Forwarded-For header. Uvicorn's
    ProxyHeadersMiddleware (enabled in the Procfile with --proxy-headers)
    parses that header and rewrites scope["client"], so reading the raw header
    here again would second-guess the one component whose job this is.

    ⚠️ NOT YET SPOOF-PROOF — a deliberate, staged position, not an oversight.

    The Procfile currently passes --forwarded-allow-ips='*', which trusts every
    hop and therefore takes the FIRST entry in the header: the one an attacker
    writes. A script rotating that value still gets a fresh bucket per request,
    exactly as before this change. What '*' buys today is that the header
    parsing lives in one place, ready to tighten.

    THE REMAINING STEP: Railway's proxy currently reaches us on a public
    address, so there is no narrow range to trust yet. Once it is moved to a
    private IP, set --forwarded-allow-ips to that range. Uvicorn then walks the
    header in REVERSE, taking the last hop that is not a trusted proxy — which
    a prepended value cannot influence, because real proxies append:

        attacker sends:  1.2.3.4, 203.0.113.9, <proxy>
        with '*'      -> 1.2.3.4      (attacker-controlled)
        with a range  -> 203.0.113.9  (the real client)

    WHAT STILL HOLDS IN THE MEANTIME. This bucketing is not the only brute
    force defence, and not the one that matters most: services/auth.py locks an
    account after 5 failed attempts for 15 minutes, keyed on the broker row,
    which no header can forge. What the per-IP limit uniquely catches is
    credential stuffing spread ACROSS many accounts, and that is the part still
    bypassable until the range is set.

    "unknown" when there is no client at all: every such request then shares a
    single bucket, which fails closed — more limiting, never less.
    """
    return request.client.host if request.client else "unknown"


def rate_limit(max_requests: int, window_seconds: int):
    """Build a dependency that allows at most `max_requests` per client IP
    per rolling `window_seconds`. 429 with Retry-After once exceeded.
    Composable per-route, matching the Depends() idiom used everywhere else:
        @router.post("/login", dependencies=[Depends(rate_limit(10, 60))])
    """

    def dependency(request: Request) -> None:
        now = time.monotonic()
        ip = _client_ip(request)
        cutoff = now - window_seconds

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
            _rate_hits[ip] = hits

    return dependency


def get_current_broker(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> dict:
    """Resolve the calling broker from the Authorization header, or raise
    401. Returns the live AUTH_COLUMNS row (see repositories/broker.py) —
    NOT the SAFE_COLUMNS shape callers normally get back from the API."""
    if credentials is None:
        raise HTTPException(401, "Not authenticated")

    try:
        payload = security.decode_token(credentials.credentials)
    except PyJWTError:
        raise HTTPException(401, "Invalid or expired token")

    if payload.get("stage") != "access":
        raise HTTPException(401, "This token can't be used as a session")

    rows = repo.get_auth_by_id(payload["sub"]).data
    if not rows:
        raise HTTPException(401, "Broker not found")

    broker = rows[0]

    # Re-checked live, every request — never inferred from the token alone.
    if broker["is_archived"]:
        raise HTTPException(401, "This account is archived")
    if broker["is_blocked_by_admin"]:
        raise HTTPException(401, "This account is blocked")
    if not broker["is_active"]:
        raise HTTPException(401, "This account is deactivated")

    return broker


def require_admin(broker: dict = Depends(get_current_broker)) -> dict:
    """Gate for every admin/CRUD route — communities, broker groups, brokers,
    lead forms. A plain 'broker' authenticates fine and works the
    broker-facing routes, but cannot call these.

    THE ONLY authorization decision in this codebase. Every admin route
    reaches it through this one function, so a change here changes the whole
    permission model — which is the point, and the reason it is worth keeping
    to a single equality check.

    Compares for equality rather than membership: this used to accept
    ("admin", "manager"), which made the two indistinguishable and left the
    lower tier able to reach anything the higher one could. `manager` was
    dropped in migrations/0066 precisely because a tier granting exactly what
    the tier above grants is a trap rather than a feature.

    Reads the role from the LIVE broker row (get_current_broker re-fetches it
    every request), never from the token's role claim — so a role change takes
    effect on the next request, with no need to re-issue a session."""
    if broker["role"] != "admin":
        raise HTTPException(403, "Admin role required")
    return broker


def try_get_bearer_broker_id(credentials: HTTPAuthorizationCredentials | None = Depends(_bearer)) -> str | None:
    """Used ONLY by /auth/change-password, which must accept EITHER a real
    session (voluntary change) OR a pending_token in the request body
    (forced first-login change) — unlike every other route, a missing/
    invalid header here is not itself an error, just "try the other path".
    Does NOT re-check live block/archived/active state like
    get_current_broker does — services.auth.change_password re-fetches the
    broker fresh anyway before doing anything."""
    if credentials is None:
        return None
    try:
        payload = security.decode_token(credentials.credentials)
    except PyJWTError:
        return None
    if payload.get("stage") != "access":
        return None
    return payload["sub"]
