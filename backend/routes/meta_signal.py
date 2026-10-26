from uuid import UUID

from fastapi import APIRouter, Depends, Query

from auth_dependencies import require_admin
from services import meta_signal as service

router = APIRouter(tags=["meta_signal"], dependencies=[Depends(require_admin)])


@router.get("/meta-signal")
def list_signals(
    status: str | None = Query(None, pattern="^(Pending|Sent|Failed)$"),
    limit: int = Query(100, ge=1, le=500),
):
    """Recent conversion signals, newest first — what the admin CAPI screen
    renders.

    The screen exists because a failure had nowhere to surface. Meta returns a
    400 for an EXPIRED ACCESS TOKEN, every 400 is terminal, and a Failed row is
    never picked up again by the drain — so a token expiring overnight lost
    every conversion queued in that window, silently. Now they are visible, and
    retryable.
    """
    return {"rows": service.list_recent(status=status, limit=limit)}


@router.get("/meta-signal/leads")
def signal_leads(days: int = Query(90, ge=1, le=365)):
    """One row per Meta lead that arrived in the window: its broker, when it
    arrived, and which milestones (Lead, Quote done, Purchase) have reached
    Meta. The list the admin Meta Conversions screen is built on.

    Per LEAD, where every other endpoint here is per signal — one lead is up to
    three signals, and the screen is about leads.
    """
    return service.lead_progress(days=days)

@router.get("/meta-signal/health")
def signal_health():
    """Is anything wrong on Meta's side right now?

    Asks Meta directly rather than waiting for a conversion to fail — by the
    time a failure appears, a lead has already gone unreported.

    Note the token in use is a SYSTEM USER token, which Meta issues with no
    expiry. So the real question this answers is "is it still VALID" —
    revoked, restricted, or carrying the wrong scopes — not "has it expired".
    """
    return service.health()


@router.get("/meta-signal/summary")
def signal_summary(days: int = Query(14, ge=1, le=90)):
    """Counts for the admin screen's tiles and charts.

    Declared BEFORE /meta-signal/{signal_id} would be — FastAPI matches in
    order, so a literal path has to come first or "summary" is read as a
    signal id.
    """
    return service.summary(days=days)


@router.get("/meta-signal/send-quality")
def signal_send_quality(
    days: int = Query(14, ge=1, le=90),
):
    """Which matching fields our sent events actually carried, by event type.

    Answers "are we giving Meta enough to match on" from OUR side. Meta's own
    verdict is a separate question — Event Match Quality is not exposed for
    conversion-leads integrations (Meta's docs list them as an exception), so
    this reports what we sent, never an invented score.

    Declared before /meta-signal/{signal_id} for the same reason as summary
    above: FastAPI matches in order, so a literal path must come first.
    """
    return service.send_quality(days=days)


@router.get("/meta-signal/meta-received")
def signal_meta_received(
    days: int = Query(14, ge=1, le=90),
):
    """What Meta says it received, for the same window.

    SEPARATE FROM send-quality above, which is a fast database read. This one
    calls Meta, so it is fetched independently by the dashboard: a slow or
    unreachable Meta must not hold up figures that are already in hand.

    Returns {"available": false} rather than an error when Meta cannot be
    reached — the panel shows that as "unavailable", which is a different
    statement from "nothing arrived".
    """
    return service.meta_received(days=days)


@router.post("/meta-signal/{signal_id}/retry")
def retry_signal(signal_id: UUID, admin: dict = Depends(require_admin)):
    """Put ONE failed signal back in the queue; the next drain sends it.

    THE ROW KEEPS ITS ID, which is what makes this safe: it is the event_id
    Meta deduplicates on, so retrying something Meta actually received cannot
    count one conversion twice.

    Refuses anything not Failed with a 409. Re-sending a Sent event is a
    different request from retrying a failed one, and this button is not it.
    """
    return service.retry(str(signal_id), admin["id"])


@router.post("/meta-signal/retry-dead-token")
def retry_dead_token(admin: dict = Depends(require_admin)):
    """Re-queue every signal that failed on a dead access token (code 190).

    THE BUTTON TO PRESS AFTER REPLACING A TOKEN. A credential dying overnight
    fails every conversion queued in that window, and the single-row retry is
    unusable at that scale.

    NOT "retry everything that failed", deliberately — the other failures in
    that list were judged on their merits and resending them repeats a request
    Meta already refused. Declared before /meta-signal/{signal_id} so FastAPI
    does not read "retry-dead-token" as a signal id.
    """
    return service.retry_dead_token_failures(admin["id"])


@router.post("/meta-signal/send-pending")
def send_pending():
    """Send every Pending meta_signal row now, without waiting for the drain.

    NOT the only caller, despite what this said until 2026-08-07:
    main.py's lifespan hook runs run_drain_forever() every 15 minutes, and has
    since the drain was built. This endpoint is the manual nudge — after
    fixing a Failed row, or to avoid waiting during a Test Events run.

    Safe to run alongside the drain. Both send the same rows, and Meta
    deduplicates on event_id (meta_signal.id), so the worst case is a wasted
    request rather than a double-counted conversion.

    Never fails the request over one bad row; returns counts of
    sent/failed/skipped so an admin can see the outcome."""
    return service.send_pending_signals()
