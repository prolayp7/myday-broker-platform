"""Business logic for the vehicle domain — routes stay thin, repositories stay
error-free.

A broker types these, and a DASH report can also add them
(services/dash_apply.py). The docstring here used to claim nothing seeds from
anywhere else, which stopped being true when DASH prefill returned.

A POLICY WITHOUT AN AUTO SIDE HAS NO VEHICLES. That rule is applied when the
policy type CHANGES — set_policy_type deletes the cars, after warning — and it
has to hold afterwards too, or the deletion a broker just confirmed is undone
by the Add vehicle button two clicks later.
"""

from fastapi import HTTPException

from db_errors import db_error
from repositories import driver as driver_repo
from repositories import lead as lead_repo
from repositories import vehicle as repo
from schemas.lead import PRODUCTS_FOR
from schemas.vehicle import VehiclePayload


def list_for_driver(driver_id: str) -> list[dict]:
    """Every vehicle on this driver's quote.

    No existence check on the driver: an unknown id and a driver with no cars
    both mean "nothing to show", and a 404 here would make the common empty
    case look like an error.
    """
    return repo.list_for_driver(driver_id).data or []


def get_vehicle(vehicle_id: str) -> dict:
    result = repo.get_by_id(vehicle_id)
    if not result.data:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return result.data[0]


def lead_insures_vehicles(lead_id: str) -> bool:
    """Does this lead's policy cover cars at all?

    THE QUESTION, separated from the refusal, because two callers need
    different answers to it: add_vehicle raises, and services/dash_apply.py
    skips. Asked in one place so the two cannot drift — the first version of
    this guard lived only inside add_vehicle, and DASH walked straight round
    it into the repository.

    TRUE WHEN THE LEAD IS MISSING OR HAS NO POLICY TYPE. A broker who has not
    chosen one yet is mid-quote, and refusing a car on an answer nobody has
    given would block the ordinary order of work — most add the car while they
    are still talking about it. An unknown lead is the insert's own
    foreign-key error to report, with a message written for it.
    """
    leads = lead_repo.get_by_id(lead_id).data or []
    if not leads:
        return True

    policy_type = leads[0].get("combined_policy_type")
    if not policy_type:
        return True
    return "auto" in PRODUCTS_FOR.get(policy_type, frozenset())


def _refuse_without_an_auto_side(driver_id: str) -> None:
    """Raise if this driver's lead is on a policy that insures no cars.

    TWO HOPS, because driver carries lead_id and the lead carries the policy
    type — the same shape services/dash.py uses to answer a different question
    about the same pair of rows.
    """
    drivers = driver_repo.get_by_id(driver_id).data or []
    if not drivers:
        return

    leads = lead_repo.get_by_id(str(drivers[0]["lead_id"])).data or []
    if not leads:
        return

    policy_type = leads[0].get("combined_policy_type")
    if policy_type and "auto" not in PRODUCTS_FOR.get(policy_type, frozenset()):
        raise HTTPException(
            status_code=422,
            detail=(
                f"A {policy_type.lower()} policy does not insure a vehicle — "
                "change the policy type first"
            ),
        )


def add_vehicle(driver_id: str, payload: VehiclePayload) -> dict:
    """Add a car to this driver's quote.

    Typically called with an empty payload: the row has to exist before any
    field can be saved against its id, so it is created first and filled in
    afterwards.

    REFUSED ON A POLICY WITH NO AUTO SIDE. The UI hides the control, and this
    is what makes it a rule rather than a suggestion: a stale tab, a second
    broker on the same lead, or anything calling the API directly would
    otherwise put back exactly the cars set_policy_type had just deleted with
    the broker's confirmation.
    """
    _refuse_without_an_auto_side(driver_id)

    values = payload.model_dump(exclude_unset=True, mode="json")
    values["driver_id"] = driver_id

    try:
        created = repo.insert(values)
    except Exception as err:
        # Without this mapping a bad driver_id surfaces as a raw 500 rather
        # than telling the caller what was actually wrong.
        raise db_error(
            err,
            on_foreign_key=HTTPException(status_code=404, detail="Driver not found"),
        )
    return created.data[0]


def update_vehicle(vehicle_id: str, payload: VehiclePayload) -> dict:
    """Patch a vehicle with only the fields actually sent.

    exclude_unset is load-bearing: the quote is filled in over a call, one
    answer at a time, and a request carrying just the parking arrangement must
    not blank out the ownership captured a minute earlier. Sending an explicit
    null still clears a field — that is a different intent from omitting it.
    """
    values = payload.model_dump(exclude_unset=True, mode="json")
    if not values:
        # Nothing to write. Return the row as-is rather than issuing an empty
        # UPDATE, which Postgres accepts but which would bump updated_at and
        # imply a change that never happened.
        return get_vehicle(vehicle_id)

    try:
        result = repo.update(vehicle_id, values)
    except Exception as err:
        raise db_error(err)

    if not result.data:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return result.data[0]


def delete_vehicle(vehicle_id: str) -> None:
    """Remove a vehicle from the quote — the customer sold it, or it was never
    theirs.

    404s on an unknown id rather than succeeding silently, so a UI that thinks
    it deleted something is never told it worked when nothing happened.
    """
    get_vehicle(vehicle_id)  # 404s if it does not exist

    try:
        repo.delete(vehicle_id)
    except Exception as err:
        raise db_error(err)
