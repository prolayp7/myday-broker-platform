"""Pydantic shapes for follow-ups."""

from datetime import date, datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, field_validator

# Matches the check constraint in migrations/0061_reminder.sql.
ReminderKind = Literal["Callback", "Renewal", "Document", "Other"]


class CreateReminderRequest(BaseModel):
    """Set a follow-up on a lead.

    NO status or broker_id field: a new reminder is Open by definition, and the
    broker is the authenticated caller. Accepting either would let a client
    create a pre-closed reminder, or file one under someone else's name.
    """

    due_at: datetime

    # Required, and non-blank. A reminder without a reason is an alarm nobody
    # can act on — by the time it fires the broker has forgotten why they set
    # it. The database enforces this too (length(trim(note)) > 0).
    note: str = Field(min_length=1, max_length=2000)

    # Defaults to Callback, the common case. 'Renewal' is the one that matters
    # most: an immovable insurer deadline, where missing it means the customer
    # auto-renewed elsewhere and is gone for a year.
    kind: ReminderKind = "Callback"

    # Set when this came out of a logged contact — e.g. an outcome of
    # 'Call Back Requested'. Null when set manually.
    source_contact_log_id: UUID | None = None

    @field_validator("note")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        # min_length alone passes a string of spaces, which the database then
        # rejects — better to fail here with a readable message than surface a
        # constraint violation.
        if not v.strip():
            raise ValueError("A follow-up needs a note saying what to do")
        return v


class SnoozeReminderRequest(BaseModel):
    """Push a follow-up to a later time.

    Moving the date IS the snooze — there is no separate snoozed status, or the
    row would sit with its old due date still in the past and count as overdue
    forever.
    """

    due_at: datetime


class ReminderLeadSummary(BaseModel):
    """Which customer this follow-up is about. Enough to act on it without
    opening the lead — My Day lists reminders across many leads."""

    id: UUID
    name: str
    phone: str | None = None

    # WHAT THE REMINDERS PAGE TRIAGES ON. A Lost lead renewing next week is a
    # different job from a Quote Done chasing a document, and without these
    # the broker has to open every row to find out which is which.
    #
    # They come from different tables — stage is the lead's, renewal_date is
    # the principal driver's (0098) — and the service flattens the second one
    # onto here.
    stage: str | None = None
    renewal_date: date | None = None


class ReminderResponse(BaseModel):
    id: UUID
    lead_id: UUID
    broker_id: UUID
    kind: str
    due_at: datetime
    note: str
    status: str

    completed_at: datetime | None = None
    completed_by: UUID | None = None
    source_contact_log_id: UUID | None = None

    # How many times this same follow-up has been attempted and pushed. Six
    # attempts is a different conversation from one.
    attempt_count: int

    # Which automatic rule created this, or null when a broker set it by hand
    # (0104). The page shows nothing different for either — a reminder is a
    # reminder — but it is what the sweep's uniqueness key hangs off.
    auto_rule: str | None = None
    auto_for_renewal_date: date | None = None

    lead: ReminderLeadSummary | None = None

    created_at: datetime
    updated_at: datetime


class MyDayRemindersResponse(BaseModel):
    """What My Day's Follow-ups block shows — the two numbers, and the rows
    behind them so the block can list what is actually due."""

    overdue_count: int
    due_today_count: int
    reminders: list[ReminderResponse]
