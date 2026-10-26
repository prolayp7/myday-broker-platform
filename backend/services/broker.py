"""Business logic for brokers. Owns password hashing, HTTPException
decisions, and the soft-delete rule (brokers are deactivated, never deleted,
because deleting would destroy their attendance/lead history).

Also owns every field the round-robin lead-assignment engine reads to decide
eligibility (is_active, is_blocked_by_admin, self_paused, daily_cap, max_cap,
ready_for_more, eligible_from) — see docs/round-robin-allocation-design.md.
self_paused is the one a BROKER sets for themselves; every other field here is
an admin's to write. An admin may CLEAR self_paused on someone's behalf
(resume_broker_leads) but never set it — stopping a broker is what
is_blocked_by_admin is for, and it records a reason.
The engine itself is SQL, not Python: migrations/0019_assignment_function.sql
(assign_lead) reads these columns live on every assignment, and
migrations/0020_daily_reset.sql (daily_lead_allocation_reset) is what
actually promotes the pending_* fields this file writes into their real
columns each morning. This file only ever writes the PENDING/deferred side
for an existing broker (see the "Round-robin allocation controls" section
below) — it never writes daily_cap/max_cap/is_blocked_by_admin directly for
someone already live, so a change here can't take effect mid-day."""

import logging
import re
import secrets
import unicodedata
from datetime import date, datetime, timedelta
from decimal import Decimal
from uuid import UUID
from zoneinfo import ZoneInfo

from fastapi import HTTPException

from db_errors import db_error
from repositories import audit_log as audit_log_repo
from repositories import broker as repo
from repositories import broker_group as group_repo
from repositories import lead as lead_repo
from repositories import phone_match as phone_match_repo
from repositories import premium_history as premium_repo
from services import commission as commission_service
from services.lead_cost import parse_month
from schemas.broker import (
    BlockBrokerRequest,
    CreateBrokerRequest,
    MoveBrokerRequest,
    ScheduleUnpauseRequest,
    SetDailyCapRequest,
    SetMaxCapRequest,
    SetReadyForMoreRequest,
    UpdateBrokerRequest,
)
from security import hash_password

logger = logging.getLogger("myday.broker")

# The single timezone the round-robin's "day" is anchored to — see
# docs/round-robin-allocation-design.md §5/§7. Same zone the daily reset job
# (migrations/0020) uses in SQL; kept in sync here since Python is where a
# new broker's eligible_from is computed.
_ONTARIO_TZ = ZoneInfo("America/Toronto")


def _tomorrow_ontario_midnight() -> datetime:
    """The start of the next Ontario calendar day. Used as eligible_from for
    a newly onboarded broker (§5: 'a broker who joins today starts receiving
    leads tomorrow') — any timestamp on tomorrow's Ontario date works equally
    well against the reset job's `<= now()` promotion check, but midnight is
    the clearest value to show back to an admin."""
    now_ontario = datetime.now(_ONTARIO_TZ)
    tomorrow = (now_ontario + timedelta(days=1)).date()
    return datetime.combine(tomorrow, datetime.min.time(), tzinfo=_ONTARIO_TZ)

# Deliberately readable/speakable, not vault-grade — the system sets this
# once at onboarding and it's used exactly once: the broker is forced to
# replace it with their own on their very next login (must_change_password),
# and TOTP is the real ongoing security layer, not this password. Lives here
# (broker business/policy logic), not in security.py (crypto primitives).
_TEMP_PASSWORD_WORDS = [
    "amber", "birch", "cedar", "coral", "delta", "ember", "falcon", "granite",
    "harbor", "indigo", "juniper", "lagoon", "maple", "nectar", "oak",
    "pebble", "quartz", "river", "summit", "timber", "violet", "willow",
]


def _generate_temp_password() -> str:
    """Two random words + a 2-digit number, e.g. 'river-oak42' — easy for an
    admin to read aloud or type when relaying it to the new broker."""
    word1 = secrets.choice(_TEMP_PASSWORD_WORDS)
    word2 = secrets.choice(_TEMP_PASSWORD_WORDS)
    return f"{word1}-{word2}{secrets.randbelow(90) + 10}"



def generate_password(broker_id: UUID, admin_broker_id: str) -> dict:
    """Issue a temporary password for a broker, and clear what would stop
    them using it.

    THE ONLY WAY A BROKER GETS A PASSWORD — used both to activate a
    newly-onboarded one and to recover a broker who has forgotten theirs.
    They are the same action, so there is one code path rather than a
    "forgot password" feature sitting beside onboarding doing the same work.

    Repeatable by design: pressing it again issues a fresh password. Before
    this, the onboarding response was the only place a temp password ever
    appeared, and an admin who lost that screen had no way to reissue it.

    No new auth logic is needed for any of this. Login already returns
    password_change_required when must_change_password is set, and
    enrollment_required with a fresh QR when totp_enrolled_at is null — see
    repositories/broker.reset_credentials for what is cleared and why.

    THE PASSWORD IS RETURNED ONCE and never stored in readable form. It is
    deliberately absent from the audit row below: that the reset happened is
    a fact worth keeping, the live credential is not.

    RESTORED verbatim after commit 8500532 deleted it while retiring Zoom;
    routes/broker.py kept calling it, so the button returned 500.
    """
    _reject_if_archived(str(broker_id))

    temp_password = _generate_temp_password()
    result = repo.reset_credentials(str(broker_id), hash_password(temp_password))
    if not result.data:
        raise HTTPException(status_code=404, detail="Broker not found")

    audit_log_repo.insert(
        actor_broker_id=admin_broker_id,
        action="password_generated",
        entity_type="broker",
        entity_id=str(broker_id),
        # What was reset, never what it was reset TO.
        detail={"reset": ["password", "totp", "lockout"], "actor": "admin"},
    )

    return {"temporary_password": temp_password}

# A broker group is an org/headcount split (not a routing unit) and is capped
# so no single group grows unmanageably large. Keep this in sync with
# MAX_BROKERS_PER_GROUP on the frontend onboarding UI.
MAX_BROKERS_PER_GROUP = 10


def _safe_broker_or_404(broker_id: str):
    """Fetch the safe (no password_hash) view of a broker, or 404 if the
    row vanished between operations. Guards against an empty-list IndexError."""
    rows = repo.get_safe(broker_id).data
    if not rows:
        raise HTTPException(status_code=404, detail="Broker not found")
    return rows[0]


def _reject_if_archived(broker_id: str):
    """Archive is a PERMANENT, one-way lock — every other mutation (edit,
    move, block/unblock, deactivate) must refuse to touch an archived
    broker. Called first in every mutating function below."""
    broker = _safe_broker_or_404(broker_id)
    if broker["is_archived"]:
        raise HTTPException(409, "This broker is archived and can no longer be changed")


def _reject_if_last_admin(broker_id: str, action: str):
    """Refuse to lock the LAST admin out of the system.

    WHY THIS EXISTS. Deactivating (moving to the Recycle Bin) and blocking
    both stop an account signing in — auth.py checks is_active and
    is_blocked_by_admin before issuing a token. Applied to the only admin,
    either one locks the system's own administration away with no in-app
    route back: the screen that undoes it is itself behind require_admin.
    That is not hypothetical — it happened, and the account had to be
    restored by hand against the database.

    Archiving needs no guard of its own: archive_broker already refuses
    unless the broker is sitting in the Recycle Bin, and this rule is what
    stops an admin getting there.

    THE LAST ONE, NOT ANY ONE. A brokerage with two admins must still be able
    to offboard one, so the rule is "someone must be left" rather than
    "admins are untouchable" — count_active_admins excludes the target and
    asks whether anyone remains. With a single admin the two readings behave
    identically; they diverge the day a second is added, and this one needs
    no code change then.

    Non-admins are not affected at all: a broker is deactivated, blocked and
    archived exactly as before."""
    broker = _safe_broker_or_404(broker_id)
    if broker["role"] != "admin":
        return

    remaining = repo.count_active_admins(exclude_broker_id=broker_id).count or 0
    if remaining == 0:
        raise HTTPException(
            409,
            f"{action} would leave no active admin — nobody could sign in to "
            "undo it. Add another admin first.",
        )


def list_brokers(group_id: UUID):
    return repo.list_by_group(str(group_id)).data
def create_broker(group_id: UUID, request: CreateBrokerRequest):
    # New hires start in a real working group — the Recycle Bin is only for
    # brokers on their way out (and is cap-exempt, so without this guard a
    # direct onboard into it would sometimes succeed, sometimes 409 "full").
    if group_repo.is_recycle_bin(str(group_id)):
        raise HTTPException(400, "Brokers can't be onboarded into the Recycle Bin")

    # Enforce the per-group headcount cap. This is a check-then-insert, so in
    # theory two concurrent onboards could both pass at count 9 and land the
    # group at 11 — acceptable for a single-operator admin tool. If concurrency
    # ever becomes real, replace with a DB-level trigger/constraint.
    existing = group_repo.count_brokers(str(group_id)).count or 0
    if existing >= MAX_BROKERS_PER_GROUP:
        raise HTTPException(
            409, f"This group is full ({MAX_BROKERS_PER_GROUP} brokers max) — create another group"
        )

    # Generate the one-time temp password, hash it for storage, and keep the
    # plaintext ONLY to return once in the response — it's never stored in
    # plaintext and can't be recovered afterward.
    values = request.model_dump(mode="json")
    values["group_id"] = str(group_id)
    # AN UNUSABLE PASSWORD, thrown away unread. Onboarding no longer issues a
    # working credential — the admin presses "Generate password" afterwards,
    # the same action they will press again if this broker ever forgets it.
    #
    # The column is NOT NULL (migrations/0003_broker.sql), so it has to hold
    # something. A random value nobody has seen means no password can ever
    # match it, which is exactly the state wanted: the broker exists and
    # cannot log in yet. That needs no migration and no nullable column.
    values["password_hash"] = hash_password(secrets.token_urlsafe(32))
    # Set here too, so the row is honest even before a password is generated
    # — and generate_password sets it again.
    values["must_change_password"] = True
    # A broker who joins today starts receiving leads tomorrow (design §5) —
    # eligible_from holds them out of round-robin eligibility until then.
    values["eligible_from"] = _tomorrow_ontario_midnight().isoformat()

    try:
        result = repo.insert(values)
    except Exception as err:
        # 23505 = email already taken (unique). 23503 = group_id doesn't exist.
        # P0001 = the cap trigger fired — a concurrent onboard filled the last
        # slot between our pre-check above and this insert.
        raise db_error(
            err,
            on_unique=HTTPException(409, "A broker with that email already exists"),
            on_foreign_key=HTTPException(400, "That broker group doesn't exist"),
            on_group_full=HTTPException(
                409, f"This group is full ({MAX_BROKERS_PER_GROUP} brokers max) — create another group"
            ),
        )

    # The insert's own return would include password_hash — re-fetch through
    # the safe-column query so the response can never carry the hash. No
    # password comes back: the broker is created, not yet activated.
    new_id = result.data[0]["id"]
    return {"broker": _safe_broker_or_404(new_id)}
def update_broker(broker_id: UUID, request: UpdateBrokerRequest):
    _reject_if_archived(str(broker_id))
    # exclude_unset: write only the fields the caller actually SENT.
    #
    # Several fields on this model carry defaults meant for CREATE — role
    # defaults to "broker", resident_province to "Ontario",
    # lines_of_authority to []. A plain model_dump() cannot tell an omitted
    # field from one deliberately set to its default, so it materialised
    # those defaults and wrote them: a PATCH that left role out silently
    # DEMOTED an admin, and one that left lines_of_authority out silently
    # cleared their licensing.
    #
    # Our own edit form always sends the full profile, which is why this
    # never fired — but any other client, or a future form that drops a
    # field, would strip data with no error to notice. Pydantic already
    # tracks which keys were present; this just stops throwing that away.
    values = request.model_dump(mode="json", exclude_unset=True)

    try:
        result = repo.update(str(broker_id), values)
    except Exception as err:
        # Unique violation here means the new email is already taken by another broker.
        raise db_error(err, on_unique=HTTPException(409, "A broker with that email already exists"))

    if not result.data:
        raise HTTPException(status_code=404, detail="Broker not found")

    return _safe_broker_or_404(str(broker_id))


def move_broker(broker_id: UUID, request: MoveBrokerRequest):
    _reject_if_archived(str(broker_id))
    current = repo.get_group_id(str(broker_id)).data
    if not current:
        raise HTTPException(status_code=404, detail="Broker not found")

    source_group_id = current[0]["group_id"]
    target_group_id = str(request.group_id)
    if source_group_id == target_group_id:
        raise HTTPException(400, "Broker is already in that group")

    # The Recycle Bin is a holding area, not a working team — exempt from
    # the cap, or "Deactivate" would eventually start failing once 10 people
    # have ever left. Every real working group still enforces the cap.
    target_is_recycle_bin = group_repo.is_recycle_bin(target_group_id)
    source_is_recycle_bin = group_repo.is_recycle_bin(source_group_id)

    # Moving INTO the Recycle Bin is what the UI calls "Deactivate" — it sets
    # is_active=false below, which blocks login. Checked only on that
    # direction: restoring an admin OUT of the Bin is the fix for this, not
    # another way to trigger it.
    if target_is_recycle_bin:
        _reject_if_last_admin(str(broker_id), "Deactivating this admin")

    if not target_is_recycle_bin:
        # Same-community rule: a broker belongs to their community's market
        # (region/language), so a lateral transfer between two REAL working
        # groups is only allowed within the same community. Deliberately
        # SKIPPED when the Recycle Bin is involved (deactivate INTO it, or
        # restore OUT of it) — those are lifecycle actions, not transfers, and
        # the single global Recycle Bin lives in one community, so enforcing
        # this on it would wrongly block deactivating brokers from every other
        # community.
        if not source_is_recycle_bin:
            source_community = group_repo.get_community_id(source_group_id).data
            target_community = group_repo.get_community_id(target_group_id).data
            if (
                source_community
                and target_community
                and source_community[0]["community_id"] != target_community[0]["community_id"]
            ):
                raise HTTPException(
                    400, "A broker can only move to a group within their own community"
                )

        # Same cap check as onboarding — the destination group must have
        # room. Check-then-insert race is acceptable here for the same
        # reason as create_broker (single-operator admin tool).
        existing = group_repo.count_brokers(target_group_id).count or 0
        if existing >= MAX_BROKERS_PER_GROUP:
            raise HTTPException(
                409, f"That group is full ({MAX_BROKERS_PER_GROUP} brokers max) — choose another"
            )

    # is_active mirrors the destination: false in the Recycle Bin (also blocks
    # login, see auth_dependencies.py — a deactivated broker shouldn't be able
    # to sign in either), true in any real group (covers restoring someone
    # from the Recycle Bin back to a working team via this same endpoint).
    try:
        result = repo.move(str(broker_id), target_group_id, is_active=not target_is_recycle_bin)
    except Exception as err:
        # FK violation here means the target group_id doesn't exist. P0001 =
        # the cap trigger fired (a concurrent move filled the last slot).
        raise db_error(
            err,
            on_foreign_key=HTTPException(400, "That broker group doesn't exist"),
            on_group_full=HTTPException(
                409, f"That group is full ({MAX_BROKERS_PER_GROUP} brokers max) — choose another"
            ),
        )

    if not result.data:
        raise HTTPException(status_code=404, detail="Broker not found")

    return _safe_broker_or_404(str(broker_id))


def block_broker(broker_id: UUID, request: BlockBrokerRequest):
    _reject_if_archived(str(broker_id))
    # is_blocked_by_admin stops a sign-in just as firmly as deactivating does
    # (services/auth.py checks both), so the last admin is protected here too.
    _reject_if_last_admin(str(broker_id), "Blocking this admin")
    result = repo.block(str(broker_id), request.reason)

    if not result.data:
        raise HTTPException(status_code=404, detail="Broker not found")

    return _safe_broker_or_404(str(broker_id))


# ── Round-robin allocation controls ─────────────────────────────────────
# See docs/round-robin-allocation-design.md §5-6. These write DEFERRED
# fields (never the live column directly) so a change never takes effect
# mid-day — see schemas/broker.py's SetDailyCapRequest/SetMaxCapRequest/
# ScheduleUnpauseRequest for the corresponding request shapes.

def schedule_unpause(broker_id: UUID, request: ScheduleUnpauseRequest, admin_broker_id: UUID):
    """Deferred unblock (design §5) — the ONLY way to unpause a broker once
    round-robin allocation is live. Pausing (block_broker, above) stays
    instant. This writes scheduled_unpause_at only; is_blocked_by_admin stays
    true until the first daily reset ON OR AFTER effective_at promotes it
    (migrations/0020), so leads resume the morning of the effective date —
    same regime as a brand-new broker's first day. Still requires the broker
    to actually be blocked; a no-op schedule on someone who was never paused
    would be confusing, not harmless."""
    _reject_if_archived(str(broker_id))
    broker = _safe_broker_or_404(str(broker_id))
    if not broker["is_blocked_by_admin"]:
        raise HTTPException(400, "This broker isn't currently blocked")

    result = repo.set_scheduled_unpause(str(broker_id), request.effective_at.isoformat())

    if not result.data:
        raise HTTPException(status_code=404, detail="Broker not found")

    audit_log_repo.insert(
        actor_broker_id=str(admin_broker_id),
        action="unpause_scheduled",
        entity_type="broker",
        entity_id=str(broker_id),
        detail={"effective_at": request.effective_at.isoformat(), "actor": "admin"},
    )

    return _safe_broker_or_404(str(broker_id))
def set_daily_cap(broker_id: UUID, request: SetDailyCapRequest, admin_broker_id: UUID):
    """Deferred cap change (design §6) — writes pending_daily_cap only.
    daily_cap is system-managed from here on; the reset job is its only
    writer. This is a separate endpoint from update_broker (the general
    profile edit) on purpose — see schemas/broker.py's _BrokerProfileFields
    docstring. request.effective_at is already guaranteed to be in the
    future by SetDailyCapRequest's own validator (schemas/broker.py) — this
    function doesn't re-check it."""
    _reject_if_archived(str(broker_id))
    result = repo.set_pending_daily_cap(str(broker_id), request.daily_cap, request.effective_at.isoformat())

    if not result.data:
        raise HTTPException(status_code=404, detail="Broker not found")

    audit_log_repo.insert(
        actor_broker_id=str(admin_broker_id),
        action="daily_cap_scheduled",
        entity_type="broker",
        entity_id=str(broker_id),
        detail={"new_daily_cap": request.daily_cap, "effective_at": request.effective_at.isoformat(), "actor": "admin"},
    )

    return _safe_broker_or_404(str(broker_id))


def set_max_cap(broker_id: UUID, request: SetMaxCapRequest, admin_broker_id: UUID):
    """Deferred max_cap change (design §6) — same pattern as set_daily_cap,
    for the Group-2 overtime ceiling."""
    _reject_if_archived(str(broker_id))
    result = repo.set_pending_max_cap(str(broker_id), request.max_cap, request.effective_at.isoformat())

    if not result.data:
        raise HTTPException(status_code=404, detail="Broker not found")

    audit_log_repo.insert(
        actor_broker_id=str(admin_broker_id),
        action="max_cap_scheduled",
        entity_type="broker",
        entity_id=str(broker_id),
        detail={"new_max_cap": request.max_cap, "effective_at": request.effective_at.isoformat(), "actor": "admin"},
    )

    return _safe_broker_or_404(str(broker_id))


def set_ready_for_more(broker_id: UUID, request: SetReadyForMoreRequest):
    """Group-2 daily opt-in (design §8) — takes effect immediately (it's
    today's live choice, not a scheduled change) and is cleared every
    morning by the reset job. Not audit-logged: this is a routine daily
    toggle a broker/admin can flip repeatedly, not a meaningful admin
    action worth a permanent trail entry (unlike pause/cap changes).

    Deliberately NOT rejected for a blocked broker: is_blocked_by_admin is
    already checked independently by assign_lead's eligibility query
    (migrations/0019), so a blocked broker toggling this on has no effect —
    rejecting it here too would just be a second, redundant gate on the same
    fact."""
    _reject_if_archived(str(broker_id))
    result = repo.set_ready_for_more(str(broker_id), request.ready_for_more)

    if not result.data:
        raise HTTPException(status_code=404, detail="Broker not found")

    broker = _safe_broker_or_404(str(broker_id))

    # OFFER THE LEADS ALREADY WAITING, not just the next one to arrive.
    #
    # Setting the flag alone used to change nothing for a lead that was
    # already sitting unassigned: it stayed there until another lead happened
    # to come in, or until the next morning's reset. So the broker who had just
    # said "I'll take more" got nothing, while the work they asked for was in
    # the group the whole time — the exact case this feature exists for.
    #
    # ON only. Turning it off frees no capacity and has no backlog to offer.
    #
    # THROUGH THE NORMAL ROTATION, not straight to this broker: if two people
    # have opted in they share the backlog by turn, and the one who clicked may
    # get none of it. That is the fair-share rule working, and it stops anyone
    # toggling the flag to jump the queue.
    #
    # Cap changes deliberately do NOT do this. A cap raise is deferred to the
    # next reset on purpose — a guaranteed daily share should not move halfway
    # through a day — so draining on one would apply today a change designed
    # for tomorrow. Different mechanism, different purpose.
    if request.ready_for_more:
        try:
            lead_repo.drain_pending_for_group(broker["group_id"])
        except Exception:
            # The flag is ALREADY saved, so the broker is opted in either way
            # and the next lead — or tomorrow's reset — still reaches them.
            # Raising here would report their choice as failed when it
            # succeeded.
            #
            # WHAT IS LOST IS THE BACKLOG, NOT MERELY IMMEDIACY — this comment
            # used to claim the latter and it was false. A lead that arrives
            # later assigns only ITSELF; nothing re-offers the ones already
            # waiting. So they sit until another explicit drain or tomorrow's
            # reset. set_my_self_paused reports this to the caller instead of
            # swallowing it; this path predates that and still cannot, because
            # its response model is the broker row.
            logger.exception(
                "Could not drain waiting leads for group %s after broker %s "
                "opted in for more",
                broker["group_id"],
                broker_id,
            )

    return broker


def set_my_self_paused(broker_id: UUID, paused: bool) -> dict:
    """A broker stopping or resuming their own leads. Immediate both ways.

    The point of the feature: a broker who is not working can stop leads
    reaching them, instead of them arriving and sitting unworked. A delivered
    lead counts against the day's cap whether or not anybody rings it, so
    "away and still receiving" is the most expensive state there is.

    SEPARATE FROM is_blocked_by_admin, which this must never touch. A broker
    able to clear that could undo an admin's block — exactly the capability an
    admin block exists to have. The two are asked about separately when
    somebody wants to know why a broker received nothing.

    RESUMING DRAINS THE POOL, through the normal rotation rather than straight
    to this broker — returning from lunch does not jump the queue past
    colleagues who worked all morning. ON only: pausing frees no capacity and
    has no backlog to offer.

    THE DRAIN'S FAILURE IS DATA, NOT AN EXCEPTION, and this is the part worth
    reading twice. The switch is already saved by the time the drain runs. The
    header rolls the toggle back on ANY thrown error (AppShell.tsx), so
    raising here would leave the database saying "receiving leads" and the
    screen saying "paused" — the broker waits for work while work is being
    assigned to them. Lying about the backlog is bad; lying about the pause
    itself is worse. So a failed drain comes back as backlog_drained: false
    with a 200, and the screen says what actually happened.
    """
    _reject_if_archived(str(broker_id))

    # .data, not the response object — set_self_paused hands back what
    # .execute() returned, like every other repository function here. Taking
    # the return value directly would pass a response object to the drain as
    # though it were a group id.
    group_id = repo.set_self_paused(str(broker_id), paused).data
    if not group_id:
        raise HTTPException(status_code=404, detail="Broker not found")

    backlog_drained = True
    if not paused:
        try:
            lead_repo.drain_pending_for_group(group_id)
        except Exception:
            logger.exception(
                "Could not drain waiting leads for group %s after broker %s "
                "came back on",
                group_id,
                broker_id,
            )
            backlog_drained = False

    # The NARROW shape, matching AvailabilityResponse exactly — not the broker
    # row, whose field is called self_paused and would not validate against a
    # model promising `paused`.
    return {"paused": paused, "backlog_drained": backlog_drained}


def resume_broker_leads(broker_id: UUID, admin_broker_id: UUID) -> dict:
    """An admin switching a broker's own pause back off, on their behalf.

    RESUME ONLY — there is deliberately no admin route that SETS self_paused.
    An admin stopping a broker already has one: PATCH /brokers/{id}/block,
    which records a reason, an actor and a timestamp. A second admin-driven way
    to stop someone, through a flag carrying none of that, would collapse the
    one distinction 0082 exists to preserve: "away sick" and "stopped by an
    admin" must stay tellable apart when somebody asks why a broker received
    nothing. The signature is the guarantee — this takes no `paused` argument.

    WHY AN ADMIN MAY CLEAR IT AT ALL, given they may not set it: self_paused
    persists now (0105). A broker who is away and unreachable — the exact case
    the feature was built for — cannot clear their own, and without this the
    switch is a trap that needs database access to escape.

    AUDIT-LOGGED, unlike set_ready_for_more and unlike the broker's own
    /my-availability. This is one person acting on another's state, and the
    broker will find leads arriving again without having asked. paused_since is
    read BEFORE the write, because the write clears it — this row is the only
    place the length of the absence survives.

    DRAINS THE BACKLOG through the normal rotation, same as the broker's own
    resume: without it the leads already waiting sit there until another lead
    happens to arrive, and the capacity just restored goes unused.

    A FAILED DRAIN IS NOT A FAILED RESUME, and it is REPORTED rather than
    swallowed — the reason this returns AvailabilityResponse's shape and not
    the broker row. The flag is already saved by the time the drain runs, so
    raising would tell the admin nothing happened when it did; staying silent
    would hide leads stuck in a group from the one person who can unstick them.
    See set_my_self_paused above for the same contract on the broker's path.
    """
    _reject_if_archived(str(broker_id))

    broker = _safe_broker_or_404(str(broker_id))
    if not broker["self_paused"]:
        # Not idempotent-by-silence: an admin pressing this on somebody who is
        # already receiving leads has misread the screen, and a cheerful 200
        # would confirm the misreading. Same shape as schedule_unpause's
        # "isn't currently blocked".
        raise HTTPException(400, "This broker hasn't stopped their own leads")

    paused_since = broker.get("self_paused_at")

    # .data, not the response object — see set_my_self_paused above.
    group_id = repo.set_self_paused(str(broker_id), False).data
    if not group_id:
        raise HTTPException(status_code=404, detail="Broker not found")

    audit_log_repo.insert(
        actor_broker_id=str(admin_broker_id),
        # ITS OWN ACTION NAME, not the reset's old broker_self_pause_cleared.
        # That one meant "the nightly job cleared everybody" — an event that
        # can no longer happen — so reusing it would blur two different things
        # in the one table somebody reads to answer "who did this".
        action="broker_leads_resumed_by_admin",
        entity_type="broker",
        entity_id=str(broker_id),
        detail={"paused_since": paused_since},
    )

    backlog_drained = True
    try:
        lead_repo.drain_pending_for_group(group_id)
    except Exception:
        logger.exception(
            "Could not drain waiting leads for group %s after admin %s "
            "resumed broker %s",
            group_id,
            admin_broker_id,
            broker_id,
        )
        backlog_drained = False

    return {"paused": False, "backlog_drained": backlog_drained}

# ── End round-robin allocation controls ─────────────────────────────────


def archive_broker(broker_id: UUID):
    # PERMANENT, one-way. Only allowed while the broker is currently sitting
    # in the Recycle Bin — you can't archive someone straight out of a real
    # working group, they must be deactivated (moved to the Recycle Bin)
    # first. This matches the UI flow: Archive only ever appears as an
    # action on the Recycle Bin screen.
    broker = _safe_broker_or_404(str(broker_id))
    if broker["is_archived"]:
        raise HTTPException(409, "This broker is already archived")
    if not group_repo.is_recycle_bin(broker["group_id"]):
        raise HTTPException(400, "Move this broker to the Recycle Bin before archiving")

    result = repo.archive(str(broker_id))

    if not result.data:
        raise HTTPException(status_code=404, detail="Broker not found")

    return _safe_broker_or_404(str(broker_id))


# ── My Day dashboard ────────────────────────────────────────────────────
# The broker's landing screen. Read-only, assembled from data that already
# exists — deliberately NOT everything the original mock-up drew.
#
# The mock had a "premium written this month" hero card and overdue/due-today
# follow-up tiles. Neither is returned here, because neither is recorded
# anywhere: there is no premium column in the schema (0045/0050 both say so
# explicitly, deferring it to a per-policy table that does not exist yet) and
# nothing schedules a callback. The frontend renders those blocks as inert
# "not set up yet" notes rather than tiles, so this response has no field to
# feed them. A tile reading 0 would be read as "you have no overdue
# follow-ups" when the truth is "nothing tracks them" — the one number worse
# than no number.

# A lead nobody has moved in this many days is "stale". Five working-ish days
# is the point at which a quote goes cold in this business; it is a display
# threshold, nothing enforces it.
_STALE_AFTER_DAYS = 5


def get_my_day(broker_id: str, month: str | None = None) -> dict:
    """Everything the My Day screen renders, in one request.

    One round-trip per figure and no caching: this is three cheap reads on
    indexed columns, and the screen is polled rarely (it is a landing page,
    not the live inbox). Correctness over cleverness until it measures slow.

    `month` ('2026-10') picks which month the premium and commission cards
    describe; everything else on the screen is "right now" whatever it says.
    Defaults to the current Ontario month. A future month is refused (422):
    there is nothing issued in it, and a picker that offered it would be wrong.
    """
    broker = _safe_broker_or_404(broker_id)

    now_ontario = datetime.now(_ONTARIO_TZ)

    # "Today" is an ONTARIO calendar day, not a UTC one. At 9pm Toronto it is
    # already tomorrow in UTC, so a UTC boundary would empty this count every
    # evening — precisely when a broker is reviewing the day they just had.
    # Same zone the round-robin's own day is anchored to (see above).
    start_of_today = datetime.combine(
        now_ontario.date(), datetime.min.time(), tzinfo=_ONTARIO_TZ
    )

    counts = lead_repo.broker_stage_counts(broker_id).data or {}
    new_today = lead_repo.count_assigned_since(broker_id, start_of_today.isoformat()).count or 0

    # THE CHOSEN MONTH, as calendar dates. issued_at is the DATE the broker
    # picked when marking the policy Issued, so the month is [1st, next 1st)
    # with no timezone involved.
    today = now_ontario.date()
    if month is None:
        year, mon = today.year, today.month
    else:
        year, mon = parse_month(month)
        if (year, mon) > (today.year, today.month):
            raise HTTPException(status_code=422, detail="That month has not happened yet")
    month_start = date(year, mon, 1)
    month_end = date(year + (mon == 12), 1 if mon == 12 else mon + 1, 1)

    # Premium the carrier actually WROTE in that month.
    rows = premium_repo.issued_between_for_broker(
        broker_id, month_start.isoformat(), month_end.isoformat()
    ).data or []
    premium_written = sum(
        Decimal(str(row["total_new_premium_annual"] or 0)) for row in rows
    )

    # The broker's own commission on those same policies: each policy's
    # commission at the two rates, then the broker's share (0134). Same split
    # for every broker, read from the one settings row.
    # If the rates cannot be read (or 0134 has not run), the card shows "—"
    # rather than a confident $0 — and the rest of My Day still loads.
    try:
        commission_earned = str(
            commission_service.broker_commission(rows, commission_service.get_rates())
        )
    except (HTTPException, ValueError):
        logger.exception("Commission earned could not be worked out for broker %s", broker_id)
        commission_earned = None

    return {
        "broker": {"name": broker["name"]},
        # Annual premium and how many policies produced it. A STRING, like
        # every other money value crossing this boundary: Decimal survives the
        # trip, float would not.
        "premium": {
            # Which month these figures describe, so the screen can label them.
            "month": f"{year:04d}-{mon:02d}",
            # Named for the month it describes, which is now the CHOSEN one.
            # Kept, not renamed: the phone My Day reads it too.
            "written_this_month": str(premium_written),
            "policy_count": len(rows),
            "commission_earned": commission_earned,
        },
        # Passed through as the DB returns it: one jsonb object keyed by the
        # real stage names, every stage always present. The frontend maps it
        # against LEAD_STAGES, so a stage added in SQL appears on this screen
        # without a change here.
        "counts": counts,
        "attention": {
            "new_today": new_today,
            "stale": _count_stale(broker_id, now_ontario),
        },
    }


def _count_stale(broker_id: str, now_ontario: datetime) -> int:
    """How many of this broker's leads have had no stage movement in
    _STALE_AFTER_DAYS.

    The repository hands back every history row for the broker's current
    leads, newest first; the first row seen for a lead is therefore its most
    recent activity, and later rows for that lead are its older history and
    are skipped. Reduced here rather than in SQL because "newest per group"
    is not something PostgREST can express — and doing it in Python keeps the
    repository free of business rules like the cutoff.

    A lead with NO history rows at all cannot be stale by this measure and is
    correctly absent: it has never moved, but it also arrived at 'New' with an
    arrival row written at the same moment, so in practice every lead has one.
    """
    cutoff = now_ontario - timedelta(days=_STALE_AFTER_DAYS)

    newest_seen: set[str] = set()
    stale = 0
    for row in lead_repo.stage_activity_for_broker(broker_id).data or []:
        lead_id = row["lead_id"]
        if lead_id in newest_seen:
            continue  # an older row for a lead already accounted for
        newest_seen.add(lead_id)
        if datetime.fromisoformat(row["changed_at"]) < cutoff:
            stale += 1

    return stale


def get_my_profile(broker_id: str) -> dict:
    """The signed-in broker's own profile.

    The caller's id comes from their session, never a parameter — see the
    route. Shaping happens in schemas.broker.MyProfileResponse, which is a
    deny-by-default field list, so a column added to the broker table cannot
    start appearing here on its own.
    """
    rows = repo.get_safe_with_group(broker_id).data
    if not rows:
        # A live session whose broker row is gone. Not an ordinary state —
        # the token was issued against a row that existed.
        raise HTTPException(status_code=404, detail="Broker not found")

    row = rows[0]
    # PostgREST returns the embed under the TABLE name; the response calls it
    # `group`, which is what it is from the broker's side.
    row["group"] = row.pop("broker_group", None)

    # Imported here, not at module scope: services/broker_document.py imports
    # THIS module for the archive guard, so a top-level import would be a
    # cycle. One function needs it; a local import is cheaper than splitting
    # the guard out into a third module to break a loop of two.
    from services import broker_document

    row["photo_url"] = broker_document.photo_url(broker_id)

    return row


def update_my_profile(broker_id: str, phone: str) -> dict:
    """Change the broker's own contact number, and nothing else.

    The request model accepts one field, so there is no path here to writing
    a name, a licence number or a cap — a broker cannot raise their own daily
    cap by patching their profile.
    """
    result = repo.update(broker_id, {"phone": phone})
    if not result.data:
        raise HTTPException(status_code=404, detail="Broker not found")
    return get_my_profile(broker_id)
