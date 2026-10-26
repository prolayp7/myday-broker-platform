from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response

from auth_dependencies import get_current_broker, require_admin
from schemas.lead import (
    AdjustCallCountRequest,
    CallCountResponse,
    CreateLeadRequest,
    CreateManualLeadRequest,
    ReassignLeadRequest,
    SetPolicyTypeRequest,
    SetQualificationRequest,
    UpdateStageRequest,
)
from services import lead as service

router = APIRouter(tags=["lead"], dependencies=[Depends(require_admin)])

# Its own router, gated by get_current_broker (any authenticated broker) NOT
# require_admin — a broker moving their OWN lead's stage is not an admin
# action. Mounted separately so the router-level require_admin above never
# applies to this one route. See services/lead.py::update_stage for the
# ownership guard (only the CURRENT holder may change it).
broker_router = APIRouter(tags=["lead"])


@router.get("/leads")
def list_leads(
    community: str | None = None,
    group_id: UUID | None = None,
    broker_id: UUID | None = None,
    stage: str | None = None,
    search: str | None = None,
    unassigned: bool = False,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
):
    """One page of admin leads matching the filters, newest first, plus the
    exact filtered total and the 4 KPI counts — everything the Admin › Leads
    screen needs in a single response: {rows, total, counts}. All filters are
    optional and applied server-side. community is a name ('Dragon')."""
    return service.list_leads(
        community=community,
        group_id=str(group_id) if group_id else None,
        broker_id=str(broker_id) if broker_id else None,
        stage=stage,
        search=search,
        unassigned=unassigned,
        page=page,
        limit=limit,
    )


@router.post("/leads")
def create_lead(request: CreateLeadRequest):
    """Create a lead and immediately run round-robin assignment on it (see
    docs/round-robin-allocation-design.md). group_id must already be resolved
    by the caller (via lead_form lookup or website_lead_rotation) — this
    endpoint does not resolve routing itself. 400 if group_id/ad_id doesn't
    exist. If no broker is currently eligible, the lead is created unassigned
    and picked up by the next reset drain — never an error."""
    return service.create_lead(request)


@router.patch("/leads/{lead_id}/reassign")
def reassign_lead(lead_id: UUID, request: ReassignLeadRequest, admin: dict = Depends(require_admin)):
    """Admin-only move of an already-assigned lead to a different broker.
    NOT a broker claim (there is no such thing) — see design §9. Invisible to
    both brokers' daily caps by design (§3)."""
    return service.reassign_lead(lead_id, request, admin["id"])


@broker_router.get("/leads/mine")
def list_my_leads(
    stage: str | None = None,
    search: str | None = None,
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    broker: dict = Depends(get_current_broker),
):
    """A broker's OWN leads — the real data behind the broker-facing Leads
    screen. Returns {rows, total, counts}: a page of the broker's leads
    (optionally filtered by stage/search), the filtered total for paging, and
    per-stage counts across their whole book for the left rail. Scoped to the
    authenticated broker — never a caller-supplied id. Its own path (not the
    admin GET /leads, differently shaped and gated) so the two never collide."""
    return service.list_my_leads(
        broker["id"], stage=stage, search=search, page=page, limit=limit
    )


@broker_router.get("/leads/{lead_id}")
def get_my_lead(lead_id: UUID, broker: dict = Depends(get_current_broker)):
    """One of the broker's OWN leads, by id — what a deep link needs.

    /leads/mine is paginated, so a link naming a lead outside the current page
    had nothing to fetch it with. Scoped to the authenticated broker; someone
    else's lead 404s exactly as a non-existent one does.

    DECLARED AFTER /leads/mine, and that matters: FastAPI matches routes in
    order, so a path parameter registered first would swallow "mine" and try to
    parse it as a UUID.
    """
    return service.get_my_lead(lead_id, broker["id"])


@broker_router.patch("/leads/{lead_id}/stage")
def update_stage(lead_id: UUID, request: UpdateStageRequest, broker: dict = Depends(get_current_broker)):
    """The broker moving their OWN lead through the pipeline. Free-form — any
    stage to any other stage, always allowed, no terminal lock (design
    decision 2026-07-16). Only the broker who CURRENTLY holds this lead may
    change its stage — enforced in the DB function itself (services/lead.py),
    not just here. Also appends this broker's own personal progress row to
    lead_stage_history (migrations/0029/0032) — a parallel, per-broker record
    distinct from lead.stage."""
    return service.update_stage(lead_id, request, broker["id"])


@broker_router.patch(
    "/leads/{lead_id}/call-count", response_model=CallCountResponse
)
def adjust_call_count(
    lead_id: UUID,
    request: AdjustCallCountRequest,
    broker: dict = Depends(get_current_broker),
):
    """One press of a call counter on the broker's own lead.

    Answers "who have I not rung yet?" on the lead list — the counters are a
    scanning aid, deliberately not call history (contact_log is that). Only
    the broker who currently holds the lead may move them.

    Returns BOTH counters, so the screen replaces its optimistic guess with
    the authoritative pair rather than trusting its own arithmetic.
    """
    return service.adjust_call_count(
        lead_id, broker["id"], request.outcome, request.delta
    )


@broker_router.get("/leads/{lead_id}/qualification-readiness")
def qualification_readiness(lead_id: UUID, broker: dict = Depends(get_current_broker)):
    """What the server will do about this lead's qualification, before it is asked.

    A READ of the same rules update_stage enforces, so the screen can act on them
    BEFORE putting a question to the broker — rather than asking first and then
    refusing, or worse, accepting an answer it is about to overwrite.

    Two things, and the second is why this route grew past its old name:

      missing           the applicant fields still empty. Quote Done and later
                        need all of them; In Progress needs none.

      insurance         ALWAYS 'ok' now, and min_insured_years always 0.

    THE INSURANCE VERDICT IS VESTIGIAL. It used to report what the four-year
    rule had already decided from driver.insured_since, so the dialog could
    avoid asking a question update_stage was about to overwrite. That rule is
    gone: insured_since has no say in qualification or conversions, so
    there is nothing to pre-empt. The keys stay for one release so an older
    bundle branching on them reads the permissive case instead of crashing on a
    missing key.

    The server still owns the applicant rule. A copy of it in the frontend would
    drift the first time it changed, which is what the old applicant-only
    version of this route existed to prevent.
    """
    return service.qualification_readiness(lead_id, broker["id"])


@broker_router.get(
    "/leads/{lead_id}/contact.vcf",
    # Declared, because FastAPI otherwise advertises application/json for every
    # route — and a generated client would then try to JSON-parse a vCard and
    # fail on a route that works perfectly. Same reason as the .ics route in
    # routes/reminder.py.
    response_class=Response,
    responses={200: {"content": {"text/vcard": {}}, "description": "vCard file"}},
)
def contact_vcard(lead_id: UUID, broker: dict = Depends(get_current_broker)):
    """The Create New Contact button on the mobile lead screen.

    Returns one contact card the broker's phone imports through its own Add
    Contact sheet, so the next call from that number arrives with a name on it
    instead of bare digits.

    FETCHED WITH THE AUTH HEADER, never opened as a plain link. This route is
    behind get_current_broker like every other broker read, and a browser
    navigating to a URL sends no Authorization header — which is exactly why
    the .ics button in ContactHistoryPage currently 401s. The frontend fetches
    this as a blob through the authenticated client instead; see
    saveLeadContact in data/api.ts.
    """
    body, filename = service.contact_vcard(lead_id, broker["id"])
    return Response(
        content=body,
        media_type="text/vcard",
        # attachment is what makes the browser hand the file to the OS rather
        # than render it as text — which is what opens the Add Contact sheet.
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@broker_router.patch("/leads/{lead_id}/qualification")
def set_qualification(
    lead_id: UUID,
    request: SetQualificationRequest,
    broker: dict = Depends(get_current_broker),
):
    """Is this lead a real prospect? The broker's own judgement.

    Asked at Quote Done and REQUIRED before the lead can move there or beyond
    (services/lead.py::update_stage refuses with a 422 until it is answered) —
    because CompleteRegistration, the qualified-lead signal, fires at that
    exact moment and an answer arriving later would be too late to decide
    anything.

    An unqualified lead sends NOTHING to Meta — no CompleteRegistration
    (migrations/0078, carried into 0100). Meta leads only.

    No 'skip' here. Skipping is the broker closing the dialog, which calls
    nothing at all and leaves the answer null, so the question returns on
    their next move.
    """
    return service.set_qualification(lead_id, request, broker["id"])


@broker_router.patch("/leads/{lead_id}/policy-type")
def set_policy_type(
    lead_id: UUID,
    request: SetPolicyTypeRequest,
    broker: dict = Depends(get_current_broker),
):
    """Whether this quote bundles a property, and which kind.

    'Standalone' is auto only. Anything else creates the applicant's own home —
    seeded with their address — and opens the property side of the quote.

    409 when the change would destroy properties: switching to Standalone, or
    to Tenants while rentals exist. The message names how many would be lost;
    resend with confirm=true to proceed.

    No ownership guard, matching the driver and property routes: brokers cover
    for each other, and every caller is already an authenticated broker of this
    brokerage.
    """
    return service.set_policy_type(lead_id, request)


@broker_router.post("/leads/manual", status_code=201)
def create_manual_lead(
    request: CreateManualLeadRequest,
    broker: dict = Depends(get_current_broker),
):
    """Create a lead the broker took themselves — a referral, a walk-in, a call
    to the office.

    Assigned to the CALLER, in the caller's own group. Neither is accepted from
    the request: the broker on the phone is the answer to "whose lead is this",
    and taking a group_id here would let one broker file a customer into
    another's book.

    Does NOT run round-robin, and so never counts toward anyone's daily cap —
    that cap exists to share out paid ad leads, not calls a broker handled
    personally.

    Name is required, plus a phone number or an email address. Whichever is not
    given is stored as "(not provided)", which the lead list hides.
    """
    return service.create_manual_lead(request, broker)
