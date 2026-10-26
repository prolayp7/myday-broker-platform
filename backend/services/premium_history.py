"""Business logic for premiums. Owns every HTTPException decision.

Writes only the six ANNUAL figures a broker types and the facts about the
quote. The two totals and the savings are generated columns (0099) — read
back, never written, so a total cannot disagree with the figures it came from.

This said MONTHLY and cited 0067, which 0099 reversed: the annual columns
stopped being generated and became the ones a broker writes directly.
"""

import logging

from fastapi import HTTPException

from db_errors import UNIQUE_VIOLATION, db_error
from repositories import lead as lead_repo
from repositories import premium_history as repo
from schemas.lead import PRODUCT_LABEL, PRODUCTS_FOR
from schemas.premium_history import SavePremiumRequest

logger = logging.getLogger("myday.premium")


def _refuse_products_outside_the_policy(policy_type: str | None, values: dict) -> None:
    """Raise if a figure belongs to a product this policy does not cover.

    THE RACE THIS CLOSES, and it is not hypothetical: these fields save on
    BLUR. A broker switches Auto and Homeowner to Auto Only, set_policy_type
    clears the home premium — and a save already in flight lands a moment
    later carrying new_homeowner_premium_annual.

    Accepted, that figure keeps feeding total_new_premium_annual, which is a
    GENERATED column built with coalesce. So it silently reaches the Meta
    CompleteRegistration event's value, while the home fields are hidden on
    screen and cannot show it. Money wrong, and invisible.

    REFUSED, NOT DROPPED. A 422 is noise for the broker in the race above —
    the value was cleared on purpose seconds earlier — but it is the only way
    a bug in our own UI, sending a field it should not, ever surfaces.
    Quietly writing nothing is what let this gap sit here unnoticed.

    NO POLICY TYPE YET means no rule to apply. A broker enters figures while
    still deciding, and refusing on an answer nobody has given would block the
    ordinary order of work.
    """
    if not policy_type:
        return

    keeping = PRODUCTS_FOR.get(policy_type, frozenset())
    for product, label in PRODUCT_LABEL.items():
        # Only what was actually SENT, and only a real figure: an explicit
        # null is a broker clearing a field, which must keep working whatever
        # the policy covers.
        #
        # BOTH HALVES OF THE PAIR (0106). A product now has a monthly column
        # and an annual one, and the trigger derives one from the other — so
        # checking only the annual name would let a stray monthly figure
        # through, and the trigger would promptly turn it into the annual
        # figure this guard exists to keep out of the Meta value.
        for side in ("current", "new"):
            for unit in ("annual", "monthly"):
                field = f"{side}_{product}_premium_{unit}"
                if product not in keeping and values.get(field) is not None:
                    raise HTTPException(
                        status_code=422,
                        detail=(
                            f"A {policy_type.lower()} policy has no {label} premium"
                        ),
                    )


def get_for_lead(lead_id: str) -> dict | None:
    """This lead's premium, or None if the broker has not entered any yet.

    None rather than 404: a lead with no premium is an ordinary, expected
    state — most leads have none — and the screen renders empty inputs for it.
    A 404 would make the caller treat "not filled in yet" as an error.
    """
    rows = repo.get_by_lead(lead_id).data
    return rows[0] if rows else None


def save_for_lead(lead_id: str, request: SavePremiumRequest) -> dict:
    """Create or update this lead's premium row.

    UPSERT, not POST, because the unique index on lead_id makes a second save
    for the same lead a conflict — and a broker filling these figures in over
    several conversations WILL save more than once.

    Safe under concurrency, the same shape as
    services/driver.py::get_or_create_principal: two tabs on one lead both see
    no row and both insert; idx_premium_history_lead_id_unique lets exactly
    one win, and the loser updates the winner's row instead of erroring. That
    is why the insert is caught rather than guarded by a prior existence
    check — a check-then-insert has a window between the two, an index does
    not.

    exclude_unset so a partial save leaves untouched fields alone while an
    explicit null clears one. Without it, saving just the auto premium would
    write nulls over the tenant figures entered ten minutes earlier.
    """
    # Checked explicitly rather than left to the insert's foreign-key
    # violation: PostgREST returns an empty list for a row that does not
    # exist, so indexing into it would raise IndexError and surface as a 500
    # before the FK ever had a chance to give us a clean 404.
    lead = lead_repo.get_by_id(lead_id).data
    if not lead:
        raise HTTPException(status_code=404, detail="Lead not found")

    values = request.model_dump(mode="json", exclude_unset=True)
    _refuse_products_outside_the_policy(lead[0].get("combined_policy_type"), values)
    existing = repo.get_by_lead(lead_id).data

    if not values:
        # Nothing sent — a no-op save is not an error. Hand back what is
        # already there, or create the empty row so the caller has an id to
        # patch into later.
        if existing:
            return existing[0]

    if existing:
        return _update(lead_id, values)

    try:
        repo.insert({**values, "lead_id": lead_id})
    except Exception as err:
        if UNIQUE_VIOLATION in str(err):
            # Lost the race — the row now exists, so apply this save to it.
            return _update(lead_id, values)
        raise db_error(
            err,
            on_foreign_key=HTTPException(
                status_code=404, detail="That lead no longer exists"
            ),
        )

    # Re-read rather than return the insert's own rows: the insert response
    # does not carry the generated columns, and the response model promises
    # them.
    return _reread_or_500(lead_id)


def _update(lead_id: str, values: dict) -> dict:
    if not values:
        return _reread_or_500(lead_id)

    try:
        repo.update(lead_id, values)
    except Exception as err:
        raise db_error(
            err,
            on_foreign_key=HTTPException(
                status_code=404, detail="That lead no longer exists"
            ),
        )

    return _reread_or_500(lead_id)


def _reread_or_500(lead_id: str) -> dict:
    """Read the row back with its generated columns.

    A miss here means the row vanished between writing and reading it, which
    is not an ordinary state and should not be reported as a clean 404 — that
    would tell the caller their lead does not exist when it plainly does.
    """
    rows = repo.get_by_lead(lead_id).data
    if not rows:
        logger.error("premium row for lead %s vanished immediately after a write", lead_id)
        raise HTTPException(status_code=500, detail="Something went wrong")
    return rows[0]
