"""Parses Meta's field_data shape (from meta_graph.fetch_lead_data) into our
fixed name/phone/email columns. Kept separate from services/lead.py — this
is Meta-specific parsing, not lead business logic.

Meta's field_data key names are NOT fully standardized across every form —
confirmed against Meta's own docs (full_name, email are the documented
standard keys) plus real-world variance (some forms split first_name/
last_name instead of full_name; the phone key is commonly phone_number).
This parser tries the known variants and never raises on a missing field —
see parse_field_data's docstring for why."""

# Known Meta standard field_data keys, checked in order. Meta's own retrieval
# guide shows "full_name" and "email" as the documented standard keys:
# https://developers.facebook.com/documentation/ads-commerce/marketing-api/guides/lead-ads/retrieving
_NAME_KEYS = ["full_name"]
_FIRST_LAST_NAME_KEYS = ("first_name", "last_name")
_PHONE_KEYS = ["phone_number", "phone"]
_EMAIL_KEYS = ["email"]

# The Ontario auto qualifying question. Read off real leads rather than
# guessed: Meta derives the key from the question text, so it carries the word
# "first" and keeps the question mark. A form whose wording is edited produces
# a DIFFERENT key and simply stops matching — the answer is still kept in
# extra_data, so the fix is to add the new key here, never to re-fetch.
_LICENSED_KEYS = ["when_were_you_first_licensed_in_canada?"]

# The two answers the form offers, in the form's own vocabulary. Anything else
# — a reworded option, a new third choice — maps to None rather than being
# forced into one of these: lead.licensed_in_canada has a check constraint, and
# a wrong-but-plausible value in a rating qualifier is worse than no value,
# because a broker would act on it.
_LICENSED_VALUES = {"2020_or_before", "2021_or_later"}

NOT_PROVIDED = "(not provided)"
# A syntactically valid but obviously fake address so a missing email never
# blocks lead creation (CreateLeadRequest.email is a strict EmailStr) while
# still being unmistakably a placeholder to anyone who sees it.
PLACEHOLDER_EMAIL = "not-provided@no-email.myday-crm.internal"


def _field_map(field_data: list[dict]) -> dict[str, str]:
    """Meta's field_data is a list of {"name": ..., "values": [...]} — values
    is always an array (supports multi-select questions) but every field we
    care about here is single-value. Flatten to {name: first_value}."""
    result = {}
    for field in field_data:
        values = field.get("values") or []
        if values:
            result[field.get("name", "")] = values[0]
    return result


def parse_field_data(field_data: list[dict]) -> dict:
    """Returns {"name": str, "phone": str, "email": str, "raw": dict} — raw
    is the flattened field map, always attached so nothing Meta sent is ever
    lost even when a standard field can't be found (see module docstring: we
    never drop a real lead over a parsing mismatch, we degrade to a visible
    placeholder and keep the raw data in the lead's extra_data)."""
    fields = _field_map(field_data)

    name = None
    for key in _NAME_KEYS:
        if fields.get(key):
            name = fields[key]
            break
    if name is None:
        first, last = fields.get(_FIRST_LAST_NAME_KEYS[0]), fields.get(_FIRST_LAST_NAME_KEYS[1])
        if first or last:
            name = " ".join(p for p in (first, last) if p)
    if not name:
        name = NOT_PROVIDED

    phone = next((fields[k] for k in _PHONE_KEYS if fields.get(k)), None) or NOT_PROVIDED

    email = next((fields[k] for k in _EMAIL_KEYS if fields.get(k)), None) or PLACEHOLDER_EMAIL

    # None, not a placeholder: website and phone leads never answer this, and
    # unlike name/phone/email it is nullable in the database, so there is no
    # NOT NULL to satisfy with a stand-in string.
    licensed = fields.get(next((k for k in _LICENSED_KEYS if fields.get(k)), ""), "")
    licensed_in_canada = licensed.strip() if licensed.strip() in _LICENSED_VALUES else None

    return {
        "name": name,
        "phone": phone,
        "email": email,
        "licensed_in_canada": licensed_in_canada,
        "raw": fields,
    }
