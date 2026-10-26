"""Business logic for leads. Owns every HTTPException decision.

Leads are assigned exclusively by the system (round-robin) — see
docs/round-robin-allocation-design.md §9. There is no broker "claim" path;
the only lead-assigned-broker mutation available here is admin reassignment
(reassign_lead), which is deliberately invisible to both brokers' daily caps.
"""

import logging
import re
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo
from concurrent.futures import ThreadPoolExecutor
from uuid import UUID

from fastapi import HTTPException

import vcard
from db_errors import db_error
from repositories import lead as repo
from repositories import broker as broker_repo
from repositories import broker_group as broker_group_repo
from repositories import driver as driver_repo
from repositories import premium_history as premium_repo
from repositories import property as property_repo
from repositories import vehicle as vehicle_repo
from schemas.lead import (
    PRODUCT_LABEL,
    PRODUCTS_FOR,
    CreateLeadRequest,
    CreateManualLeadRequest,
    ReassignLeadRequest,
    SetPolicyTypeRequest,
    SetQualificationRequest,
    UpdateStageRequest,
)
from services import driver as driver_service
from services import property as property_service

logger = logging.getLogger("myday.lead")

# The brokerage's own clock, for dating an issued policy. Railway runs UTC, so
# the server's date rolls over at 8pm Ontario — a policy issued at 9pm on the
# 31st would be dated the 1st and counted in the following month.
_ONTARIO = ZoneInfo("America/Toronto")

# Fires a request's two independent reads (page of rows + counts) CONCURRENTLY
# instead of back-to-back — measured 165ms -> 87ms per screen load, since each
# read is ~85ms of Supabase round-trip and neither depends on the other.
# Module-level so threads are reused across requests. The shared supabase
# client is httpx-based and thread-safe for this two-at-a-time use.
_read_pool = ThreadPoolExecutor(max_workers=8, thread_name_prefix="lead-reads")


def list_leads(
    *,
    community: str | None = None,
    group_id: str | None = None,
    broker_id: str | None = None,
    stage: str | None = None,
    search: str | None = None,
    unassigned: bool = False,
    page: int = 1,
    limit: int = 50,
):
    """One combined payload for the Admin › Leads screen: a page of rows, the
    exact filtered total (for the pager), and the 4 KPI counts — all the screen
    needs in a single call. Every filter is applied server-side, so the response
    is one page no matter how many leads exist.

    community is a NAME ('Dragon'); it's resolved to its group ids and combined
    with any explicit group_id. If either resolves to an empty set (a filter was
    given but matched nothing), we short-circuit to an empty page rather than
    querying — and, importantly, without collapsing 'no such community' into
    'no filter' (which would wrongly return ALL leads)."""
    # Resolve the group-id filter set from community + group_id. None means "no
    # group filter"; a list (possibly empty) means "restrict to these".
    group_ids: list[str] | None = None
    if community is not None:
        rows = repo.group_ids_for_community(community).data
        group_ids = [r["id"] for r in rows if r.get("community")]
    if group_id is not None:
        if group_ids is None:
            group_ids = [group_id]
        else:
            # Both community and a specific group given: intersect.
            group_ids = [g for g in group_ids if g == group_id]

    # A filter was applied but matched no groups -> empty page, zero counts.
    # (Do NOT fall through to an unfiltered query.)
    if group_ids is not None and len(group_ids) == 0:
        return {"rows": [], "total": 0, "counts": _empty_counts()}

    offset = (max(1, page) - 1) * limit

    def rows_query(off: int):
        return repo.list_leads(
            group_ids=group_ids,
            broker_id=broker_id,
            stage=stage,
            search=search,
            unassigned=unassigned,
            offset=off,
            limit=limit,
        )

    # Rows and counts are independent reads — run them concurrently (see
    # _read_pool). .result() re-raises either query's failure unchanged.
    rows_future = _read_pool.submit(rows_query, offset)
    counts_future = _read_pool.submit(
        lambda: repo.lead_list_counts(
            community=community,
            group_id=group_id,
            broker_id=broker_id,
            stage=stage,
            search=search,
        )
    )
    result = _rows_or_first_page(rows_future, rows_query)
    counts_rows = counts_future.result().data
    c = counts_rows[0] if counts_rows else {}

    return {
        "rows": result.data,
        "total": result.count or 0,
        "counts": {
            "all": c.get("all_count", 0),
            "unassigned": c.get("unassigned_count", 0),
            "assigned": c.get("assigned_count", 0),
            "today": c.get("today_count", 0),
        },
    }


def _empty_counts():
    return {"all": 0, "unassigned": 0, "assigned": 0, "today": 0}


def _rows_or_first_page(rows_future, rows_query):
    """Resolve the rows future; if the requested page is beyond the data
    (PostgREST PGRST103 'range not satisfiable' — a stale pager or an edited
    URL), gracefully retry at page 1 instead of surfacing a 500. Any other
    failure re-raises unchanged."""
    try:
        return rows_future.result()
    except Exception as err:
        if "PGRST103" not in str(err):
            raise
        return rows_query(0)


def list_my_leads(
    broker_id: UUID,
    *,
    stage: str | None = None,
    search: str | None = None,
    page: int = 1,
    limit: int = 50,
):
    """A broker's OWN leads — the real data behind the Leads screen (replaces
    the frontend's mock). Returns {rows, total, counts} scoped to this one
    broker: a page of rows (optionally narrowed by stage/search), the exact
    filtered total (for paging), and per-stage counts across the broker's
    WHOLE book (for the left rail — these ignore the stage filter so the rail
    always shows the full breakdown, but do honor search).

    Server-side stage/search filtering + pagination means nothing is ever
    silently hidden: the broker reaches any of their leads by filtering,
    searching, or paging — however large their book grows. (Replaces the
    earlier limit=1000 version, which silently truncated past 1000 leads.)"""
    bid = str(broker_id)
    offset = (max(1, page) - 1) * limit

    # WHAT THE RAIL CALLS A STAGE IS NOT ALWAYS lead.stage. 'In Progress' and
    # 'Junk' are labels inside New and Lost (0092) that the rail shows as
    # chips of their own, because that is how a broker thinks about them. The
    # translation lives HERE rather than in list_leads, which the admin screen
    # also uses and which should keep speaking in real stages only.
    #
    # New and Lost mean UNTOUCHED here — a lead carrying a label belongs to
    # its own chip and to exactly one chip, so the rail's numbers still sum to
    # the total. broker_stage_counts (0093) counts them the same way.
    stage_filter, sub_filter, sub_is_null = stage, None, False
    if stage in _CHIP_SUB_STAGE:
        stage_filter, sub_filter = _CHIP_SUB_STAGE[stage], stage
    elif stage in _SUB_STAGES_FOR:
        sub_is_null = True

    def rows_query(off: int):
        return repo.list_leads(
            broker_id=bid,
            stage=stage_filter,
            sub_stage=sub_filter,
            sub_stage_is_null=sub_is_null,
            search=search,
            # Broker's own view sorts by when it landed in THEIR inbox, not
            # the customer's submission time — a lead reassigned to them today
            # is newest to them (see repo.list_leads order_by note).
            order_by="assigned_at",
            # Leaner select: no broker/group embeds (always their own), but
            # WITH extra_data for the detail panel's Meta answers.
            select_columns=repo.MY_LEAD_SELECT,
            offset=off,
            limit=limit,
        )

    # Rows and rail-counts are independent reads — run them concurrently
    # (see _read_pool), same pattern as the admin list_leads above.
    rows_future = _read_pool.submit(rows_query, offset)
    counts_future = _read_pool.submit(lambda: repo.broker_stage_counts(bid, search=search))
    result = _rows_or_first_page(rows_future, rows_query)
    counts = counts_future.result().data or {}

    return {
        "rows": result.data,
        "total": result.count or 0,
        "counts": counts,
    }


def create_lead(request: CreateLeadRequest):
    # A website lead made here would be routed by FACEBOOK rules — assign_lead
    # writes lead_delivery, so it would use the broker's Facebook cap and be
    # charged the lead cost. Website leads have their own route (0127: the
    # webhook stores them, the website drain assigns them), and this is the
    # one other door they could come through.
    if request.source == "website":
        raise HTTPException(
            status_code=422,
            detail="Website leads arrive through the website form, not this endpoint",
        )

    values = request.model_dump(mode="json")
    values["group_id"] = str(request.group_id)
    values["ad_id"] = str(request.ad_id) if request.ad_id else None

    try:
        result = repo.insert(values)
    except Exception as err:
        raise db_error(
            err,
            on_foreign_key=HTTPException(400, "That broker group or ad doesn't exist"),
        )

    lead_id = result.data[0]["id"]
    assign_lead(UUID(lead_id))

    # Re-fetch so the response reflects the assignment that just happened
    # (assigned_broker_id/assigned_at/assigned_by) rather than the
    # pre-assignment snapshot from the insert.
    return repo.get_by_id(lead_id).data


def assign_lead(lead_id: UUID) -> None:
    """Runs the round-robin assignment function (migrations/0019) for one
    lead. Never raises for "no eligible broker" — that's an ordinary,
    expected outcome (the lead is left unassigned and logged by the function
    itself), not an error. Only a genuine DB/RPC failure is logged here, and
    even then this must not block lead creation from succeeding — the lead
    row already exists and will be picked up by the next reset drain."""
    try:
        repo.call_assign_lead(str(lead_id))
    except Exception:
        logger.exception("assign_lead RPC failed for lead %s — will be retried by the next reset drain", lead_id)


def get_group_capacity(group_id: UUID):
    """The capacity read-model for one group (migrations/0021 group_capacity):
    leads_today, unassigned, and per-broker delivered-today. Read-only, never
    raises for an empty/unknown group — an unknown id simply yields zero
    counts and an empty broker array, which the UI renders as an empty group."""
    result = repo.group_capacity(str(group_id))
    # rpc returns the JSON blob as .data directly (a dict), not a row list.
    return result.data


def get_broker_lead_history(broker_id: UUID):
    """Every lead a broker has ever taken, permanently (migrations/0026) —
    survives that lead later being reassigned away. Read-only; an unknown
    broker id simply yields an empty list."""
    result = repo.broker_lead_history(str(broker_id))
    return result.data


def get_my_lead(lead_id: UUID, broker_id: UUID) -> dict:
    """One of the broker's OWN leads, by id.

    FOR DEEP LINKS. /leads/mine is paginated, so a link naming a lead on page
    four had no way to fetch it — the renewal WhatsApp's "Set Up Reminder"
    button would land the broker on an unfiltered list. Same row shape as that
    endpoint's, so the screen can drop it straight into its selection.

    404 FOR SOMEONE ELSE'S LEAD, identical to the answer for an id that does
    not exist. The scoping is in the query, so this cannot be used to learn
    whether a given id is real — only whether it is theirs.
    """
    rows = repo.get_one_for_broker(str(lead_id), str(broker_id)).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Lead not found")
    return rows[0]


def reassign_lead(lead_id: UUID, request: ReassignLeadRequest, admin_broker_id: UUID):
    """Admin assignment of a lead to a broker (single). All validation, the
    lead move, the custody-history row, and the audit log happen atomically
    inside admin_assign_lead (migrations/0025) — the only job here is to run it
    and turn its guard errors into a 400. The DB function is intentionally
    permissive about cross-group / cross-community moves (the frontend warns
    first); it only rejects the genuinely-invalid cases below."""
    try:
        repo.admin_assign(str(lead_id), str(request.broker_id), str(admin_broker_id))
    except Exception as err:
        # admin_assign_lead raises P0001 with a ready-to-show message for every
        # invalid case (lead/broker missing, archived, same-broker no-op).
        raise db_error(err, on_group_full=_plpgsql_message_error(err, "Could not reassign the lead"))

    # Return the freshly-updated lead so the caller sees the new holder.
    return repo.get_by_id(str(lead_id)).data


def update_stage(lead_id: UUID, request: UpdateStageRequest, broker_id: UUID):
    """The broker moving their OWN lead through the pipeline (free-form — any
    stage to any stage, no rules, design decision 2026-07-16). The ownership
    guard (only the CURRENT holder may change it) and the lead_stage_history
    write happen atomically inside update_lead_stage (migrations/0032) — this
    is only responsible for running it and turning its guard errors into a
    clean HTTP response.

    ONE EXCEPTION to free-form: Quote Done and everything past it require a
    premium. Quote Done fires Meta's CompleteRegistration, which carries the
    annual premium as its `value` — with no premium the event goes out empty,
    so Meta's optimisation cannot see revenue and the quote teaches it
    nothing. Refused rather than warned about, because the broker is the only
    one who can fix it and the moment they mark a quote done is when they
    still have the numbers to hand.
    """
    # THE ISSUE DATE IS THE BROKER'S TO GIVE, and Issued cannot be reached
    # without it. It used to be today's date, set automatically — which put a
    # policy the carrier wrote on the 30th into the following month whenever
    # the broker recorded it a couple of days later, with no way to correct
    # it. The Issued dialog asks, defaulting to today.
    if request.stage == "Issued":
        if request.issued_at is None:
            raise HTTPException(
                status_code=422,
                detail="Enter the date this policy was issued.",
            )
        # A policy cannot have been issued tomorrow. Worth refusing rather
        # than trusting: a year typed as 2027 parks the projection in a month
        # nobody will think to look at.
        if request.issued_at > datetime.now(_ONTARIO).date():
            raise HTTPException(
                status_code=422,
                detail="A policy cannot be issued in the future.",
            )

        # A second guard used to run at Issued, requiring a carrier that held
        # a commission rate for every product on the policy. It went with the
        # commission module (0083): the question it asked — "can we calculate
        # money on this?" — no longer has anything to ask it of. The premium
        # check above is Meta's requirement and stands on its own.

    # THE APPLICANT MUST BE COMPLETE. A quote-completeness rule first: these
    # are the details a broker needs before quoting anyone, whatever the lead's
    # source.
    #
    # They also happen to be what Meta matches on. NOT that attribution is
    # impossible without them — platform_leadgen_id is an exact match on its
    # own, and email and phone come off the lead. These raise match QUALITY,
    # which Meta scores around 5.5-6.5 on email alone against roughly 8 with
    # name, date of birth, gender and location present. This comment claimed
    # the stronger thing until 2026-08-07.
    if request.stage in _STAGES_NEEDING_APPLICANT:
        missing = _missing_applicant_fields(str(lead_id))
        if missing:
            # EVERYTHING missing, in one message. Reporting only the first
            # would send the broker back and forth to the same dialog once per
            # empty field.
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Complete the applicant before moving this lead to "
                    f"{request.stage} — missing: {', '.join(missing)}."
                ),
            )

    # THE PREMIUM COMES LAST OF THE THREE COMPLETENESS CHECKS, and the order
    # is deliberate rather than incidental.
    #
    # It moved below the applicant check when Quote Done joined
    # _STAGES_NEEDING_PREMIUM (0100). Above it, a broker with a half-filled
    # applicant was told "Choose the policy type" — a question about a
    # different screen than the one they had actually left incomplete. The
    # applicant is also the earlier piece of work, so asking for it first is
    # the order the broker fills things in.
    #
    # Still ahead of the stage WRITE, which is the part that matters for Meta:
    # the trigger fires off lead_stage_history, so a refused move leaves no
    # signal row at all.
    if request.stage in _STAGES_NEEDING_PREMIUM:
        lead_rows = repo.get_by_id(str(lead_id)).data or []
        policy_type = lead_rows[0].get("combined_policy_type") if lead_rows else None

        _validate_premiums_complete(str(lead_id), policy_type, request.stage)

    # THE SUB-STAGE MUST BELONG TO THE STAGE IT IS SENT WITH (0092). 'Junk'
    # says a Lost lead was never a real opportunity; 'In Progress' says a New
    # one has been started. Neither means anything anywhere else, and the
    # column's own CHECK constraint deliberately does not encode the pairing —
    # Postgres cannot without a trigger — so this is where it is enforced.
    #
    # Refused rather than quietly dropped: a client sending Junk with Sold has
    # a bug, and silently storing nothing would hide it until someone asked
    # why the label never appeared.
    if request.sub_stage and request.sub_stage not in _SUB_STAGES_FOR.get(request.stage, ()):
        raise HTTPException(
            status_code=422,
            detail=f"'{request.sub_stage}' is not a label a {request.stage} lead can carry.",
        )

    # Carries source and is_qualified for the qualification guard below, so
    # that check costs no extra round-trip.
    #
    # It used to also derive was_issued, to decide whether the issued date
    # needed clearing. That decision moved into SQL with 0085 — the function
    # reads the old stage itself, inside the same transaction, which is the
    # whole point: a value read here could be stale by the time it was acted
    # on.
    current = repo.get_by_id(str(lead_id)).data or []

    # THE QUALIFICATION GATE IS GONE, and the insurance rule inside it with it.
    #
    # Both were written when CompleteRegistration fired ON a stage move, so the
    # judgement had to exist by that moment or the conversion would report a lead
    # nobody had vouched for. NEITHER PREMISE SURVIVES: 0123 dropped the
    # stage-history trigger and 0124 made the broker's ANSWER fire the conversion,
    # so a stage move reports nothing and blocking one buys nothing. What it cost
    # was real — a broker could not record where a lead had got to until they had
    # answered a question about Meta.
    #
    # THE ANSWER IS STILL ASKED, in two places that do not block: the dialog opens
    # on a qualifying stage move (frontend NEEDS_QUALIFYING), and a lead left
    # unjudged for 24 hours turns red in the list (frontend domain/unjudged.ts).
    #
    # THE INSURANCE RULE GOES TOO. It refused the move outright when there was no
    # Continuous Insurance date, and silently wrote is_qualified = false when the
    # customer was insured under MIN_INSURED_YEARS. Measured before removing: 135
    # of 200 Meta leads have no date, so that refusal was the most common way this
    # guard fired — blocking a pipeline move over a field that now decides
    # nothing. insured_since is still parsed and stored; it has no say in
    # qualification or conversions.
    #
    # THE OTHER TWO GUARDS ARE UNTOUCHED, and they are the ones with business
    # reasons: _STAGES_NEEDING_APPLICANT still demands the nine applicant fields
    # for Quote Done and beyond (you cannot quote someone whose address you do not
    # have), and _STAGES_NEEDING_PREMIUM still demands a premium for the sale
    # stages, which is what lets Purchase carry its value.

    # THE DRAFTS BECOME ANNUAL, at the one moment the annual figure is read.
    #
    # READ-ONLY SINCE 0103. This used to convert the premiums itself — a x12
    # write-back, both units reset to 'year', and a careful argument about
    # what a retry after a failed stage write would multiply. None of it
    # exists now: the totals are generated columns that honour
    # current_premium_unit / new_premium_unit, so the annual figure is correct
    # the moment a premium is saved and cannot be made wrong by anything that
    # happens here.
    #
    # All that is left is the question. Asked after the guards above, so a
    # broker is told the real blocker before being asked about a low figure.
    if request.stage == "Quote Done":
        _confirm_plausible_auto_premium(str(lead_id), request.confirm)

    # THE STAGE AND THE ISSUED DATE, IN ONE TRANSACTION.
    #
    # Issued is when the carrier actually wrote the policy, and
    # premium_history.issued_at is what every issued-premium figure counts by
    # (repositories/premium_history.py::issued_since_for_broker). The date used
    # to be a second call made after this one had already committed, and it
    # swallowed its own failures — so a blip between them left a lead reading
    # 'Issued' that counted toward no month at all, with nothing reporting a
    # problem. Migration 0085 moved the write inside the function.
    #
    # The date is passed on EVERY call, not just the Issued one. The DB decides
    # what to do with it: set it when moving to Issued, clear it when moving
    # off Issued, ignore it otherwise. Sending it unconditionally is also what
    # keeps the PostgREST signature stable — see repositories/lead.py.
    #
    # Non-null on the Issued path: the guard at the top of this function
    # refuses Issued without a date.
    try:
        repo.update_stage(
            str(lead_id),
            str(broker_id),
            request.stage,
            request.issued_at.isoformat() if request.issued_at else None,
        )
    except Exception as err:
        # "You do not currently hold this lead" is an ownership violation —
        # surfaced as 403 (the request is well-formed, just not allowed for
        # this caller), not a generic 400.
        text = str(err)
        status = 403 if "do not currently hold" in text else 400
        raise db_error(
            err,
            on_group_full=_plpgsql_message_error(err, "Could not update the lead's stage", status=status),
        )

    # THE SUB-STAGE, WRITTEN AFTER THE MOVE AND ON EVERY MOVE (0092).
    #
    # AFTER, so it can never be set on a move the function above refused —
    # that call owns the ownership guard, the history row and the issued date,
    # and a label written first would survive a stage change that did not
    # happen.
    #
    # UNCONDITIONALLY, which is what clears it. A lead marked Junk and later
    # reopened as New must not still read Junk, and update_stage is
    # deliberately free-form — any stage to any stage — so there is no move
    # this rule can skip. Sending null when the request carries no sub_stage
    # is the clear.
    #
    # A separate statement rather than a parameter on update_lead_stage: that
    # function's signature is called through PostgREST by name, and widening
    # it would mean a new migration for a column no other caller needs. The
    # cost is that this is not in the same transaction — acceptable here,
    # because a failure leaves the STAGE correct and only the label stale,
    # which the next move fixes.
    try:
        repo.set_sub_stage(str(lead_id), request.sub_stage)
    except Exception:
        logger.exception("Stage moved but the sub-stage did not save on lead %s", lead_id)

    # Conversions are not queued here: update_lead_stage (0126) reconciles in
    # SQL when the stage is a sale, and qualification has its own RPC (0124).

    return repo.get_by_id(str(lead_id)).data


def qualification_readiness(lead_id: UUID, broker_id: UUID) -> dict:
    """What the applicant is still missing, for the screen to show before it
    asks the broker to judge the lead.

    THE INSURANCE VERDICT IS GONE. This used to report what the four-year rule
    had already decided — 'ok', 'too_recent' or 'missing' — so the dialog could
    avoid asking a question update_stage had settled on its own. That rule no
    longer exists: insured_since has no say in qualification or
    conversions, so there is nothing for it to pre-empt and nothing to report.
    The keys stay in the response for one release so an older bundle does not
    break on their absence.

    PURE READ, as before. The screen calling this must not have side effects.

    Ownership-checked like every other broker read: a lead someone else holds is
    not theirs to inspect.
    """
    rows = repo.get_by_id(str(lead_id)).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Lead not found")
    if str(rows[0].get("assigned_broker_id")) != str(broker_id):
        raise HTTPException(
            status_code=403, detail="You do not currently hold this lead"
        )

    return {
        "missing": _missing_applicant_fields(str(lead_id)),
        # ALWAYS 'ok' NOW. Kept so a frontend still branching on this reads the
        # permissive case rather than crashing on a missing key; both this and
        # min_insured_years come out when the screen stops sending them.
        "insurance": "ok",
        "min_insured_years": 0,
    }


# The two counters a broker can move, and the column each one lives in. The
# REQUEST carries the left-hand side (a validated Literal); only these two
# strings ever reach SQL as a column name.
_CALL_COUNT_COLUMNS = {
    "answered": "answered_count",
    "no_answer": "no_answer_count",
}


def adjust_call_count(lead_id, broker_id: str, outcome: str, delta: int) -> dict:
    """Move one of a lead's call counters by one (0121).

    OWNERSHIP-CHECKED like every other broker write on a lead: a lead someone
    else holds is not theirs to annotate. Same 404-then-403 order as the reads
    above, so "no such lead" and "not yours" stay distinguishable.

    The arithmetic and the zero-clamp both happen in SQL — see
    repositories/lead.py::adjust_call_count for why a read-then-write here
    would drop counts under a double press.
    """
    rows = repo.get_by_id(str(lead_id)).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Lead not found")
    if str(rows[0].get("assigned_broker_id")) != str(broker_id):
        raise HTTPException(
            status_code=403, detail="You do not currently hold this lead"
        )

    try:
        result = repo.adjust_call_count(
            str(lead_id), _CALL_COUNT_COLUMNS[outcome], delta
        )
    except Exception as err:  # noqa: BLE001 — mapped to HTTP by db_error
        raise db_error(err)

    # The RPC returns one row of both counters. Empty would mean the lead
    # vanished between the guard above and the update — vanishingly unlikely,
    # but a 404 beats an IndexError.
    data = result.data or []
    if not data:
        raise HTTPException(status_code=404, detail="Lead not found")

    return data[0]


def missing_applicant_fields(lead_id: UUID, broker_id: UUID) -> list[str]:
    """The applicant details still missing on this lead, for the screen to show
    before it asks the broker to judge it.

    Ownership-checked like every other broker read: a lead someone else holds
    is not theirs to inspect.
    """
    rows = repo.get_by_id(str(lead_id)).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Lead not found")
    if str(rows[0].get("assigned_broker_id")) != str(broker_id):
        raise HTTPException(
            status_code=403, detail="You do not currently hold this lead"
        )
    return _missing_applicant_fields(str(lead_id))


def contact_vcard(lead_id: UUID, broker_id: UUID) -> tuple[str, str]:
    """This lead as a vCard the broker's phone can save, plus a filename.

    THE POINT: a customer ringing back shows a bare number unless they are in
    the phone's own address book. This is the one-tap way to put them there —
    the broker taps Create New Contact, the phone opens its native Add Contact
    sheet, and the next call from that number arrives with a name on it.

    Ownership-checked like every other broker read (missing_applicant_fields
    above): a lead someone else holds is not theirs to download a customer's
    details from.

    THE NUMBER IS THE WHOLE POINT, so a lead without one is refused rather
    than served a card that can never match a call. phone_e164 is preferred —
    it is what build_vcard's comment says Android and iOS match on — but it is
    null for two ordinary reasons (the "(not provided)" placeholder, and any
    non-NANP number, because normalize_phone_e164 is NANP-only by design,
    migration 0058). So lead.phone is the fallback, and only when BOTH are
    unusable is this a 409.

    409 rather than 422: nothing about the request is malformed. The lead
    simply has no number yet, which is a state the broker can fix by filling
    one in — and the message says so.
    """
    rows = repo.get_by_id(str(lead_id)).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead = rows[0]
    if str(lead.get("assigned_broker_id")) != str(broker_id):
        raise HTTPException(
            status_code=403, detail="You do not currently hold this lead"
        )

    # E.164 first, the display number second. Both go through is_placeholder:
    # lead.phone is NOT NULL, so a lead captured without one carries the
    # stand-in string rather than a null.
    phone = lead.get("phone_e164")
    if vcard.is_placeholder(phone):
        phone = lead.get("phone")
    if vcard.is_placeholder(phone):
        raise HTTPException(
            status_code=409,
            detail="This lead has no phone number yet — add one before saving the contact",
        )

    name = (lead.get("name") or "").strip() or "Unknown"

    body = vcard.build_vcard(
        uid=vcard.vcard_uid_for_lead(str(lead_id)),
        name=name,
        phone_e164=phone,
        # Passed through as-is: build_vcard drops a placeholder itself rather
        # than writing "not provided" into the broker's Contacts as an address.
        email=lead.get("email"),
    )

    # ASCII, and never empty. The name reaches this from customer-entered form
    # data, so it can carry quotes, semicolons, slashes or non-Latin script —
    # all of which either break the Content-Disposition header's quoting or
    # produce a filename the OS refuses. The card itself carries the real name;
    # this only has to be a sane thing to land in a Downloads folder.
    safe = re.sub(r"[^A-Za-z0-9 _-]", "", name).strip() or "contact"
    return body, f"{safe}.vcf"


def set_qualification(lead_id: UUID, request: SetQualificationRequest, broker_id: UUID):
    """Record whether this lead is a real prospect.

    This is what unblocks the stage move refused by update_stage, and what
    decides whether Meta ever hears about this lead: an unqualified one sends
    NO CompleteRegistration (the gate in migrations/0078, carried into 0100).

    ONLY THE CURRENT HOLDER. Checked here rather than in a DB function,
    because unlike a stage change this writes one column with no history row
    and no atomicity to protect — the guard is the whole of the logic, and
    hiding it in PL/pgSQL would put it a migration chain away from the rule it
    enforces.

    ONLY META LEADS. Refused rather than silently ignored: nothing else stops
    a crafted request storing a judgement on a manual lead, and a stored
    answer that can never be sent anywhere is worse than no answer — it reads,
    later, as though someone had decided something.
    """
    rows = repo.get_by_id(str(lead_id)).data or []
    if not rows:
        raise HTTPException(status_code=404, detail="Lead not found")
    lead = rows[0]

    if str(lead.get("assigned_broker_id")) != str(broker_id):
        raise HTTPException(
            status_code=403, detail="You do not currently hold this lead"
        )

    if lead.get("source") != "meta":
        raise HTTPException(
            status_code=422,
            detail="Only Meta leads are qualified — this one came in another way",
        )

    # NO APPLICANT CHECK HERE, and its removal is the point of this endpoint's
    # current shape.
    #
    # It used to refuse while any of the nine applicant fields was empty, on the
    # reasoning that a broker cannot judge a lead they have not written down yet.
    # That was right while the judgement was asked on the way into Quote Done,
    # where the full applicant is required anyway.
    #
    # It is wrong now. The judgement is asked on the way into IN PROGRESS, which
    # exists precisely so a qualified lead reaches Meta days earlier — before the
    # form is finished. Keeping the guard meant the final Qualified tap failed for
    # exactly the leads the change was built for.
    #
    # NOTHING IS LOST. update_stage still enforces the complete applicant for
    # Quote Done and everything past it (_STAGES_NEEDING_APPLICANT), which is the
    # gate that governs what Meta is told and when. This endpoint records an
    # answer; that one decides whether the lead may move.
    values = request.model_dump(exclude_unset=True)

    repo.set_qualification(str(lead_id), values)
    return repo.get_by_id(str(lead_id)).data


# WHERE THE ISSUED DATE IS WRITTEN, now that it is not written here.
#
# _stamp_issued_at used to live at this spot: a second PostgREST call made
# AFTER the stage change had committed, which caught and logged its own
# failures. That gap is the bug migration 0085 closes — the date is now set and
# cleared inside update_lead_stage, in the same transaction as the stage.
#
# The reasoning it carried, kept because it is still what the column is for:
#
#   * premium_history.issued_at was once READ and never WRITTEN.
#     repositories/premium_history.py::issued_since_for_broker filters on it to
#     answer "what did this broker write this month" — with the column always
#     null that query returned NOTHING, permanently, and every issued-premium
#     figure was zero.
#   * THE DATE COMES FROM THE BROKER, not the clock. A policy the carrier wrote
#     on the 30th and the broker recorded on the 2nd used to land in the
#     following month. The Issued dialog asks, defaulting to today.
#   * No timezone is involved: issued_at is a DATE, and a date the broker chose
#     has no clock to convert. The future-date guard in update_stage is the one
#     place that still reads a clock.
#   * CLEARED ON THE WAY OUT. A lead moved off Issued was not written after
#     all, and a date left behind would keep counting a sale that did not
#     happen.


# Stages a lead cannot reach without a premium: Pending and the SALE stages.
#
# QUOTE DONE CAME OUT, and 0100's reason for putting it in is the reason it
# goes. That migration added it because Quote Done was the stage that fired
# CompleteRegistration, and that event carried the annual premium as its value —
# so refusing the move was what made a valueless conversion impossible.
#
# BOTH HALVES ARE NOW FALSE. The conversion fires on the broker's ANSWER rather
# than a stage move (0124), and CompleteRegistration carries no money at all:
# it says "a real prospect", which has no price. The event that carries the
# premium is Purchase, and Purchase fires on the three stages below.
#
# WHAT THAT GUARANTEE IS WORTH KEEPING FOR: reaching a sale stage still proves a
# premium exists, which is why ensure_conversion_signals can snapshot one into
# every Purchase row rather than sending a sale worth nothing.
#
# A SET, not `== "Sold"`: stages are free-form and a broker can jump straight to
# Issued without passing through Sold.
#
# PENDING JOINED ON 2026-10-05, for the business rather than for Meta: a lead
# is Pending because a quote is out with the customer, and a quote out with no
# figure on it is not a quote. Nothing is sent to Meta at Pending, so this adds
# no conversion — it only stops Pending meaning "quoted, amount unknown".
#
# Lost is deliberately absent: a lead that did not buy has no premium to
# enter, and demanding one would make it impossible to close out.
_STAGES_NEEDING_PREMIUM = frozenset({"Pending", "Sold", "Submitted", "Issued"})


# The applicant details a lead cannot be quoted without.
#
# NO LONGER AN ALIAS OF _STAGES_NEEDING_QUALIFICATION, and the split is
# load-bearing. They were the same frozenset object, so adding 'In Progress' to
# the qualification set would have added it here too — demanding all nine
# applicant fields before a broker could mark a lead In Progress, which is
# exactly the delay moving the signal earlier exists to remove.
#
# THE JUDGEMENT AND THE PAPERWORK ARE DIFFERENT QUESTIONS. A broker can tell
# whether a lead is real on the phone; filling in nine fields is the work that
# follows. Quote Done and later still need both — that is where a quote is
# produced and where Meta's match quality actually matters.
#
# Still a SET rather than `== "Quote Done"`: stages are free-form, so a broker
# can jump New -> Sold and must not walk past this.
_STAGES_NEEDING_APPLICANT = frozenset(
    {"Quote Done", "Pending", "Sold", "Submitted", "Issued"}
)


# Which labels belong to which stage (0092). A stage absent from this map
# takes no sub-stage at all, which is most of them.
#
# THE PAIRING LIVES HERE, not in the database. The column's CHECK constraint
# limits the vocabulary to these two words; it cannot say which stage each
# belongs to without a trigger, and a trigger for this is more machinery than
# the distinction earns. Keeping it in one dict means the rule is stated once
# and reads as a rule rather than as two ifs.
_SUB_STAGES_FOR: dict[str, tuple[str, ...]] = {
    # 'New': ('In Progress',) was here until 0115 promoted In Progress to a real
    # stage. It is gone rather than emptied: a stage with an empty tuple would
    # still appear in _CHIP_SUB_STAGE's iteration and in the "does this stage
    # take labels" check, claiming a vocabulary it no longer has.
    "Lost": ("Junk",),
}

# The reverse: a rail chip that is really a label -> the stage it lives in.
# 'In Progress' -> 'New', 'Junk' -> 'Lost'.
#
# DERIVED, never written out by hand. A label added to _SUB_STAGES_FOR becomes
# filterable in the same commit, and the two can never disagree about which
# stage owns which label — which they would if this were a second literal.
_CHIP_SUB_STAGE: dict[str, str] = {
    sub: stage for stage, subs in _SUB_STAGES_FOR.items() for sub in subs
}

# Column -> what to call it in the message. Every one is a field
# meta_capi.py::build_user_data hashes into em, ph, db, ge, ct/st/zp — which is
# why these and not the licence dates, which Meta never sees.
#
# They raise MATCH QUALITY; they are not what makes attribution possible.
# platform_leadgen_id is an exact match on its own. Meta scores email-only
# around 5.5-6.5 and roughly 8 with name, date of birth, gender and location.
#
# THE APPLICANT ONLY. build_user_data reads the PRINCIPAL driver alone, so a
# spouse or additional driver has nothing to contribute and stays optional —
# blocking a broker mid-call for a second driver's details would cost more
# than it buys.
_REQUIRED_APPLICANT_FIELDS = {
    # FIRST AND LAST, NOT THE COMBINED NAME. Meta hashes fn and ln as separate
    # parameters, so a lead that has only `name` cannot be matched on either —
    # and the applicant screen has no `name` box for the broker to fill, which
    # made "missing: name" an instruction they could not act on.
    #
    # MIDDLE IS NOT REQUIRED: Meta has no parameter for it, so blocking a lead
    # on a field nothing sends would cost the broker work for nothing.
    #
    # A single-word name ("Garima") yields a first name and no surname, so the
    # lead stays incomplete until the broker adds one. That is intended — a
    # conversion Meta cannot match on a name is the thing this exists to stop.
    "first_name": "first name",
    "last_name": "last name",
    "phone": "phone",
    "email": "email",
    "date_of_birth": "date of birth",
    "gender": "gender",
    # ALL FOUR ADDRESS PARTS, not just the street. The sender hashes city,
    # province and postal code into ct/st/zp — so a driver with a street and
    # nothing else passed this guard and then sent no location at all. The test
    # fixture omitted the three too, which is why the suite approved it.
    # They arrive together from the address lookup, so requiring them costs the
    # broker nothing.
    "street_address": "address",
    "city": "city",
    "postal_code": "postal code",
    # PROVINCE IS NOT ASKED FOR. This brokerage writes Ontario only, so there
    # is one possible answer and a question with one answer is not worth
    # asking. meta_capi.hash_province defaults an empty value to "on", so the
    # field still reaches Meta — it is simply not the broker's job to type it.
}


# How long a customer must have been insured for the lead to be worth reporting.
#
# ONE CONSTANT, because it has already moved once — five years was the original
# rule and four is what it became. A literal buried in the comparison would mean
# finding three of them next time.
def _missing_applicant_fields(lead_id: str) -> list[str]:
    """Which required applicant details are still empty, in form order.

    "(not provided)" COUNTS AS EMPTY. A manually created lead carries that
    placeholder in whichever of phone/email the broker did not have, and it is
    copied onto the applicant when the row is seeded — so a plain truthiness
    check would read it as a real answer and let the lead through with nothing
    to match on. Reuses driver_service._real_or_none rather than restating what
    counts as a placeholder in a second place.

    No applicant row at all means every field is missing, which is honest: the
    broker has not opened the applicant yet.
    """
    rows = driver_repo.list_for_lead(lead_id).data or []
    applicant = next((d for d in rows if d.get("is_principal")), None)
    if applicant is None:
        return list(_REQUIRED_APPLICANT_FIELDS.values())

    return [
        label
        for column, label in _REQUIRED_APPLICANT_FIELDS.items()
        if not driver_service._real_or_none(applicant.get(column))
    ]


def _validate_premiums_complete(lead_id: str, policy_type: str | None, stage: str) -> None:
    """Every product this policy covers has a proposed premium.

    FROM QUOTE DONE, and the reason is Meta rather than tidiness. Quote Done
    fires the CompleteRegistration event (the trigger in migrations/0100),
    which snapshots total_new_premium_annual AT THAT MOMENT — and event_id is
    what Meta deduplicates on. So an Auto and Homeowner lead quoted on its
    auto figure alone teaches Meta a conversion worth half the policy,
    PERMANENTLY: entering the home premium afterwards sends nothing, and the
    ad optimisation is trained on a number that was never right.

    EVERY PRODUCT, not just a non-zero total. This replaced a check that asked
    only whether the combined total was above zero — which any single figure
    satisfies.

    A zero needs no special case: `not row.get(...)` is falsy for 0, so a
    product entered as zero is refused exactly like one left blank. Sending
    value: 0 would tell Meta the conversion was worth nothing, which is a
    claim, not an absence.

    The broker has these numbers in hand at the moment they mark a quote
    done, which is exactly why refusing here costs them nothing and catching
    it afterwards would cost them the signal.
    """
    if not policy_type:
        raise HTTPException(
            status_code=422,
            detail=f"Choose the policy type before moving this lead to {stage}.",
        )

    rows = premium_repo.get_by_lead(lead_id).data or []
    row = rows[0] if rows else {}

    # Raw product keys — _join does the labelling, and passing labels in gives
    # it "home" to look up in a map keyed by "homeowner".
    missing = [
        product
        # Iterated over the label map so the message lists products in a fixed
        # order rather than whatever order the set happens to yield.
        for product in PRODUCT_LABEL
        if product in PRODUCTS_FOR.get(policy_type, frozenset())
        # THE ANNUAL COLUMN ANSWERS FOR BOTH (0106). Whichever unit the broker
        # typed in, the database fills the annual figure — so an empty one
        # means nothing was entered, not that it was entered monthly.
        and not row.get(f"new_{product}_premium_annual")
    ]
    if missing:
        raise HTTPException(
            status_code=422,
            detail=(
                f"Enter the {_join(missing)} premium before moving this lead to {stage}."
            ),
        )


# Below this, a proposed auto premium is almost certainly a monthly figure
# entered as an annual one. No real Ontario auto policy costs $1,000 a year.
#
# AUTO ONLY. Five of the fourteen live premium rows hold a home or tenant
# premium under $1,000 — 228, 324, 360, 432, 816 — and every one is genuine.
# Warning on those would teach brokers to click through the warning that
# matters.
_IMPLAUSIBLE_AUTO_ANNUAL = 1000

def _confirm_plausible_auto_premium(lead_id: str, confirm: bool) -> None:
    """Ask before letting an implausible auto premium reach Meta.

    Below $1,000 a year, a proposed auto premium is almost certainly a monthly
    figure entered with the unit left on Year. No real Ontario auto policy
    costs that.

    AUTO ONLY. Five of the live premium rows hold a home or tenant premium
    under $1,000 — 228, 324, 360, 432, 816 — and every one is genuine.
    Warning on those would teach brokers to click through the warning that
    matters.

    READS THE ANNUAL COLUMN, which is genuinely annual since 0106 — the
    database derives it whenever the broker types in months. The old worry
    about "the check must run on the CONVERTED figure, not the typed one" is
    gone, and so is the ×12 this function used to do by hand: there is nothing
    left here to get wrong.

    Reads the auto column directly for the same reason it always did: on a
    combined policy the total carries the home premium too, and a $600 auto
    beside a $2,000 home would pass a check made against their sum.
    """
    rows = premium_repo.get_by_lead(lead_id).data or []
    if not rows or confirm:
        return

    row = rows[0]
    annual = row.get("new_auto_premium_annual")
    if annual is None:
        return

    if float(annual) >= _IMPLAUSIBLE_AUTO_ANNUAL:
        return

    # 409, not 422: nothing about the request is malformed. The broker is
    # being asked a question, not corrected — the same shape as the
    # policy-type switch guard above.
    raise HTTPException(
        status_code=409,
        detail=(
            f"An auto premium of ${annual:,.0f} a year is unusually low — "
            "monthly figures are the usual cause. Move this lead to Quote "
            "Done anyway?"
        ),
    )


def _plpgsql_message_error(err: Exception, fallback: str, status: int = 400) -> HTTPException:
    """Surface a plpgsql function's own human message (raised as P0001) as a
    clean HTTP error. The message is embedded in the raised error's text —
    pull it out so the client shows exactly what the DB function said, rather
    than a generic string."""
    text = str(err)
    marker = "'message': '"
    if marker in text:
        detail = text.split(marker, 1)[1].split("'", 1)[0]
    else:
        detail = fallback
    return HTTPException(status_code=status, detail=detail)


def set_policy_type(lead_id: UUID, request: SetPolicyTypeRequest) -> dict:
    """Answer "is this a combined policy, and what kind" — the question that
    decides whether this quote has a property side at all.

    THE ONE PLACE the applicant's own home is created or destroyed. A Primary
    property exists because the quote is combined; it is not something a broker
    adds or removes directly, so both ends of its life live here.

    What each transition costs:

      first choice          creates the Primary, seeded with the applicant's
                            address. Nothing exists yet, so nothing is lost.
      -> a different type   the Primary is REPLACED, not re-typed: a new form
                            means new questions, so its answers start over.
                            Rentals survive unless the new type is Tenants.
      -> a Tenant type      as above, and the rentals go too — a tenant policy
                            covers one home.
      -> Auto Only          there is no property side any more, so everything
                            goes.
      -> the same value     a no-op. Nothing is touched and nothing is asked.

    AND ON EVERY TRANSITION: the premiums for products the new type does not
    cover are cleared (_stale_premium_products). They are not cosmetic — the
    totals are generated with coalesce, so a figure left behind keeps feeding
    the total Meta receives as the CompleteRegistration value.

    ANY CHANGE THAT DISCARDS ANSWERS REFUSES FIRST with a 409 naming what goes,
    and proceeds only when the request says the warning was accepted. That
    covers more than the rentals: changing the home's TYPE starts it over,
    because the three intakes ask different questions and a row carried across
    would keep answers the new form never asks (see property.create_primary).
    A dropdown that silently discards a call's worth of answers is not
    recoverable — there is no undo and no saved copy.
    """
    lead = repo.get_by_id(str(lead_id)).data
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    new_type = request.policy_type
    if lead[0].get("combined_policy_type") == new_type:
        # Nothing to do. Returned rather than refused: re-picking the value
        # already set is a no-op, not a mistake, and it must not trip the
        # confirmation guard below.
        return lead[0]

    # get-or-create rather than assume: the applicant row is made when a broker
    # first opens the lead, which is normally before they can reach this
    # control — but "normally" is not a guarantee to build on.
    applicant = driver_service.get_or_create_principal(str(lead_id))
    driver_id = applicant["id"]

    existing = property_repo.list_for_driver(driver_id).data or []
    rentals = [p for p in existing if p["property_role"] != "Primary"]

    # Premiums for products the new type does not cover. Worked out before
    # either branch because both can drop one: Auto Only drops the property
    # premium, and every property type drops something (the auto premium, the
    # other property's, or both).
    stale_premiums = _stale_premium_products(str(lead_id), new_type)

    # 'Auto Only' was called 'Standalone' before 0068. Renamed so the value
    # says what it covers rather than what it lacks.
    if new_type == "Auto Only":
        losses = []
        if existing:
            losses.append(_describe(existing))
        if stale_premiums:
            losses.append(f"the {_join(stale_premiums)} premium")

        if losses and not request.confirm:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Switching to auto only will delete {'; and '.join(losses)}"
                    " from this quote. This cannot be undone."
                ),
            )
        property_repo.delete_for_driver(driver_id)
    else:
        # 'Homeowners' and 'Tenants' name the POLICY; a property row names one
        # building, so the singular form is what belongs on it.
        wanted = _PROPERTY_TYPE_FOR[new_type]
        primary = next((p for p in existing if p["property_role"] == "Primary"), None)

        # A TYPE CHANGE STARTS THE HOME OVER. The three intakes ask different
        # questions, so the existing answers describe a form that no longer
        # applies — see property.create_primary. What is lost has to be said
        # before it happens, and it is more than the rentals: the home's own
        # answers go too.
        losses = []
        if primary and primary["property_type"] != wanted:
            where = _where(primary)
            losses.append(
                f"everything entered for the {primary['property_type'].lower()}"
                + (f" at {where}" if where else "")
            )
        # Rentals cannot hang off a tenant policy, so they go with the switch.
        if new_type in _TENANT_TYPES and rentals:
            losses.append(_describe(rentals))

        # A property-only policy has no auto side, so the cars go too. Counted
        # rather than named: the warning already names the property being lost,
        # and a second list of makes and models buries it.
        #
        # EVERY driver on the lead, not just the applicant — a household's cars
        # are commonly under a spouse, and counting only the applicant's would
        # warn "1 vehicle", delete one, and leave the spouse's orphaned on a
        # policy with no auto side.
        all_driver_ids = (
            []
            if _has_auto(new_type)
            else [d["id"] for d in (driver_repo.list_for_lead(str(lead_id)).data or [])]
        )
        vehicles = _vehicles_for(all_driver_ids)
        if vehicles:
            losses.append(f"{len(vehicles)} vehicle{'s' if len(vehicles) > 1 else ''}")

        # Named, not silent. A broker who typed a figure and watched it vanish
        # unwarned reads it as the system losing their work.
        if stale_premiums:
            losses.append(f"the {_join(stale_premiums)} premium")

        if losses and not request.confirm:
            raise HTTPException(
                status_code=409,
                detail=(
                    f"Switching to {new_type.lower()} will discard "
                    # Semicolon between the two clauses, not "and": the rental
                    # clause is itself a list joined by "and", so a third one
                    # between them leaves the reader unable to tell where the
                    # list breaks.
                    f"{'; and '.join(losses)}. This cannot be undone."
                ),
            )

        if vehicles:
            vehicle_repo.delete_for_drivers(all_driver_ids)

        if new_type in _TENANT_TYPES:
            property_repo.delete_rentals_for_driver(driver_id)

        property_service.create_primary(driver_id, wanted)

    # Both branches confirmed above, so clearing here covers every path.
    if stale_premiums:
        _clear_premiums(str(lead_id), stale_premiums)

    result = repo.set_combined_policy_type(str(lead_id), new_type)
    if not result.data:
        raise HTTPException(status_code=404, detail="Lead not found")

    # A commission recalculation used to run here, so that switching an Issued
    # Auto+Home lead to Auto Only did not leave the homeowner commission
    # standing on a policy that no longer had a home side. It went with the
    # commission module (0083).
    #
    # What that guarded is still guarded: _clear_premiums above removes the
    # premiums for products the new type does not cover, which is what
    # total_new_premium_annual is generated from — so the value Meta receives
    # follows the policy's real shape whether or not anything computes money
    # from it.

    return result.data[0]

# lead.combined_policy_type is the policy's name; property.property_type is one
# building's. They are deliberately different vocabularies — an Auto and
# Homeowner policy can hold a rental whose own type is Homeowner — so the
# mapping is explicit rather than assumed to be the same string.
#
# Both standalone and bundled forms map to the same property type: whether an
# auto policy rides along changes nothing about the building.
_PROPERTY_TYPE_FOR = {
    "Homeowner Only": "Homeowner",
    "Auto and Homeowner": "Homeowner",
    "Tenant Only": "Tenant",
    "Auto and Tenant": "Tenant",
    "Auto and Condo": "Condo",
}

# Policy types whose primary property is a rented unit. A tenant does not own
# the building, so they cannot hold rental dwellings under the same policy —
# switching to one of these deletes any rentals already entered.
#
# A SET, not an equality check: 0068 split the old single "Tenants" value into
# a standalone and a bundled form, and an `== "Tenants"` left behind would
# silently stop matching either — no error, just rentals surviving a switch
# that should have removed them.
_TENANT_TYPES = frozenset({"Tenant Only", "Auto and Tenant"})


# Policy types with NO auto side. Switching to one of these wipes the lead's
# vehicles — there is no auto policy left for them to belong to.
#
# Listed rather than derived from the name: matching on a "Auto" prefix would
# be one badly-named future policy type away from silently deleting the wrong
# thing.
_PROPERTY_ONLY_TYPES = frozenset({"Homeowner Only", "Tenant Only"})


def _has_auto(policy_type: str) -> bool:
    return policy_type not in _PROPERTY_ONLY_TYPES


def _stale_premium_products(lead_id: str, new_type: str) -> list[str]:
    """Products whose premium this lead holds but the new policy type drops.

    THE FIGURES ARE NOT COSMETIC. total_new_premium_annual is a generated
    column built with coalesce, so a premium left behind keeps feeding the
    total — and that total is what Meta's CompleteRegistration event carries
    as its value. Switching Auto Only -> Tenant Only without this would report a
    tenant policy worth its old auto premium, teaching Meta's optimisation the
    wrong number, with nothing anywhere reporting an error.

    Both the current and the new figure go. Leaving `current` behind would
    leave the savings chip comparing a current auto premium against no new
    one, showing a loss the customer is not taking.
    """
    rows = premium_repo.get_by_lead(lead_id).data or []
    if not rows:
        return []

    row = rows[0]
    keeping = PRODUCTS_FOR.get(new_type, frozenset())
    return [
        product
        # Iterated over the label map, not the row, so the warning always
        # lists products in a fixed order rather than PostgREST's column order.
        for product in PRODUCT_LABEL
        if product not in keeping
        and any(
            row.get(f"{prefix}_{product}_premium_annual") is not None
            for prefix in ("current", "new")
        )
    ]


def _clear_premiums(lead_id: str, products: list[str]) -> None:
    """Null the figures for products this policy no longer covers.

    BOTH HALVES OF EACH PAIR (0106). Nulling only one would leave the other
    standing, and the trigger derives the missing half from whichever column
    the unit names — so clearing just the annual figure on a monthly quote
    would see it reappear, multiplied, on the next write. The totals and the
    savings are generated and follow on their own; writing those would be
    rejected by Postgres.
    """
    values = {
        f"{prefix}_{product}_premium_{unit}": None
        for product in products
        for prefix in ("current", "new")
        for unit in ("annual", "monthly")
    }
    premium_repo.update(lead_id, values)


def _vehicles_for(driver_ids: list[str]) -> list[dict]:
    """Every vehicle across these drivers, as a plain list.

    The repository returns None for an empty id list rather than a result
    object, so unwrapping it at the call site would put two branches in the
    middle of the policy-switch logic.
    """
    result = vehicle_repo.list_for_drivers(driver_ids)
    return (result.data if result else []) or []


def _join(products: list[str]) -> str:
    """"auto", or "auto and home" — the products in one short phrase.

    Deliberately terse. This sits inside a warning that already names the
    property and counts the vehicles, and "the auto and home premium" carries
    the whole point in five words. Spelling out each figure separately would
    push the sentence past the length a broker reads before clicking through.
    """
    labels = [PRODUCT_LABEL[p] for p in products]
    if len(labels) == 1:
        return labels[0]
    return f"{', '.join(labels[:-1])} and {labels[-1]}"


def _where(row: dict) -> str:
    """The property's address, or a stand-in when it has none yet.

    Empty when there is none — a rental added moments ago has no address, and
    naming it by street would produce "the rental dwelling at None". The
    caller words that case differently.
    """
    parts = [row.get("street_address"), row.get("city")]
    return ", ".join(p for p in parts if p)


def _describe(rows: list[dict]) -> str:
    """Name what is about to be deleted, rather than counting it.

    "1 property" tells a broker nothing they can act on — they cannot tell
    whether it is the home they spent the call on or a rental they added by
    mistake a second ago. Naming each row by role and address lets them
    recognise it:

        the primary residence at 50 Pinnacle Dr, Kitchener
        the primary residence at 50 Pinnacle Dr and 2 more: the rental
        dwelling at 14 King St, the rented condo (no address yet)

    Capped at three because past that the list stops being readable and the
    count is the clearer signal.
    """
    def name(r: dict) -> str:
        # 'Primary' is the column's value, not what a broker calls it — on its
        # own it reads as an adjective with the noun missing.
        role = "primary residence" if r["property_role"] == "Primary" else r["property_role"].lower()
        address = _where(r)
        # "at 50 Pinnacle Dr" reads as a place; "at no address yet" does not,
        # so a blank row is described in brackets instead of located.
        return f"the {role} at {address}" if address else f"the {role} (no address yet)"

    named = [name(r) for r in rows[:3]]
    if len(rows) > 3:
        named.append(f"{len(rows) - 3} more")
    if len(named) == 1:
        return named[0]
    return f"{', '.join(named[:-1])} and {named[-1]}"


# The columns are NOT NULL but the broker is only asked for one way to reach
# the customer — see CreateManualLeadRequest. This is the value the rest of the
# system already uses for a contact detail that was never collected, and
# LeadList hides it rather than rendering it as though it were data.
_NOT_PROVIDED = "(not provided)"


def create_manual_lead(request: CreateManualLeadRequest, broker: dict) -> dict:
    """A lead the broker typed in themselves — a referral, a walk-in, a call to
    the office.

    ASSIGNED TO ITS CREATOR, not allocated. Every other lead goes through the
    round-robin engine because ad spend has to be shared out; this one arrived
    because a particular broker answered the phone, and handing it to a
    colleague mid-conversation would be absurd. So there is nothing to
    allocate: the creator is the answer.

    That is also why it never touches the daily cap. The cap counts rows in
    lead_delivery, which only assign_lead writes — skipping the engine means no
    delivery row, so a manual lead cannot use up a broker's share of the paid
    leads. Nothing enforces this; it falls out of not calling assign_lead.

    The GROUP is the creator's own, read from their token. A broker cannot file
    a customer into someone else's book, because the request has no field for
    it.
    """
    # Read the group here rather than off the token's broker: that dict comes
    # from AUTH_COLUMNS, which is deliberately narrow — it carries the password
    # hash and TOTP secret, so it is kept to what authentication needs and
    # group_id is not part of that.
    creator = broker_repo.get_group_id(broker["id"]).data
    if not creator:
        raise HTTPException(404, "Broker not found")
    group_id = creator[0].get("group_id")
    if not group_id:
        # Nothing to route to. Not reachable today — every broker row has a
        # group — but a lead with no group cannot be listed, counted or
        # reassigned, so it must not be created at all.
        raise HTTPException(400, "Your account has no broker group — ask an admin")

    # The Recycle Bin holds deactivated brokers, not leads. Unreachable while
    # get_current_broker rejects inactive accounts, but a customer filed into
    # the bin would be invisible in every screen that matters.
    if broker_group_repo.is_recycle_bin(str(group_id)):
        raise HTTPException(400, "Leads can't be created from the Recycle Bin")

    values = {
        "group_id": str(group_id),
        "source": "manual",
        "name": request.name.strip(),
        "phone": (request.phone or "").strip() or _NOT_PROVIDED,
        "email": (request.email or "").strip() or _NOT_PROVIDED,
        "stage": "New",
        "assigned_broker_id": broker["id"],
        "assigned_at": datetime.now(timezone.utc).isoformat(),
        "assigned_by": "broker",
        # When the customer asked. For a manual lead that is the moment the
        # broker took the call, which is now — unlike a Meta lead, where the
        # webhook reports a time that may be older than our row.
        "requested_at": datetime.now(timezone.utc).isoformat(),
    }

    try:
        result = repo.insert(values)
    except Exception as err:
        raise db_error(
            err,
            on_foreign_key=HTTPException(400, "That broker group doesn't exist"),
        )

    lead = result.data[0]

    # Custody and arrival history, which assign_lead would otherwise have
    # written. Not fatal if it fails — the lead itself exists and is workable,
    # and losing the quote because its history row did not write would be the
    # worse outcome — but it is logged, since a lead with no custody row is a
    # gap someone will eventually have to explain.
    try:
        repo.insert_manual_custody(lead["id"], broker["id"])
    except Exception:
        logger.exception("Manual lead %s created without its custody rows", lead["id"])

    return lead
