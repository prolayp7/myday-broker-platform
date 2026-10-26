"""Business logic for the driver domain — routes stay thin, repositories stay
error-free.

The one decision worth stating: the applicant row is GET-OR-CREATED, not
created by an explicit action. A lead's driver record comes into existence the
first time a broker opens that lead, and every later open returns the same row.
Nothing in the UI asks "create a driver?" — the question has one answer, so it
is not asked.
"""

import logging
import re

from fastapi import HTTPException

import claude_client
import supabase_storage
from db_errors import UNIQUE_VIOLATION, db_error
from repositories import driver as repo
from repositories import lead as lead_repo
from schemas.driver import UpdateDriverRequest

logger = logging.getLogger("myday.driver")


def get_or_create_principal(lead_id: str) -> dict:
    """The lead's applicant driver, created on first open and seeded from the
    lead's own name, phone and email.

    THE SEED IS A STARTING POINT, NOT A CLAIM. The applicant is usually the
    same person as the lead but not always — someone can enquire on behalf of
    a spouse — so the broker confirms or corrects these on the call. 0035
    originally argued for creating this row blank, on the grounds that a lead's
    details are unverified marketing data; that held while DASH was going to
    overwrite them with something verified. With DASH gone the choice is
    between a starting point and an empty box, and an empty box just means
    retyping a name already on screen.

    Seeded ONCE, at creation. Nothing re-seeds later, so a cleared field stays
    cleared — a field that refills itself after being emptied reads as a bug.

    Safe under concurrency. Two tabs opening the same lead at once both see no
    row and both insert; the partial unique index
    idx_driver_one_principal_per_lead lets exactly one win, and the loser
    re-reads the winner's row instead of erroring. That is why the insert is
    caught rather than guarded by a prior existence check — a check-then-insert
    has a window between the two, an index does not.
    """
    existing = repo.get_principal(lead_id)
    if existing.data:
        return existing.data[0]

    # Read the lead for the seed values. Checked explicitly rather than left to
    # the insert's foreign-key violation: PostgREST returns an empty list for a
    # row that does not exist, so indexing into it would raise IndexError and
    # surface as a 500 before the FK ever had a chance to give us a clean 404.
    lead = lead_repo.get_by_id(lead_id).data
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    # THE SAME SPLIT DASH DOES, AT THE SAME MOMENT THE NAME ARRIVES. One rule
    # everywhere: a name is split wherever one is set, and the lead's source
    # decides only whether the result is sent to Meta. Without this a lead with
    # no DASH report yet has blank name parts, so it can never satisfy the
    # applicant-complete check and never fires a Lead.
    #
    # An ambiguous name Claude cannot resolve yields blanks, which are stored
    # as null — the broker fills them on the screen they are already on.
    seed_name = lead[0].get("name")
    first, middle, last = claude_client.split_name(seed_name)

    try:
        created = repo.insert_principal(
            lead_id,
            name=seed_name,
            first_name=first or None,
            middle_name=middle or None,
            last_name=last or None,
            # A manually created lead carries "(not provided)" in whichever
            # contact detail was not collected — lead.phone/email are NOT NULL,
            # so there has to be something there. Seeding that string into the
            # driver would put it in a form field as though the broker had
            # typed it, and they would have to clear it before entering the
            # real number.
            phone=_real_or_none(lead[0].get("phone")),
            email=_real_or_none(lead[0].get("email")),
        )
    except Exception as err:
        if UNIQUE_VIOLATION in str(err):
            # Lost the race — the row now exists, so read it back.
            reread = repo.get_principal(lead_id)
            if reread.data:
                return reread.data[0]
        raise db_error(
            err,
            on_foreign_key=HTTPException(status_code=404, detail="Lead not found"),
        )

    return created.data[0]


def list_for_lead(lead_id: str) -> list[dict]:
    """Every driver on a lead, applicant first.

    Ensures the applicant EXISTS before listing, via the same get-or-create as
    a single-driver read. Without that, a lead nobody has opened yet returns an
    empty list, and the UI would render a lead with no drivers at all — when in
    fact every lead has an applicant by definition.
    """
    get_or_create_principal(lead_id)
    return repo.list_for_lead(lead_id).data or []


def add_driver(lead_id: str, relationship: str) -> dict:
    """Add an additional driver — a spouse, a child — to a lead.

    Relationship is the only thing asked for here; everything else (licensing
    dates, vehicles) is filled in on New Policy once the row exists.
    'Applicant' is rejected: a lead has exactly one applicant and it is created
    with the lead, not added.
    """
    if relationship == "Applicant":
        raise HTTPException(
            status_code=400,
            detail="A lead already has an applicant — pick the relationship to them",
        )

    try:
        created = repo.insert_additional(lead_id, relationship)
    except Exception as err:
        raise db_error(
            err,
            on_foreign_key=HTTPException(status_code=404, detail="Lead not found"),
        )
    return created.data[0]


def delete_driver(driver_id: str) -> None:
    """Remove an additional driver.

    The APPLICANT is never deletable: they are the lead's subject, and a lead
    without one is not a lead. Deleting them would also orphan the quote.

    Deliberately destructive, and deliberately not a soft delete: a broker
    removes a driver because that person is not on the policy, and a hidden
    row that still holds their licensing details is worse than no row. The
    confirmation lives in the UI, where the person's name can be named.
    """
    existing = get_driver(driver_id)
    if existing["is_principal"]:
        raise HTTPException(
            status_code=400,
            detail="The applicant cannot be removed from their own lead",
        )

    try:
        repo.delete(driver_id)
    except Exception as err:
        raise db_error(err)

    # THE STORED DASH REPORT GOES WITH THEM (0102).
    #
    # THERE IS NO CASCADE, though the column looks like there would be:
    # Supabase Storage has no foreign key to Postgres, so deleting the row
    # leaves the object behind forever unless something removes it. That
    # object is a customer's claims history sitting in a bucket with nothing
    # pointing at it.
    #
    # ROW FIRST, THEN THE OBJECT — the order broker_document.remove uses, and
    # for its reason. Deleting the file first would leave a live driver row
    # pointing at a PDF that is gone if the row delete then failed: a broken
    # download instead of a tidy one. This way the worst case is an orphaned
    # file, which costs storage and nothing else.
    #
    # Best-effort. The driver is already gone; failing the request because a
    # leftover file lingers would report a completed deletion as an error.
    path = existing.get("dash_report_path")
    if path:
        try:
            supabase_storage.delete(path, bucket=supabase_storage.DASH_BUCKET)
        except supabase_storage.StorageError:
            logger.exception(
                "Deleted driver %s but could not remove their DASH report %s",
                driver_id,
                path,
            )


def update_driver(driver_id: str, request: UpdateDriverRequest) -> dict:
    """Patch a driver with only the fields the caller actually sent.

    exclude_unset is load-bearing: the intake form is filled over several
    calls, and a request carrying just a licence number must not blank out the
    name captured earlier. Sending an explicit null still clears a field — that
    is a different intent from omitting it.
    """
    values = request.model_dump(exclude_unset=True, mode="json")
    if not values:
        # Nothing to write. Return the row as-is rather than issuing an empty
        # UPDATE, which Postgres accepts but which would bump updated_at and
        # imply a change that never happened.
        return get_driver(driver_id)

    try:
        result = repo.update(driver_id, values)
    except Exception as err:
        raise db_error(err)

    if not result.data:
        raise HTTPException(status_code=404, detail="Driver not found")
    return result.data[0]


def get_driver(driver_id: str) -> dict:
    result = repo.get_by_id(driver_id)
    if not result.data:
        raise HTTPException(status_code=404, detail="Driver not found")
    return result.data[0]


# Matches the frontend's isPlaceholderContact (domain/leadStages.ts). Kept in
# step by hand: both sides have to agree on what "no value" looks like, and the
# string is written by services/lead.create_manual_lead.
_PLACEHOLDER_CONTACT = re.compile(
    r"^\(?\s*(not provided|not available|n/?a|unknown|none)\s*\)?$", re.I
)


def _real_or_none(value: str | None) -> str | None:
    """The value, unless it is a stand-in for one that was never collected.

    WHITESPACE IS NOT A VALUE. The old form tested `if value and ...` before
    stripping, so "   " was truthy, never reached the placeholder match, and
    counted as a real answer. That put this check out of step with the SQL the
    Lead trigger uses, and the divergence was reachable: an applicant whose
    address and city held only spaces was reported complete, so the lead could
    reach Quote Done and fire CompleteRegistration for a lead that could never
    have fired Lead — a funnel with a middle and no beginning.

    fullmatch rather than match: the pattern already ends in `$` so today they
    agree, but match anchors only the start, and this reads as what it means.

    Returns the ORIGINAL value, not the stripped one. The question here is
    "is there a real value?", not "what should be stored" — trimming what a
    broker typed is a write-path decision and does not belong in a predicate.
    """
    if value is None:
        return None

    stripped = value.strip()
    if not stripped or _PLACEHOLDER_CONTACT.fullmatch(stripped):
        return None

    return value
