"""Data access for the property table. Only talks to Supabase."""

from db import supabase

# Every column the quote screen needs. Not select * — the table will grow
# columns the broker screen has no use for.
_PROPERTY_SELECT = (
    "id, driver_id, property_role, property_type, "
    "street_address, city, province, postal_code, "
    "insured_since, insured_with_property_since, structure, year_built, notes, "
    "stories, full_baths, half_baths, number_of_mortgages, finished_percent, "
    "electrical_year, plumbing_year, heating_year, roof_year, water_heater_year, "
    "burglar_alarm, smoke_detectors, fire_extinguishers, sump_type, "
    "contents_value, created_at, updated_at"
)


def list_for_driver(driver_id: str):
    """This applicant's properties, oldest first.

    Ordered by creation so the list does not reshuffle while a broker is
    editing it — a row changing position mid-call is disorienting. The Primary
    is created before any rental by definition, so this also puts it first
    without needing to sort on property_role.
    """
    return (
        supabase.table("property")
        .select(_PROPERTY_SELECT)
        .eq("driver_id", driver_id)
        .order("created_at")
        .execute()
    )


def get_by_id(property_id: str):
    return (
        supabase.table("property")
        .select(_PROPERTY_SELECT)
        .eq("id", property_id)
        .limit(1)
        .execute()
    )


def get_primary(driver_id: str):
    """The applicant's own residence, or an empty result if the quote is auto
    only. At most one exists per driver — enforced by
    idx_property_one_primary_per_driver (0052)."""
    return (
        supabase.table("property")
        .select(_PROPERTY_SELECT)
        .eq("driver_id", driver_id)
        .eq("property_role", "Primary")
        .limit(1)
        .execute()
    )


def insert(values: dict):
    return supabase.table("property").insert(values).execute().data


def update(property_id: str, values: dict):
    return (
        supabase.table("property")
        .update(values)
        .eq("id", property_id)
        .execute()
    )


def delete(property_id: str):
    return supabase.table("property").delete().eq("id", property_id).execute()


def delete_for_driver(driver_id: str):
    """Every property on this applicant's quote.

    For the policy-type flow switching down to Standalone, where losing all of
    them is the intent rather than an accident.
    """
    return supabase.table("property").delete().eq("driver_id", driver_id).execute()


def delete_rentals_for_driver(driver_id: str):
    """The investment properties, leaving the Primary.

    For switching Homeowners/Condo -> Tenants: a tenant has no sub-properties,
    but their own residence is still on the quote.
    """
    return (
        supabase.table("property")
        .delete()
        .eq("driver_id", driver_id)
        .neq("property_role", "Primary")
        .execute()
    )
