"""Data access for the broker table. Only talks to Supabase.

SECURITY: every read uses SAFE_COLUMNS, never select("*"), so password_hash
can never accidentally be returned to a caller. Any new query added here
must use SAFE_COLUMNS too.

password_hash and totp_secret are secrets, not profile data — they are
FETCHED ONLY through AUTH_COLUMNS, by the auth module alone, never through
SAFE_COLUMNS. Do not ever add either column to SAFE_COLUMNS."""

from datetime import datetime, timezone

from db import supabase

# Every column EXCEPT password_hash and totp_secret. Listed explicitly so
# neither secret is impossible to leak by accident — adding a column to the
# table does not silently start exposing it.
SAFE_COLUMNS = (
    "id, group_id, name, email, phone, role, date_of_birth, address, "
    "ribo_license_number, lines_of_authority, resident_province, "
    "license_expiry_date, eo_start_date, eo_expiry_date, is_active, "
    "daily_cap, availability_status, is_blocked_by_admin, blocked_reason, "
    "blocked_at, blocked_by, is_archived, archived_at, "
    "totp_enrolled_at, must_change_password, created_at, updated_at, "
    # self_paused_at is listed HERE and not only on the table, because this is
    # an explicit column list: a column the API never names is a column no
    # caller can read, however correct the migration was.
    "max_cap, ready_for_more, self_paused, self_paused_at, "
    "pending_daily_cap, pending_daily_cap_effective_at, "
    # pending_max_cap and its date were on the table but missing here, so no
    # API could read them — a scheduled max-cap change was invisible to every
    # caller including the admin screen that set it.
    "pending_max_cap, pending_max_cap_effective_at, "
    # No trailing comma — this is the LAST entry. Other columns have followed
    # it and been removed (commission splits in 0083, the Zoom and CardDAV
    # columns when those integrations were retired); dropping such a line
    # without taking its comma produces a malformed column list, and
    # SAFE_COLUMNS is read by EVERY broker query.
    "scheduled_unpause_at, eligible_from"
)

# Used ONLY by services/auth.py to verify a password or a TOTP code. Never
# used to build an API response — these two columns must never leave the
# server.
AUTH_COLUMNS = (
    "id, email, password_hash, totp_secret, totp_enrolled_at, role, "
    "is_active, is_blocked_by_admin, is_archived, "
    "failed_login_count, locked_until, must_change_password"
)


def list_by_group(group_id: str):
    return (
        supabase.table("broker")
        .select(SAFE_COLUMNS)
        .eq("group_id", group_id)
        .order("created_at")
        .execute()
    )


def insert(values: dict):
    # values includes password_hash on the way IN; the returning select
    # strips it back out so the response never carries it.
    return supabase.table("broker").insert(values).execute()
def get_safe(broker_id: str):
    return supabase.table("broker").select(SAFE_COLUMNS).eq("id", broker_id).execute()


def get_safe_with_group(broker_id: str):
    """SAFE_COLUMNS plus the group's own name and number.

    The profile screen says "Gta Team · Group 1", not a uuid. Embedded rather
    than fetched separately because it is one row either way and a second
    round trip for two strings is not worth the code that would coordinate it.
    """
    return (
        supabase.table("broker")
        .select(f"{SAFE_COLUMNS}, broker_group(name, group_number)")
        .eq("id", broker_id)
        .execute()
    )


def update(broker_id: str, values: dict):
    return supabase.table("broker").update(values).eq("id", broker_id).execute()


def get_group_id(broker_id: str):
    """Just the broker's current group_id — used to check they're not being
    'moved' into the group they're already in."""
    return supabase.table("broker").select("group_id").eq("id", broker_id).execute()


def count_active_admins(exclude_broker_id: str | None = None):
    """How many admins can still SIGN IN — the guard behind "you cannot lock
    out the last admin" (services/broker.py::move_broker, ::block_broker).

    The filters are exactly the three auth_dependencies.py::get_current_broker
    re-checks on every request (is_archived / is_blocked_by_admin /
    is_active), because "an admin who counts" means precisely "an admin who
    can get in". Counting rows that merely say role='admin' would let the last
    reachable admin be locked out by an already-locked-out one — which is the
    exact failure this exists to prevent.

    exclude_broker_id leaves the broker being acted on OUT of the count, so
    the caller asks "would anyone be left?" rather than "is anyone here?" —
    otherwise the target always counts itself and the guard never fires."""
    query = (
        supabase.table("broker")
        .select("id", count="exact")
        .eq("role", "admin")
        .eq("is_active", True)
        .eq("is_blocked_by_admin", False)
        .eq("is_archived", False)
    )
    if exclude_broker_id is not None:
        query = query.neq("id", exclude_broker_id)
    return query.execute()


def move(broker_id: str, group_id: str, is_active: bool):
    # is_active travels WITH the move so it's a single atomic write, never a
    # separate follow-up call that could partially fail (broker moved but
    # left is_active stale). See services/broker.py::move_broker for why this
    # value is true/false: Recycle Bin -> false (also blocks login, see
    # auth_dependencies.py), any real group -> true (covers restoring).
    return (
        supabase.table("broker")
        .update({"group_id": group_id, "is_active": is_active})
        .eq("id", broker_id)
        .execute()
    )


def block(broker_id: str, reason: str):
    # blocked_by is left null — there's no authenticated "current admin"
    # concept wired up yet, so we can't truthfully record who blocked this
    # broker. Revisit once real auth exists.
    return (
        supabase.table("broker")
        .update({
            "is_blocked_by_admin": True,
            "blocked_reason": reason,
            "blocked_at": datetime.now(timezone.utc).isoformat(),
            "blocked_by": None,
        })
        .eq("id", broker_id)
        .execute()
    )


def archive(broker_id: str):
    # One-way: also sets is_active=false so an archived broker never shows
    # as "Active" anywhere. group_id is deliberately left untouched — the
    # broker keeps their real historical group association forever.
    return (
        supabase.table("broker")
        .update({
            "is_archived": True,
            "archived_at": datetime.now(timezone.utc).isoformat(),
            "is_active": False,
        })
        .eq("id", broker_id)
        .execute()
    )


def get_auth_by_email(email: str):
    """Fetch the auth-relevant columns (including password_hash/totp_secret)
    for the login flow. This is the ONLY place those two secrets are ever
    read — never expose this row shape outside services/auth.py."""
    return supabase.table("broker").select(AUTH_COLUMNS).eq("email", email).execute()


def get_auth_by_id(broker_id: str):
    """Same as get_auth_by_email, keyed by id — used by get_current_broker
    to re-check live status (active/blocked/archived) on every request."""
    return supabase.table("broker").select(AUTH_COLUMNS).eq("id", broker_id).execute()


def record_login_success(broker_id: str):
    """Reset the failed-attempt counter and clear any lock — called after a
    fully successful login (password AND TOTP both correct)."""
    return (
        supabase.table("broker")
        .update({"failed_login_count": 0, "locked_until": None})
        .eq("id", broker_id)
        .execute()
    )


def record_login_failure(broker_id: str, new_count: int, locked_until: str | None):
    """Persist an incremented failed-attempt count, and a lock timestamp once
    the threshold is hit. locked_until is None until the threshold trips."""
    values = {"failed_login_count": new_count}
    if locked_until is not None:
        values["locked_until"] = locked_until
    return supabase.table("broker").update(values).eq("id", broker_id).execute()


def set_totp_secret(broker_id: str, encrypted_secret: str):
    """Save a freshly-generated (encrypted) TOTP secret mid-enrollment,
    BEFORE it's confirmed — totp_enrolled_at stays null until the broker
    proves they scanned it correctly by submitting one valid code."""
    return (
        supabase.table("broker")
        .update({"totp_secret": encrypted_secret})
        .eq("id", broker_id)
        .execute()
    )


def confirm_totp_enrollment(broker_id: str):
    """Mark enrollment complete — called by services/auth.py::verify_login the
    first time a broker submits a valid code for a secret set by
    set_totp_secret above. Stamping totp_enrolled_at is what flips that broker
    from "enrolling" to "enrolled": login() reads it to decide between
    status="enrollment_required" (send a fresh QR) and status="totp_required"
    (just prompt for a code)."""
    return (
        supabase.table("broker")
        .update({"totp_enrolled_at": datetime.now(timezone.utc).isoformat()})
        .eq("id", broker_id)
        .execute()
    )


def set_pending_daily_cap(broker_id: str, daily_cap: int, effective_at: str):
    """Deferred cap change (design §6) — writes pending_daily_cap only, NEVER
    daily_cap. Promoted by the daily reset job (migrations/0020) once
    effective_at arrives."""
    return (
        supabase.table("broker")
        .update({"pending_daily_cap": daily_cap, "pending_daily_cap_effective_at": effective_at})
        .eq("id", broker_id)
        .execute()
    )


def set_pending_max_cap(broker_id: str, max_cap: int, effective_at: str):
    """Same deferred pattern as set_pending_daily_cap, for the Group-2
    ceiling — writes pending_max_cap only, NEVER max_cap directly. Promoted
    by the daily reset job once effective_at arrives."""
    return (
        supabase.table("broker")
        .update({"pending_max_cap": max_cap, "pending_max_cap_effective_at": effective_at})
        .eq("id", broker_id)
        .execute()
    )


def set_scheduled_unpause(broker_id: str, effective_at: str):
    """Deferred unblock (design §5) — writes scheduled_unpause_at only.
    is_blocked_by_admin stays true until the reset job promotes this."""
    return (
        supabase.table("broker")
        .update({"scheduled_unpause_at": effective_at})
        .eq("id", broker_id)
        .execute()
    )


def set_eligible_from(broker_id: str, eligible_from: str):
    """New-broker/deferred activation (design §5) — set at onboarding time."""
    return (
        supabase.table("broker")
        .update({"eligible_from": eligible_from})
        .eq("id", broker_id)
        .execute()
    )


def set_ready_for_more(broker_id: str, ready_for_more: bool):
    """Group-2 daily opt-in (design §8) — takes effect immediately, cleared
    every morning by the reset job."""
    return (
        supabase.table("broker")
        .update({"ready_for_more": ready_for_more})
        .eq("id", broker_id)
        .execute()
    )


def set_self_paused(broker_id: str, paused: bool):
    """Stopping or resuming a broker's OWN leads. Immediate both ways.

    TWO CALLERS, both in services/broker.py: the broker themselves via
    set_my_self_paused, and an admin via resume_broker_leads — which only ever
    passes False. There is deliberately no admin path that passes True; an
    admin stopping a broker uses is_blocked_by_admin, which records a reason.

    The RPC also stamps broker.self_paused_at (0105), so nothing here needs to
    know when the pause started — the column is maintained by the one statement
    that changes the flag.

    Through an RPC rather than a plain update, for two things a PostgREST
    update cannot do:

      * it takes the group's advisory lock, so this queues behind an
        assignment already in flight rather than racing it — without that, a
        lead can be delivered AFTER the screen has said leads are stopped;
      * it returns the broker's group_id, so the caller can drain the backlog
        without a second read that could fail on its own.

    Returns the group id, or None when there is no such broker. The caller
    must unwrap .data — this hands back the response object like every other
    function in this module.
    """
    return supabase.rpc(
        "set_broker_self_paused",
        {"p_broker_id": broker_id, "p_paused": paused},
    ).execute()


def set_password(broker_id: str, password_hash: str):
    """Store a new password hash and clear must_change_password — used both
    by the forced first-login change and any later voluntary change (same
    endpoint, see services/auth.py::change_password)."""
    return (
        supabase.table("broker")
        .update({"password_hash": password_hash, "must_change_password": False})
        .eq("id", broker_id)
        .execute()
    )


def reset_credentials(broker_id: str, password_hash: str):
    """Everything an admin's "Generate password" clears, in one write.

    NOT set_password above: that CLEARS must_change_password, because it
    serves a broker choosing their own. This sets it — the whole point of an
    admin-issued password is that it must be replaced.

    Six columns, and the last two are the ones easy to forget: a broker who
    has forgotten their password has usually already failed five times and
    locked themselves out for fifteen minutes (services/auth.py), which is
    exactly why they called. Leaving the lockout means handing over a working
    password that still does not work, and a second support call.

    totp_secret and totp_enrolled_at go too, so one action also covers the
    broker who lost their PHONE. Login regenerates a QR the moment
    totp_enrolled_at is null — no extra flow needed.

    RESTORED after commit 8500532 (Zoom retirement) deleted it along with
    generate_password and confirm_totp_enrollment; the route that needs it was
    left in place, so "Generate password" returned 500 in production from
    2026-09-16 until this went back.
    """
    return (
        supabase.table("broker")
        .update(
            {
                "password_hash": password_hash,
                "must_change_password": True,
                "totp_secret": None,
                "totp_enrolled_at": None,
                "locked_until": None,
                "failed_login_count": 0,
            }
        )
        .eq("id", broker_id)
        .execute()
    )
