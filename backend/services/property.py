"""Business logic for the property domain — the homes on a combined quote.

Owns every rule about what may exist. Three of them matter:

  * A Primary is created by the POLICY-TYPE flow (services/lead.set_policy_type),
    never added here. It is the applicant's own residence, so its existence
    follows from "this quote is combined", not from a broker deciding to add a
    home.
  * A Tenant applicant has no investment properties. Someone renting where they
    live may in reality own a rental, but the intake does not cover it, and a
    row with no form behind it is worse than no row.
  * A Homeowner or Condo applicant may add either kind of rental — a condo
    owner renting out another condo is an ordinary case.

Reads driver's own repository to answer "who is the applicant" and to seed the
Primary's address. Services may import other domains' repositories (established
by broker.py, broker_group.py, lead_form.py); what they must not do is import
another domain's service and inherit its HTTP decisions.
"""

import logging

from fastapi import HTTPException

from db_errors import db_error
from repositories import driver as driver_repo
from repositories import property as repo
from schemas.property import AddPropertyRequest, PropertyPayload

logger = logging.getLogger("myday.property")

# Which primary types may hold investment properties. A Tenant may not — see
# the module docstring.
_CAN_HOLD_RENTALS = {"Homeowner", "Condo"}


def list_for_driver(driver_id: str) -> list[dict]:
    """Every property on this applicant's quote.

    No existence check on the driver: an unknown id and an auto-only quote both
    mean "nothing to show", and a 404 here would make the ordinary empty case
    look like an error. Same rule as vehicle.
    """
    return repo.list_for_driver(driver_id).data or []


def get_property(property_id: str) -> dict:
    result = repo.get_by_id(property_id)
    if not result.data:
        raise HTTPException(status_code=404, detail="Property not found")
    return result.data[0]


def create_primary(driver_id: str, property_type: str) -> dict:
    """The applicant's own residence, created when the quote becomes combined.

    Called by the policy-type flow, not by a route.

    A TYPE CHANGE REPLACES THE ROW. Re-typing it in place looks tidier and is
    wrong: the three intakes ask different questions, so a row carried across
    keeps answers the new form never asks. A tenant record would still hold
    "2 stories, roof done 2019" — invisible on screen, present in the database,
    and describing a building the customer does not own. Switch back and those
    stale answers reappear as though freshly collected, possibly against an
    address that has changed in between.

    Same type is a no-op — the existing row is returned untouched, so nothing
    is lost by re-picking the value already set.

    The caller warns first: services/lead.set_policy_type refuses with a 409
    naming what the switch discards, exactly as it does for rentals.

    NO ADDRESS OF ITS OWN. The primary residence is where the applicant lives,
    which the driver row already records — so the screen reads it from there
    rather than this table holding a second copy. Change it on the auto side
    and the property follows, because they were never two separate facts.
    Rentals are different buildings and do carry their own.
    """
    existing = repo.get_primary(driver_id)
    if existing.data:
        current = existing.data[0]
        if current["property_type"] == property_type:
            return current
        # Replaced, not re-typed. Deleted first so the partial unique index
        # never sees two Primaries for one driver.
        repo.delete(current["id"])

    # Checked explicitly rather than left to the insert's foreign-key
    # violation, so an unknown driver gives a clean 404 instead of a 500.
    if not driver_repo.get_by_id(driver_id).data:
        raise HTTPException(status_code=404, detail="Driver not found")

    # NO ADDRESS. The Primary is where the applicant lives, and that address is
    # already on the driver row — copying it here would be the same fact in two
    # places, free to disagree the moment either is corrected. The screen reads
    # the applicant's directly. Only rentals, which are different buildings,
    # carry an address of their own.
    values = {
        "driver_id": driver_id,
        "property_role": "Primary",
        "property_type": property_type,
    }

    try:
        created = repo.insert(values)
    except Exception as err:
        # A concurrent call — two tabs, or a double-click — lost the race for
        # the partial unique index. The winner's row is the answer, so re-read
        # rather than failing: an index closes the window a check-then-insert
        # would leave open.
        existing = repo.get_primary(driver_id)
        if existing.data:
            return existing.data[0]
        raise db_error(
            err,
            on_foreign_key=HTTPException(status_code=404, detail="Driver not found"),
        )

    return created[0]


def add_property(driver_id: str, request: AddPropertyRequest) -> dict:
    """Add an investment property to the applicant's quote.

    Refuses three things, each because the row would otherwise describe
    something the rest of the system says cannot exist.
    """
    if request.property_role == "Primary":
        raise HTTPException(
            status_code=400,
            detail=(
                "The applicant's own home is added by choosing a policy type, "
                "not from here"
            ),
        )

    primary = repo.get_primary(driver_id).data
    if not primary:
        raise HTTPException(
            status_code=400,
            detail="Choose a policy type before adding properties",
        )
    if primary[0]["property_type"] not in _CAN_HOLD_RENTALS:
        raise HTTPException(
            status_code=400,
            detail="A tenant policy covers one home — additional properties can't be added",
        )

    values = request.model_dump(mode="json")
    values["driver_id"] = driver_id

    try:
        created = repo.insert(values)
    except Exception as err:
        raise db_error(
            err,
            on_foreign_key=HTTPException(status_code=404, detail="Driver not found"),
        )

    return created[0]


def update_property(property_id: str, payload: PropertyPayload) -> dict:
    """Patch a property with only the fields sent.

    exclude_unset, so omitting a field leaves it alone while an explicit null
    clears it — the distinction a form filled one answer at a time depends on.
    """
    values = payload.model_dump(exclude_unset=True, mode="json")
    if not values:
        # Nothing to write. Returning the row unchanged is friendlier than a
        # 400 for a request that simply asked for nothing.
        return get_property(property_id)

    try:
        result = repo.update(property_id, values)
    except Exception as err:
        raise db_error(err)

    # PostgREST returns an empty list for an update that matched nothing.
    if not result.data:
        raise HTTPException(status_code=404, detail="Property not found")

    return result.data[0]


def delete_property(property_id: str) -> None:
    """Remove an investment property.

    The PRIMARY is never deletable here: it exists because the quote is
    combined, so it goes by changing the policy type — the same shape as the
    applicant driver, who is removed by deleting the lead, not by a button.
    Deleting it from this route would leave a combined quote with no home on
    it and nothing saying why.
    """
    existing = get_property(property_id)
    if existing["property_role"] == "Primary":
        raise HTTPException(
            status_code=400,
            detail="The applicant's own home can't be removed — change the policy type instead",
        )

    try:
        repo.delete(property_id)
    except Exception as err:
        raise db_error(err)
