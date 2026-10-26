from uuid import UUID

from fastapi import APIRouter, Depends, Query

from auth_dependencies import get_current_broker, require_admin
from schemas.broker import (
    AvailabilityResponse,
    BlockBrokerRequest,
    CreateBrokerRequest,
    MoveBrokerRequest,
    MyProfileResponse,
    ScheduleUnpauseRequest,
    SetDailyCapRequest,
    SetMaxCapRequest,
    SetReadyForMoreRequest,
    SetSelfPausedRequest,
    UpdateBrokerRequest,
    UpdateMyProfileRequest,
)
from services import broker as service
from services import lead as lead_service

router = APIRouter(tags=["broker"], dependencies=[Depends(require_admin)])

# Its own router, gated by get_current_broker (any authenticated broker) NOT
# require_admin — a broker reading their OWN dashboard is not an admin action.
# Mounted separately in main.py so the router-level require_admin above never
# applies to it. Same split as routes/lead.py::broker_router.
broker_router = APIRouter(tags=["broker"])


@broker_router.get("/my-day")
def get_my_day(
    month: str | None = Query(None, pattern=r"^\d{4}-\d{2}$"),
    broker: dict = Depends(get_current_broker),
):
    """The signed-in broker's own dashboard: their name, their per-stage lead
    counts (the same 7 stages as the Leads rail), two attention figures —
    leads received today and leads with no stage movement in 5 days — and the
    premium written and commission earned on their policies issued in a month.

    Takes no broker_id: it always describes the CALLER, so there is no id to
    pass and no way to ask for someone else's day.

    `month` ('2026-10') picks the month the premium and commission cards show;
    omitted, it is the current one. See services/broker.py::get_my_day."""
    return service.get_my_day(broker["id"], month)


@broker_router.get("/my-profile", response_model=MyProfileResponse)
def get_my_profile(broker: dict = Depends(get_current_broker)):
    """What the system knows about the signed-in broker.

    Takes no broker_id, exactly like /my-day above: it always describes the
    CALLER. Accepting an id would turn this into a directory of every
    broker's licence number, address and date of birth, readable by anyone
    with a login.
    """
    return service.get_my_profile(broker["id"])


@broker_router.patch("/my-profile", response_model=MyProfileResponse)
def update_my_profile(
    request: UpdateMyProfileRequest,
    broker: dict = Depends(get_current_broker),
):
    """Change the broker's own contact number.

    The request model carries one field, so this cannot write a name, a
    licence number or a cap however the body is shaped.
    """
    return service.update_my_profile(broker["id"], request.phone)


@broker_router.patch("/my-ready-for-more")
def set_my_ready_for_more(
    request: SetReadyForMoreRequest,
    broker: dict = Depends(get_current_broker),
):
    """The broker opting THEMSELVES in for more leads today (design §1, §8).

    Once every eligible broker in their group has had their daily share, the
    Group-2 gate opens and only those who opted in receive further leads, up
    to their max_cap. Cleared every morning by the reset, so it is a choice
    about today rather than a setting left on.

    ITS OWN ROUTE, not a relaxed require_admin on
    PATCH /brokers/{id}/ready-for-more. That route names a broker, which is
    exactly the capability a broker must not have: an admin opting someone
    else in is a different act from a broker opting themselves in. Here the id
    comes from the token and there is no field to carry another one — the same
    reasoning as /my-profile directly above.

    Switching it ON offers the leads ALREADY WAITING in the group, not just
    the next to arrive, so a broker who says "I'll take more" gets the work
    that was sitting there. See services/broker.py::set_ready_for_more.
    """
    return service.set_ready_for_more(broker["id"], request)


@broker_router.patch("/my-availability", response_model=AvailabilityResponse)
def set_my_availability(
    request: SetSelfPausedRequest,
    broker: dict = Depends(get_current_broker),
):
    """The broker stopping their OWN leads, and starting them again.

    BOTH DIRECTIONS ARE IMMEDIATE. The admin route that already existed
    (PATCH /brokers/{id}/block) needs somebody else to act, records a
    disciplinary-shaped reason, and — by design — only unblocks the NEXT
    MORNING. None of that fits a broker who is ill today and back tomorrow, and
    while they wait the leads keep arriving and sit unworked.

    ITS OWN ROUTE, taking no broker id, for the same reason as
    /my-ready-for-more directly above: the id comes from the token and the
    request model has no field to carry another one. A broker pausing a
    colleague is not a capability this should have; an admin doing it
    deliberately is a different act on a different route.

    Switching back ON offers the leads ALREADY WAITING in the group, through
    the normal rotation. The response says whether that succeeded —
    backlog_drained is false when the pause was saved but the waiting leads
    could not be handed out, which is information rather than an error. See
    services/broker.py::set_my_self_paused for why that must never be a 5xx.
    """
    return service.set_my_self_paused(broker["id"], request.paused)


@router.get("/broker-groups/{group_id}/brokers")
def list_brokers(group_id: UUID):
    """List all brokers in a group (password hash never included)."""
    return service.list_brokers(group_id)


@router.post("/broker-groups/{group_id}/brokers")
def create_broker(group_id: UUID, request: CreateBrokerRequest):
    """Onboard a broker into a group.

    NO PASSWORD IS ISSUED HERE. The broker exists but cannot log in until an
    admin calls generate-password below — the same action used later if they
    ever forget it, so there is one path rather than two doing the same work.
    """
    return service.create_broker(group_id, request)


@router.post("/brokers/{broker_id}/generate-password")
def generate_password(broker_id: UUID, admin: dict = Depends(require_admin)):
    """Issue a temporary password, returned ONCE as {temporary_password}.

    Used to activate a newly-onboarded broker AND to recover one who has
    forgotten their password or lost the phone holding their 2FA — all the
    same action. Also clears the failed-attempt lockout, so a broker who
    locked themselves out can use the new password immediately.

    admin comes from the session for the audit row; the password itself is
    never logged.
    """
    return service.generate_password(broker_id, admin["id"])


@router.patch("/brokers/{broker_id}")
def update_broker(broker_id: UUID, request: UpdateBrokerRequest):
    """Edit a broker's profile. Not for password, group, or block state —
    those have their own dedicated endpoints. 409 if the new email is taken."""
    return service.update_broker(broker_id, request)


@router.patch("/brokers/{broker_id}/move")
def move_broker(broker_id: UUID, request: MoveBrokerRequest):
    """Move a broker into a different group. 409 if the destination group is
    already at the 10-broker cap, 400 if the destination doesn't exist or is
    the broker's current group."""
    return service.move_broker(broker_id, request)


@router.patch("/brokers/{broker_id}/block")
def block_broker(broker_id: UUID, request: BlockBrokerRequest):
    """Admin block — separate from deactivate. Requires a reason. Does not
    change is_active or group membership."""
    return service.block_broker(broker_id, request)


@router.patch("/brokers/{broker_id}/schedule-unpause")
def schedule_unpause(broker_id: UUID, request: ScheduleUnpauseRequest, admin: dict = Depends(require_admin)):
    """Schedule a blocked broker's unpause for a future date (design §5).
    Deferred, not instant — the block stays in effect until the daily reset
    job promotes it once effective_at arrives, so leads resume the day AFTER.
    400 if the broker isn't currently blocked."""
    return service.schedule_unpause(broker_id, request, admin["id"])


@router.patch("/brokers/{broker_id}/daily-cap")
def set_daily_cap(broker_id: UUID, request: SetDailyCapRequest, admin: dict = Depends(require_admin)):
    """Schedule a daily_cap change for a future date (design §6). daily_cap
    is system-managed — this writes pending_daily_cap only; the change takes
    effect at the next daily reset, never mid-day."""
    return service.set_daily_cap(broker_id, request, admin["id"])


@router.patch("/brokers/{broker_id}/max-cap")
def set_max_cap(broker_id: UUID, request: SetMaxCapRequest, admin: dict = Depends(require_admin)):
    """Schedule a max_cap (Group-2 overtime ceiling) change for a future
    date — same deferred pattern as daily-cap (design §6)."""
    return service.set_max_cap(broker_id, request, admin["id"])


@router.patch("/brokers/{broker_id}/ready-for-more")
def set_ready_for_more(broker_id: UUID, request: SetReadyForMoreRequest):
    """Today's Group-2 overtime opt-in (design §8). Cleared automatically by
    tomorrow's reset, so it is chosen each day rather than left on.

    Switching it ON also offers the leads ALREADY waiting in the broker's
    group, not just the next one to arrive — see services/broker.py for why
    that matters."""
    return service.set_ready_for_more(broker_id, request)


@router.patch("/brokers/{broker_id}/resume-leads", response_model=AvailabilityResponse)
def resume_broker_leads(broker_id: UUID, admin: dict = Depends(require_admin)):
    """Switch a broker's OWN pause back off on their behalf. Resume only.

    NO REQUEST BODY, and no admin counterpart that STOPS a broker's leads: an
    admin doing that uses PATCH /brokers/{id}/block, which records a reason.
    Keeping the two apart is what lets "why did this broker get nothing" be
    answered from the data — see services/broker.py::resume_broker_leads.

    Exists because self_paused persists until a person clears it (0105). A
    broker who is away and unreachable cannot clear their own, and somebody
    has to be able to.

    Offers the leads already waiting in the group, through the normal
    rotation. backlog_drained is false when the resume was saved but those
    leads could not be handed out — information, not an error, exactly as on
    the broker's own /my-availability. 400 if the broker hasn't stopped their
    own leads; audit-logged, unlike /ready-for-more above.
    """
    return service.resume_broker_leads(broker_id, admin["id"])


@router.get("/brokers/{broker_id}/leads")
def get_broker_lead_history(broker_id: UUID):
    """Every lead this broker has ever taken, permanently — survives a lead
    later being reassigned away (read from the lead_assignment custody log,
    migrations/0026). Powers the Admin > Brokers drilldown."""
    return lead_service.get_broker_lead_history(broker_id)


@router.patch("/brokers/{broker_id}/archive")
def archive_broker(broker_id: UUID):
    """PERMANENT, one-way lock — only allowed while the broker is currently
    in the Recycle Bin group. Every field is kept forever for compliance;
    no further edits, moves, or status changes are allowed after this.
    400 if the broker isn't in the Recycle Bin, 409 if already archived."""
    return service.archive_broker(broker_id)
