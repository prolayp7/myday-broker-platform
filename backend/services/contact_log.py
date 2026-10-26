"""Business logic for the contact timeline.

Owns the one thing the HTTP layer must not: reading the lead's CURRENT stage to
stamp onto the row, rather than accepting it from the caller.
"""

import logging

from fastapi import HTTPException

from db_errors import db_error
from repositories import contact_log as repo
from repositories import lead as lead_repo

logger = logging.getLogger("myday.contact")


def list_for_lead(lead_id: str) -> list[dict]:
    """The lead's timeline — every contact, newest first.

    An empty list is a normal answer, not a 404: a lead nobody has rung yet has
    no contacts, which is different from a lead that does not exist. The lead's
    existence is checked here so a mistyped id says so rather than looking like
    a customer nobody has ever called.
    """
    if not lead_repo.get_by_id(lead_id).data:
        raise HTTPException(status_code=404, detail="Lead not found")

    return repo.list_for_lead(lead_id).data or []


def set_notes(contact_id: str, notes: str | None) -> dict:
    """Write a broker's note onto an existing contact.

    Deliberately NOT restricted to the broker who made the call. A lead moves
    between brokers, and whoever holds it now is the person who needs the
    timeline to make sense — blocking them from annotating a colleague's call
    would protect nothing and cost the note.

    An empty string is stored as null rather than as a blank note, so "has a
    note" stays a single check everywhere it is asked.
    """
    if not repo.get_by_id(contact_id).data:
        raise HTTPException(status_code=404, detail="Contact not found")

    cleaned = (notes or "").strip() or None
    repo.set_notes(contact_id, cleaned)

    updated = repo.get_by_id(contact_id).data
    return updated[0]


def log_contact(
    lead_id: str,
    broker_id: str,
    outcome: str,
    notes: str | None = None,
    occurred_at: str | None = None,
) -> dict:
    """Record one contact attempt.

    stage_at_contact is READ FROM THE LEAD HERE, never accepted from the
    caller. It is the answer to "where was this lead when we spoke", and a
    client that could set it could file a call against a stage the lead was
    never in — quietly corrupting the one thing this column exists to make
    countable.

    Read BEFORE any stage change this same contact goes on to cause: the UI
    logs a contact and moves the stage in one action, and the useful fact is
    what we were trying to move them OUT of.

    occurred_at defaults to now at the database. A broker logging yesterday's
    WhatsApp message passes the real time, so the timeline reads in the order
    things actually happened rather than the order they were typed.
    """
    leads = lead_repo.get_by_id(lead_id).data
    if not leads:
        raise HTTPException(status_code=404, detail="Lead not found")

    values = {
        "lead_id": lead_id,
        "broker_id": broker_id,
        "outcome": outcome,
        "stage_at_contact": leads[0]["stage"],
        "notes": notes,
    }
    if occurred_at:
        values["occurred_at"] = occurred_at

    try:
        result = repo.insert(values)
    except Exception as err:
        raise db_error(
            err,
            on_foreign_key=HTTPException(
                status_code=404, detail="That lead or broker no longer exists"
            ),
        )

    # Re-read so the caller gets the embedded broker, which an
    # insert does not return — otherwise the row the UI appends to its timeline
    # would be shaped differently from every other row in it.
    created = repo.get_by_id(result.data[0]["id"]).data
    return created[0] if created else result.data[0]
