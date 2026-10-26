"""HTTP layer for follow-ups.

Gated by get_current_broker, with no lead-ownership guard — same reasoning as
routes/contact_log.py and routes/driver.py.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, Response

from auth_dependencies import get_current_broker
from schemas.reminder import (
    CreateReminderRequest,
    MyDayRemindersResponse,
    ReminderResponse,
    SnoozeReminderRequest,
)
from services import reminder as service

router = APIRouter(tags=["reminder"], dependencies=[Depends(get_current_broker)])


@router.get("/leads/{lead_id}/reminders", response_model=list[ReminderResponse])
def list_reminders(lead_id: UUID):
    """This lead's follow-ups, soonest first.

    Closed ones included, so the panel can show what was already done for this
    customer rather than only what is outstanding.
    """
    return service.list_for_lead(str(lead_id))


@router.post("/leads/{lead_id}/reminders", response_model=ReminderResponse, status_code=201)
def create_reminder(
    lead_id: UUID,
    request: CreateReminderRequest,
    broker: dict = Depends(get_current_broker),
):
    """Set a follow-up. The broker is the authenticated caller."""
    return service.create(
        lead_id=str(lead_id),
        broker_id=broker["id"],
        due_at=request.due_at.isoformat(),
        note=request.note,
        kind=request.kind,
        source_contact_log_id=(
            str(request.source_contact_log_id) if request.source_contact_log_id else None
        ),
    )


@router.get("/my-day/reminders", response_model=MyDayRemindersResponse)
def my_day_reminders(broker: dict = Depends(get_current_broker)):
    """The two numbers My Day's Follow-ups block is waiting for — overdue and
    due today — plus the rows behind them.

    Counted in Ontario time, not UTC: a broker asking "what's due today" means
    their own day, and computing it in UTC would move the boundary five hours
    and put this evening's follow-ups into tomorrow's count.
    """
    return service.my_day(broker["id"])


@router.get("/reminders/mine", response_model=list[ReminderResponse])
def list_my_reminders(broker: dict = Depends(get_current_broker)):
    """Every open reminder this broker holds, soonest first.

    THE WORKLIST, not the day view. /my-day/reminders stops at end-of-today
    because it answers "what is due now"; this one carries the whole list,
    because a follow-up set for next month is still owed and a page that hid
    it would send the broker back to guessing.

    Scoped to the caller by the service, so a broker cannot read anyone
    else's.
    """
    return service.list_mine(broker["id"])


@router.patch("/reminders/{reminder_id}/complete", response_model=ReminderResponse)
def complete_reminder(reminder_id: UUID, broker: dict = Depends(get_current_broker)):
    """The broker did it."""
    return service.complete(str(reminder_id), broker["id"])


@router.patch("/reminders/{reminder_id}/dismiss", response_model=ReminderResponse)
def dismiss_reminder(reminder_id: UUID, broker: dict = Depends(get_current_broker)):
    """No longer relevant — the lead went Lost, the customer bought elsewhere.

    Separate from complete so an abandoned follow-up is never counted as work
    that was done.
    """
    return service.dismiss(str(reminder_id), broker["id"])


@router.patch("/reminders/{reminder_id}/snooze", response_model=ReminderResponse)
def snooze_reminder(reminder_id: UUID, request: SnoozeReminderRequest):
    """Push it to a later time, and count the attempt."""
    return service.snooze(str(reminder_id), request.due_at.isoformat())


@router.get(
    "/reminders/{reminder_id}/calendar.ics",
    # Declared, because FastAPI otherwise advertises application/json for every
    # route — and a generated client would then try to JSON-parse an .ics file
    # and fail on a route that works perfectly.
    response_class=Response,
    responses={200: {"content": {"text/calendar": {}}, "description": "iCalendar file"}},
)
def reminder_ics(reminder_id: UUID):
    """The Add to calendar button.

    Returns a calendar file the broker's own calendar imports — Google,
    Outlook, Apple, whichever they use. One-way and deliberately so: no OAuth,
    no per-broker setup, nothing to keep in sync.
    """
    body = service.to_ics(str(reminder_id))
    return Response(
        content=body,
        media_type="text/calendar",
        # Without a filename the browser names the download after the URL path,
        # which would save as "calendar.ics" for every reminder anyway — but an
        # explicit attachment is what makes the browser download rather than
        # try to render it as text.
        headers={"Content-Disposition": f'attachment; filename="follow-up-{reminder_id}.ics"'},
    )
