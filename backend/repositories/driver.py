"""Data access for the driver table. Only talks to Supabase."""

from db import supabase

# Every column the intake form needs. Not select * — the table will grow
# columns the broker screen has no use for.
_DRIVER_SELECT = (
    "id, lead_id, is_principal, relationship_to_applicant, "
    "name, first_name, middle_name, last_name, "
    "phone, email, date_of_birth, gender, marital_status, "
    "license_number, license_province, g1_date, g2_date, g_date, "
    "renewal_date, insured_since, current_insurer_since, "
    "street_address, unit_number, city, province, postal_code, "
    # Written only by the DASH upload path, never by a broker. Listed here
    # because this select is explicit — a column missing from it reaches no
    # screen, and the Download button would stay disabled with the path
    # sitting in the database.
    "dash_report_path, "
    # THE QUALIFICATION FACTS (0114). at_fault_claims_6y comes from the report's
    # own header; convictions_3y and licensed_5y_plus are the broker's answers
    # in the qualify dialog, which reads them back from here.
    "at_fault_claims_6y, convictions_3y, licensed_5y_plus, "
    "created_at, updated_at"
)


def get_principal(lead_id: str):
    """The lead's applicant row, or an empty result if it has none yet."""
    return (
        supabase.table("driver")
        .select(_DRIVER_SELECT)
        .eq("lead_id", lead_id)
        .eq("is_principal", True)
        .limit(1)
        .execute()
    )


def insert_principal(
    lead_id: str,
    name: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    first_name: str | None = None,
    middle_name: str | None = None,
    last_name: str | None = None,
):
    """Create the applicant row for a lead, seeded with their contact details.

    name/phone/email come from the LEAD (the service reads it and passes them
    down — this repository only ever touches its own table). They are a
    starting point for the broker to confirm or correct on the call, not a
    verified record, and they are written ONCE here: nothing re-seeds them
    later, so a broker who clears a field finds it still cleared.

    Can raise a unique-violation (23505) when two requests race — the partial
    unique index idx_driver_one_principal_per_lead is what guarantees a lead
    can never end up with two applicants. The service treats that violation as
    "someone else just created it" and re-reads, so the race resolves silently
    rather than surfacing as an error.
    """
    return (
        supabase.table("driver")
        .insert(
            {
                "lead_id": lead_id,
                "is_principal": True,
                # The applicant IS the applicant — the one relationship value
                # that is knowable at creation.
                "relationship_to_applicant": "Applicant",
                "name": name,
                # The split OF that name. Meta hashes fn and ln separately, so
                # the parts have to exist as columns; `name` stays the printed
                # form the broker compares against a document.
                "first_name": first_name,
                "middle_name": middle_name,
                "last_name": last_name,
                "phone": phone,
                "email": email,
            }
        )
        .execute()
    )


def list_for_lead(lead_id: str):
    """Every driver on a lead — the applicant and any additional drivers.

    Ordered applicant-first, then oldest-created: that is the order the UI
    shows its driver tabs in, and it must not shuffle when a driver is edited.
    Sorting on the client instead would mean two places deciding tab order.
    """
    return (
        supabase.table("driver")
        .select(_DRIVER_SELECT)
        .eq("lead_id", lead_id)
        .order("is_principal", desc=True)
        .order("created_at")
        .execute()
    )


def insert_additional(lead_id: str, relationship: str, values: dict | None = None):
    """Create an additional (non-applicant) driver — a spouse, a child.

    No uniqueness to worry about: idx_driver_one_principal_per_lead only
    constrains principal rows, so a lead may have any number of these, and two
    children with the same relationship is a normal household, not a mistake.

    `values` carries whatever a DASH import already knows about them (a name, a
    licence number). Written in the INSERT rather than by a second update call
    so the row is never briefly visible as a nameless driver — a broker
    watching the panel would see one appear and then fill itself in.
    """
    return (
        supabase.table("driver")
        .insert(
            {
                **(values or {}),
                "lead_id": lead_id,
                "is_principal": False,
                "relationship_to_applicant": relationship,
            }
        )
        .execute()
    )


def get_by_id(driver_id: str):
    return (
        supabase.table("driver")
        .select(_DRIVER_SELECT)
        .eq("id", driver_id)
        .limit(1)
        .execute()
    )


def delete(driver_id: str):
    """Remove a driver row."""
    return supabase.table("driver").delete().eq("id", driver_id).execute()


def update(driver_id: str, values: dict):
    """Patch a driver. `values` is already validated and stripped of unset keys
    by the service — this writes exactly what it is given."""
    return (
        supabase.table("driver")
        .update(values)
        .eq("id", driver_id)
        .execute()
    )

