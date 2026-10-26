"""Data access for the vehicle table. Only talks to Supabase."""

from db import supabase

# Every column the quote screen needs. Not select * — the table will grow
# columns the broker screen has no use for.
_VEHICLE_SELECT = (
    "id, driver_id, vin, year_make_model, "
    "condition, purchase_date, km_at_purchase, ownership, finance_company, "
    "annual_km, one_day_distance_km, parking_at_night, "
    "winter_tires, anti_theft, coverage_type, "
    "created_at, updated_at"
)


def list_for_driver(driver_id: str):
    """This driver's vehicles, oldest first.

    Ordered by creation so the list does not reshuffle while a broker is
    editing it — a row changing position mid-call is disorienting, and a
    household's cars have no more meaningful order to impose.
    """
    return (
        supabase.table("vehicle")
        .select(_VEHICLE_SELECT)
        .eq("driver_id", driver_id)
        .order("created_at")
        .execute()
    )


def get_by_id(vehicle_id: str):
    return (
        supabase.table("vehicle")
        .select(_VEHICLE_SELECT)
        .eq("id", vehicle_id)
        .limit(1)
        .execute()
    )


def insert(values: dict):
    """Create a vehicle. `values` is already validated by the service.

    Can raise a foreign-key violation (23503) when driver_id names a driver
    that does not exist; the service translates that into a 404.
    """
    return supabase.table("vehicle").insert(values).execute()


def update(vehicle_id: str, values: dict):
    """Patch a vehicle with exactly the fields given — the service has already
    stripped anything the caller did not send."""
    return supabase.table("vehicle").update(values).eq("id", vehicle_id).execute()


def delete(vehicle_id: str):
    """Remove a vehicle row."""
    return supabase.table("vehicle").delete().eq("id", vehicle_id).execute()


def list_for_drivers(driver_ids: list[str]):
    """Every vehicle belonging to any of these drivers.

    Takes a LIST because vehicles hang off the DRIVER, not the lead, and a
    household's cars are commonly split across the applicant and a spouse.
    Asking only about the applicant undercounts — see delete_for_drivers.
    """
    if not driver_ids:
        return None
    return (
        supabase.table("vehicle")
        .select(_VEHICLE_SELECT)
        .in_("driver_id", driver_ids)
        .order("created_at")
        .execute()
    )


def delete_for_drivers(driver_ids: list[str]):
    """Remove every vehicle belonging to any of these drivers.

    For the policy-type flow switching to a property-only quote, where there
    is no auto policy left for a vehicle to belong to.

    EVERY DRIVER, not just the applicant. A spouse's car is still on the
    quote, and clearing only the applicant's leaves it orphaned on a policy
    with no auto side — invisible on screen, since the vehicle section reads
    the applicant's.
    """
    if not driver_ids:
        return None
    return supabase.table("vehicle").delete().in_("driver_id", driver_ids).execute()
