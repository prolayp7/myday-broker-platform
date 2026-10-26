"""Pydantic shapes for the contact timeline.

Every row is a contact a broker logged by hand.
"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

# Matches the check constraint in migrations/0060_contact_log.sql. Kept in sync
# by hand: a value here the database rejects surfaces as an error, not a silent
# write.
Outcome = Literal[
    "Answered",
    "No Answer",
    "Busy",
    "Wrong Number",
    "Not Interested",
    "Call Back Requested",
]


class LogContactRequest(BaseModel):
    """A broker recording a contact they made themselves — WhatsApp, their own
    mobile, in person.

    NO stage_at_contact FIELD, deliberately. It is read from the lead
    server-side (services/contact_log.py). A client that could set it could
    file a call against a stage the lead was never in, which would quietly
    corrupt the one thing that column exists to make countable.
    """

    outcome: Outcome

    # Optional at the API even though a note is what makes a hand-logged
    # contact worth anything: 'No Answer' genuinely has nothing to say, and
    # requiring a note there would train brokers to type a full stop.
    notes: str | None = Field(default=None, max_length=5000)

    # When the contact actually happened. Defaults to now at the database.
    # Passed explicitly when a broker logs something after the fact, so the
    # timeline reads in the order things happened rather than were typed.
    occurred_at: datetime | None = None


class UpdateContactNotesRequest(BaseModel):
    """Adding or editing the note on a contact that already exists.

    Its own request rather than a general PATCH: notes are the only field on a
    contact a broker should be able to change after the fact. Outcome, duration
    and time are what happened, and stage_at_contact is history — none of them
    are opinions to be revised.

    Nullable so a note typed against the wrong call can be cleared, not just
    overwritten.
    """

    notes: str | None = Field(default=None, max_length=5000)


class ContactBrokerSummary(BaseModel):
    """Who made this contact. Named rather than just an id — a reassigned
    lead's timeline shows several brokers, and "who spoke to them" is the point
    of showing it."""

    id: UUID
    name: str


class ContactResponse(BaseModel):
    id: UUID
    lead_id: UUID
    broker_id: UUID
    outcome: str
    stage_at_contact: str
    notes: str | None = None
    occurred_at: datetime

    broker: ContactBrokerSummary | None = None

    created_at: datetime
    updated_at: datetime
