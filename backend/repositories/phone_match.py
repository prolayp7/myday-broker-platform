"""Finding a lead by phone number. Only talks to Supabase.

THREE PLACES HOLD A CUSTOMER PHONE, and a call has to be findable by any of
them:

  lead.phone_e164       what the ad captured. Always present, never verified.
  driver.phone_e164     what the broker confirmed on the call — the number that
                        actually reaches the person, and the one that differs
                        when the ad's was wrong.
  lead_phone.phone_e164 any OTHER number they have rung from, once a broker
                        matched a call from it by hand (0063).

Searching only the lead would miss every customer whose number was corrected —
exactly the ones already spoken to. All three columns are normalised to E.164
by triggers (0058), so these are plain equality lookups against an index, not
pattern matching.
"""

from db import supabase


def leads_by_phone(phone_e164: str) -> list[dict]:
    """Every lead reachable on this number, by any of the three routes.

    Returns one dict per DISTINCT lead: {"id", "assigned_broker_id"}. The
    caller counts leads, never rows — a lead and its own applicant sharing a
    number is the normal case (driver.phone is seeded from lead.phone), so
    counting rows would call almost every match ambiguous.

    An empty string or None returns nothing rather than matching everything:
    normalize_phone_e164 returns null for the "(not provided)" placeholder
    lead.phone really does carry, and a blank lookup here would otherwise
    collide with every such lead at once.
    """
    if not phone_e164:
        return []

    # Keyed by lead id, so the same lead found twice collapses to one entry.
    found: dict[str, dict] = {}

    direct = (
        supabase.table("lead")
        .select("id, assigned_broker_id")
        .eq("phone_e164", phone_e164)
        .execute()
    )
    for row in direct.data or []:
        found[row["id"]] = row

    # A driver hit resolves to that driver's lead. The embed pulls the lead's
    # assigned_broker_id in the same round trip — the broker tiebreaker needs
    # it, and fetching it separately would be a second query per driver.
    drivers = (
        supabase.table("driver")
        .select("lead_id, lead(id, assigned_broker_id)")
        .eq("phone_e164", phone_e164)
        .execute()
    )
    for row in drivers.data or []:
        lead = row.get("lead")
        if lead:
            found.setdefault(lead["id"], lead)

    extra = (
        supabase.table("lead_phone")
        .select("lead_id, lead(id, assigned_broker_id)")
        .eq("phone_e164", phone_e164)
        .execute()
    )
    for row in extra.data or []:
        lead = row.get("lead")
        if lead:
            found.setdefault(lead["id"], lead)

    return list(found.values())


def normalize(raw: str | None) -> str | None:
    """Put a raw number into E.164 using the SAME function the triggers use.

    Called through the database rather than reimplemented in Python on purpose:
    a matcher normalising one side of the comparison by even slightly different
    rules than the trigger normalised the other is a bug that presents as "some
    calls just don't match", and it would be invisible in tests written against
    the Python version.
    """
    if not raw:
        return None
    result = supabase.rpc("normalize_phone_e164", {"raw": raw}).execute()
    return result.data or None
