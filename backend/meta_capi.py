"""Builds the payload Meta's Conversions API expects for one meta_signal row,
and hashes it. This is the OUTGOING side — the opposite direction from
meta_graph.py, which only ever fetches FROM Meta. Sending is a separate file
on purpose: different endpoint (a dataset's /events edge, not a plain object
GET), different credential (a system user token, not the page access token
meta_graph.py uses), different HTTP method.

Every function here is PURE: given the same lead/driver/premium data, it
always returns the same payload. Nothing in this file makes a network call —
that's deliberately left to a later file (the sender), so this one can be
fully unit-tested with no Meta credentials and no real dataset. Hash a known
input, check it against a known SHA-256 output; that's the whole test surface.

WHY HASH AT ALL: Meta receives the hash, not the raw value, and matches it
against its own hashed records of the same person — this is how CAPI protects
customer PII in transit while still letting Meta recognize who converted.
platform_leadgen_id is the ONE exception: Meta documents it as sent in the
clear, because it's already Meta's own opaque id for this submission, not a
piece of the customer's personal information."""

import hashlib
import logging
import os
import re
import time

import httpx

# Imported, not re-declared: this file used to carry its own copy with a
# comment asking whoever changed one to change the other. meta_graph.py owns
# the value now, so the two cannot disagree — Meta versions both directions of
# its API together, so they should move together.
import meta_graph as _meta_graph
from meta_graph import GRAPH_API_VERSION

# The FULL URL this module POSTs to, straight from the environment. Same three
# sources as meta_graph.py: META_CAPI_BASE, then the old META_CAPI_BASE_OVERRIDE,
# then built from the version.
#
# DELIBERATELY ITS OWN VARIABLE, not the one meta_graph.py reads. That one
# points at the local meta-lead-simulator stub for the INBOUND direction
# (Meta -> us, fetching a lead's submitted answers); sharing it would silently
# redirect every OUTBOUND CAPI send to a dev server that was never meant to
# receive them, with no error to signal the mistake — exactly what happened
# during this build's own first real test. The two must stay separable, which
# is why this is not folded into a single META_API_BASE.
CAPI_API_BASE = os.environ.get(
    "META_CAPI_BASE",
    os.environ.get(
        # The previous name, still honoured — see meta_graph.py for why the
        # old names keep working and why the default is not removed.
        "META_CAPI_BASE_OVERRIDE",
        f"https://graph.facebook.com/{GRAPH_API_VERSION}",
    ),
)

# Both bases can now be set independently, which reintroduces the drift that
# deduplicating GRAPH_API_VERSION removed: lead ingestion on v21.0 while
# conversions report on v23.0, with nothing to notice.
#
# A WARNING, NOT AN ERROR. Pointing the graph base at the local simulator
# while CAPI stays on real Meta is legitimate and is exactly what a developer
# .env does — refusing to start would break the normal dev setup. Silent when
# either base carries no version (a bare host like http://localhost:9090), so
# a simulator without one produces no noise.
def _version_in(base: str) -> str | None:
    match = re.search(r"/(v\d+\.\d+)/?$", base)
    return match.group(1) if match else None


_graph_version = _version_in(_meta_graph.GRAPH_API_BASE)
_capi_version = _version_in(CAPI_API_BASE)
if _graph_version and _capi_version and _graph_version != _capi_version:
    logging.getLogger("myday.meta").warning(
        "Meta API versions differ: lead ingestion is on %s (%s) but conversions "
        "are on %s (%s). Set both bases to the same version unless this is "
        "deliberate.",
        _graph_version,
        _meta_graph.GRAPH_API_BASE,
        _capi_version,
        CAPI_API_BASE,
    )

# RESOLVED 2026-08-03 by Meta's own CRM-integration setup guide, shown when
# creating the dataset: "Action_source field should always be
# 'system_generated'." The earlier 'other' default was a guess made while
# three sources disagreed, and it is wrong for this integration.
#
# Left as an env var rather than hardcoded so a future Meta change is a
# config edit, not a redeploy — but the default is now the documented value,
# so an environment that sets nothing is correct instead of subtly wrong.
ACTION_SOURCE = os.environ.get("META_CAPI_ACTION_SOURCE", "system_generated")

# This file was pure until check_token, so it had never needed one — the
# version-mismatch warning above builds its logger inline. A NameError here
# would defeat the point of a health check that promises never to raise, which
# is exactly how the test found it.
logger = logging.getLogger("myday.meta")


def _redact_token(message: str, token: str | None) -> str:
    """Take the access token out of anything Meta says back to us.

    Meta's reply to a bad credential quotes it in full ("Malformed access
    token EAA…"), and that message is stored on the signal row and shown on
    the admin screen. Any token long enough to be real is replaced; a short
    test value is left alone, since redacting three characters would blank
    ordinary words out of a message that has to stay readable.
    """
    if not token or len(token) < 12:
        return message
    return message.replace(token, "[token redacted]")


class MetaCapiError(Exception):
    """Meta's API itself rejected the event (a 400 with Meta's own error
    message) — e.g. event_time more than 7 days old, a malformed dataset id,
    an expired token. Distinct from a network/timeout failure, which raises
    a plain Exception, so the sender can tell a permanent rejection (do not
    bother retrying with the same payload) from a transient one (retry
    later)."""

    def __init__(self, reason: str, code: int | None = None):
        self.reason = reason
        # Meta's own numeric code, kept so the admin screen can tell an
        # EXPIRED TOKEN (190 — nothing wrong with the event, replace the token
        # and retry) from a rejected payload (resending cannot help). None when
        # Meta sent no code, which is rare but not worth guessing about.
        self.code = code
        super().__init__(reason)


def send_event(
    *,
    dataset_id: str,
    access_token: str,
    event_name: str,
    event_time: int,
    event_id: str,
    user_data: dict,
    custom_data: dict | None = None,
) -> dict:
    """POSTs ONE event to Meta's Conversions API. This is the only function
    in this file that makes a network call — everything above it is pure and
    already unit-tested without touching Meta.

    event_id is what Meta's own deduplication keys on; the caller passes
    meta_signal.id (already unique, already exists) rather than inventing a
    second identifier.

    Raises MetaCapiError on a Meta-side rejection (caller should mark the
    signal Failed and NOT blindly retry the identical payload — e.g. a stale
    event_time will fail again no matter how many times it's resent).
    Raises a plain Exception on network/timeout/5xx (caller should retry
    later — this class of failure is expected to clear on its own)."""
    body = {
        "data": [
            {
                "event_name": event_name,
                "event_time": event_time,
                "event_id": event_id,
                "action_source": ACTION_SOURCE,
                "user_data": user_data,
                **({"custom_data": custom_data} if custom_data else {}),
            }
        ]
    }

    # NO TEST-EVENT SWITCH, DELIBERATELY. META_CAPI_TEST_EVENT_CODE used to be
    # read here: when set, Meta routed the event to Events Manager's "Test
    # events" tab and EXCLUDED it from the dataset's real metrics. That earned
    # its place while the integration was being built and has no job now that
    # it is live and optimising against CompleteRegistration.
    #
    # It was removed because its failure mode is silent and expensive. Left set
    # in production — one debugging session, one copied environment — every
    # conversion stops counting, and nothing says so: Meta returns success, the
    # row is marked Sent, the dashboard shows green, the drain logs look
    # perfect. The only symptom is the campaign slowly starving weeks later,
    # with no reason to suspect the CRM.
    #
    # If testing against the Test Events tab is ever needed again, add it back
    # deliberately and guard it: a loud startup warning and a banner on the CAPI
    # dashboard, so "nothing is counting" can never be the quiet option.

    # Token goes in the Authorization header, NOT a query param — the same
    # rule meta_graph.py follows, and for the same reason: httpx logs request
    # URLs at INFO level, so a query-param token is written to every server log
    # line in cleartext. On Railway that is a retained, searchable log store.
    #
    # This file learned that the hard way. It sent the token as ?access_token=
    # from the day CAPI went live, so every conversion event logged the system
    # user token — long-lived, and able to write events into the dataset the
    # ad optimisation spends real money on. Meta accepts both forms; only one
    # of them keeps the credential out of the logs.
    response = httpx.post(
        f"{CAPI_API_BASE}/{dataset_id}/events",
        headers={"Authorization": f"Bearer {access_token}"},
        json=body,
        timeout=10.0,
    )

    # 401 AS WELL AS 400. A bad or revoked access token comes back as
    # 401 + code 190 (OAuthException), not 400 — found by sending with a
    # deliberately broken token on 2026-08-07. Reading only 400 meant that
    # response fell through to raise_for_status below, surfaced as a plain
    # Exception, and was treated as a TRANSIENT network failure: retried three
    # times, then given up on with no error_code recorded, so the admin screen
    # could not say "the token is the problem" about the one failure it was
    # built to name.
    if response.status_code in (400, 401):
        error = response.json().get("error", {})
        message = error.get("message", "Meta rejected this event")
        # error_user_msg / error_user_title carry Meta's more specific,
        # human-facing explanation of WHICH parameter was wrong — the plain
        # "message" field alone is often just "Invalid parameter", which
        # names nothing an admin (or a debugging session) can act on.
        detail = error.get("error_user_msg") or error.get("error_user_title")
        if detail:
            message = f"{message}: {detail}"
        # META ECHOES THE TOKEN BACK. Its reply to a bad credential is
        # literally "Malformed access token EAA…" — and this message is stored
        # in meta_signal.error_message and rendered on the admin screen, so
        # without this a live system user token would be written to the
        # database in the clear. Same class of leak as the query-param token
        # this file already learned about, arriving from the other direction.
        raise MetaCapiError(_redact_token(message, access_token), code=error.get("code"))

    response.raise_for_status()
    return response.json()


def _sha256(value: str) -> str:
    """Meta's own hashing contract: lowercase, trim leading/trailing
    whitespace, then SHA-256, returned as lowercase hex. Every user_data
    field below (except lead_id) goes through this exact sequence — Meta's
    matching is byte-exact on the hash, so a stray space or an uppercase
    letter before hashing silently breaks the match with no error from
    Meta's side to warn us."""
    return hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()


def hash_email(email: str | None) -> str | None:
    """em — lowercase, trimmed, then hashed. No other normalisation: Meta's
    own spec does not strip +tags or dots the way some mail providers do,
    so we don't invent normalisation Meta doesn't ask for.

    The emptiness check is on the TRIMMED value, not the raw one: "   " is
    truthy, so guarding on `email` alone let whitespace through to be hashed
    as sha256("") — a digest sent to Meta as though it were an address, and
    identical for every such lead. Every other hasher here already re-checks
    after normalising; this one did not."""
    if not email or not email.strip():
        return None
    return _sha256(email)


_NON_DIGITS = re.compile(r"\D+")


def hash_phone(phone: str | None, default_country_code: str = "1") -> str | None:
    """ph — digits only, then a country code prefix, THEN hashed. Meta's
    spec is explicit that the number must include the country code with no
    leading zeros or symbols before hashing.

    Our own phone column is free-text (e.g. "416-555-1234", "(416) 555
    1234") — this strips everything but digits, then prepends "1" (Canada/
    US) only if the result doesn't already start with it, so a broker who
    typed a number with or without the leading 1 both normalise to the
    same hash."""
    if not phone:
        return None
    digits = _NON_DIGITS.sub("", phone)
    if not digits:
        return None
    if not digits.startswith(default_country_code):
        digits = default_country_code + digits
    return _sha256(digits)


def hash_name_part(name_part: str | None) -> str | None:
    """fn / ln — lowercase, trimmed, punctuation removed, then hashed. Meta's
    spec calls for letters with no punctuation, so "O'Brien" and "OBrien"
    normalise the same way, matching however Meta's own reference data was
    hashed on their side.

    LETTERS, NOT ASCII. The filter is \\W (unicode-aware) rather than the
    [^a-zA-Z] this used to be: that ASCII-only class deleted accented
    characters outright, so "Núñez" hashed as "nez" and "Müller" as "mller" —
    a guaranteed mismatch against Meta's own copy of the name, and only ever
    for customers whose names carry accents. Meta asks for lowercase UTF-8
    with punctuation stripped; accented letters are letters and stay.

    Digits are removed too (\\W keeps them, so they are stripped separately)
    — a name is not a number, and "John2" should hash as "john"."""
    if not name_part:
        return None
    letters_only = re.sub(r"[\W\d_]", "", name_part, flags=re.UNICODE)
    if not letters_only:
        return None
    return _sha256(letters_only)


def split_name(full_name: str | None) -> tuple[str | None, str | None]:
    """driver.name is one field; Meta wants fn/ln separately. Splits on the
    LAST space — "Jean Paul Martin" becomes fn="Jean Paul", ln="Martin".
    Imperfect for some compound/multi-word surnames, but sending a wrong-ish
    split still hashes to SOMETHING Meta can attempt to match on; sending
    nothing at all is a guaranteed miss and a real loss to Event Match
    Quality. A single-word name (no space) is treated as first-name-only —
    ln stays None rather than guessing."""
    if not full_name or not full_name.strip():
        return None, None
    parts = full_name.strip().rsplit(" ", 1)
    if len(parts) == 1:
        return parts[0], None
    return parts[0], parts[1]


def hash_date_of_birth(dob) -> str | None:
    """db — Meta's required format is YYYYMMDD (no separators), then
    hashed. Accepts a date/datetime object (what Supabase returns) or an
    ISO string (what our own JSON layer sends around) so callers don't have
    to care which shape they're holding."""
    if dob is None:
        return None
    if isinstance(dob, str):
        digits_only = dob.replace("-", "")[:8]
    else:
        digits_only = dob.strftime("%Y%m%d")
    if len(digits_only) != 8 or not digits_only.isdigit():
        return None
    return _sha256(digits_only)


_GENDER_MAP = {"Male": "m", "Female": "f"}


def hash_gender(gender: str | None) -> str | None:
    """ge — Meta accepts a single letter, 'm' or 'f'. Our own column also
    allows 'Other' (see driver's check constraint) — there is no Meta
    equivalent for that value, so it is omitted rather than guessed. Omitting
    a field costs a small amount of match quality; sending a made-up value
    risks actively mismatching the person."""
    if gender not in _GENDER_MAP:
        return None
    return _sha256(_GENDER_MAP[gender])


def hash_city(city: str | None) -> str | None:
    """ct — Meta's spec, verbatim: "Lowercase only with no punctuation, no
    special characters, and no spaces."

    PUNCTUATION TOO, not just spaces. This stripped whitespace alone until
    2026-08-07, so "St. Catharines" hashed as "st.catharines" and matched
    nobody — Ontario has enough St./Ste.-prefixed and hyphenated names
    (Ste. Anne, Stouffville's official Whitchurch-Stouffville) for that to be
    a real loss rather than a technicality. A mismatch here is invisible: Meta
    accepts the event and simply fails to match, which is the class of bug
    meta_signal.payload exists to help catch.

    Non-ASCII letters are KEPT. \\W is unicode-aware here, so Montréal
    normalises to "montréal" rather than losing its accent — Meta asks for no
    punctuation, not for ASCII."""
    if not city:
        return None
    cleaned = re.sub(r"[\W_]+", "", city, flags=re.UNICODE)
    if not cleaned:
        return None
    return _sha256(cleaned)


# Ontario-only brokerage today (see broker.resident_province's own note) —
# this table exists so the rest of the code is not hardcoded to "always
# Ontario", the same reasoning driver.province already applies. Extend as
# real provinces appear in data.
_PROVINCE_CODES = {
    "ontario": "on",
    "quebec": "qc",
    "british columbia": "bc",
    "alberta": "ab",
    "manitoba": "mb",
    "saskatchewan": "sk",
    "nova scotia": "ns",
    "new brunswick": "nb",
    "newfoundland and labrador": "nl",
    "prince edward island": "pe",
    "northwest territories": "nt",
    "yukon": "yt",
    "nunavut": "nu",
}

# THE CODE ITSELF IS ALSO A VALID INPUT. The address lookup behind the New
# Policy screen fills driver.province with the two-letter code ("ON"), not the
# full name — so every real lead hashed to None and lost the `st` field
# entirely, while a hand-typed "Ontario" worked. Silent, and only visible by
# counting the fields on a sent event, which is how it was found (a browser
# test on 2026-08-07 produced 11 match fields instead of 12).
#
# Built from the table above rather than typed a second time: two lists of
# thirteen provinces would drift, and the failure mode is invisible.
_PROVINCE_CODES.update({code: code for code in set(_PROVINCE_CODES.values())})

# The only province this brokerage writes in. Used when the field is empty —
# see hash_province.
_DEFAULT_PROVINCE = "on"


def hash_province(province: str | None) -> str | None:
    """st — Meta wants the 2-letter ISO-3166-2 region code, lowercase. A
    plain string-slice ("Ontario"[:2] = "on") happens to work for Ontario
    but silently breaks for anything else ("Quebec"[:2] = "qu", not "qc") —
    this is a real lookup, not a slice, for exactly that reason. An
    unrecognised value is omitted rather than guessed.

    ONTARIO WHEN EMPTY. This is an Ontario-only brokerage, so a blank province
    is a field nobody filled in rather than a customer somewhere else — and
    omitting `st` costs match quality for a fact we already know. Same
    reasoning as `country`, which is hardcoded to "ca".

    A value that IS present but unrecognised is still omitted rather than
    coerced: that means the data is wrong, and quietly relabelling it Ontario
    would hide the problem behind a plausible-looking hash."""
    if not province:
        return _sha256(_DEFAULT_PROVINCE)
    code = _PROVINCE_CODES.get(province.strip().lower())
    if not code:
        return None
    return _sha256(code)


def hash_postal_code(postal_code: str | None) -> str | None:
    """zp — lowercase, spaces removed, then hashed. Canadian postal codes
    are commonly typed with a space ("M5V 2T6") — Meta's own examples are
    for US zips, but the same "no formatting characters" rule applies."""
    if not postal_code:
        return None
    no_spaces = re.sub(r"\s+", "", postal_code)
    if not no_spaces:
        return None
    return _sha256(no_spaces)


def _unique(*hashes: str | None) -> list[str] | None:
    """The distinct hashes given, in order, or None if there are none.

    Deduplicated AFTER hashing rather than before: the applicant's contact is
    usually copied from the lead, so the two are most often the same value and
    sending it twice would be noise. Comparing hashes rather than the raw
    values means "A@B.com" and "a@b.com " collapse correctly, since _sha256
    lowercases and trims.

    None, not [], for an empty result — build_user_data drops None keys, and
    Meta scores a present-but-empty field worse than an absent one.
    """
    seen = [h for h in hashes if h]
    return list(dict.fromkeys(seen)) or None


def build_user_data(*, lead: dict, driver: dict | None) -> dict:
    """Assembles Meta's user_data object for one event. lead supplies
    contact info and the one unhashed identifier; driver (the PRINCIPAL —
    the applicant, whoever filled Meta's form) supplies the demographic
    fields that lift Event Match Quality from ~5.5-6.5 (email only) toward
    ~8 (email + phone + name + DOB + gender + location).

    driver may be None (a signal fired before the applicant record was ever
    opened, or a lead with no driver at all) — every driver-sourced field is
    then simply absent, not an error. Absent fields are OMITTED from the
    returned dict entirely (not sent as null), since Meta's own docs treat a
    present-but-empty field as worse than an absent one for matching
    purposes.
    """
    # THE DRIVER'S NAME IS PREFERRED — it is the applicant the broker
    # confirmed on the call, and may not even be the person who enquired.
    #
    # The lead's name is the FALLBACK, and it matters: a Lead signal is queued
    # when the lead row is inserted (migration 0079) and sent by the drain up
    # to 15 minutes later, so there is usually no driver yet — while lead.name
    # has held the value the whole time. Only 4 of 35 sent Lead events carried
    # a name before this, against 35 of 35 carrying email and phone, and Meta
    # lists name among the four parameters that most improve matching.
    #
    # Not the same shape as em/ph below, which send BOTH values as a
    # deduplicated array (see _unique). One fn/ln pair is one person's name,
    # so this genuinely is a precedence choice rather than a merge.
    # .strip() before choosing, so a driver row holding only spaces does not
    # win over a real lead name: a whitespace string is truthy, so " " would
    # otherwise suppress the fallback AND hash to nothing, losing the name
    # entirely rather than falling back to the one we have.
    driver_name = ((driver or {}).get("name") or "").strip()
    source_name = driver_name or (lead.get("name") or "").strip()

    # THE CONFIRMED COLUMNS WIN. driver.first_name / last_name are the split a
    # broker can see and correct on the applicant screen; split_name is the
    # fallback for rows that predate them, and it is the reason they exist —
    # it splits on the LAST SPACE, so DASH's "CHEEMA,SAJIDA" became
    # fn="CHEEMA,SAJIDA" with no ln at all, and hash_name_part then stripped
    # the comma into "cheemasajida", a fused token matching nobody.
    #
    # NO CHANGE TO THE PARAMETER SET: the same fn and ln go to Meta. Only
    # where they come from changes. middle_name is never sent — Meta has no
    # parameter for it, and its whole job is keeping a middle name OUT of fn.
    confirmed_first = ((driver or {}).get("first_name") or "").strip()
    confirmed_last = ((driver or {}).get("last_name") or "").strip()
    if confirmed_first or confirmed_last:
        fn, ln = confirmed_first or None, confirmed_last or None
    else:
        fn, ln = split_name(source_name) if source_name else (None, None)

    user_data = {
        # THE ONE FIELD SENT UNHASHED. Meta's own 15-17 digit id for this
        # exact form submission — an exact match, where every field above
        # is only ever probabilistic.
        "lead_id": lead.get("platform_leadgen_id"),
        # Arrays, not bare strings: Meta's CRM payload example sends em and ph
        # as lists (a person can have several of each). The API accepts a bare
        # string too, but matching the documented shape removes one variable
        # if match quality ever needs debugging.
        # BOTH the lead's contact and the applicant's, deduplicated after
        # hashing. Meta accepts these as arrays precisely because a person has
        # several, and tries every one — so an extra correct value can only
        # help the match.
        #
        # Only the LEAD's was sent until 2026-08-07, which meant a broker
        # correcting a typo on the applicant screen changed nothing about what
        # Meta received. The two are genuinely different facts: the lead
        # carries what the customer typed into Meta's form, the applicant what
        # the broker confirmed on the call, and the applicant may not even be
        # the same person (someone enquiring for a spouse).
        "em": _unique(
            hash_email(lead.get("email")),
            hash_email(driver.get("email")) if driver else None,
        ),
        "ph": _unique(
            hash_phone(lead.get("phone")),
            hash_phone(driver.get("phone")) if driver else None,
        ),
        "external_id": _sha256(lead["id"]) if lead.get("id") else None,
        "fn": hash_name_part(fn),
        "ln": hash_name_part(ln),
        "db": hash_date_of_birth(driver.get("date_of_birth")) if driver else None,
        "ge": hash_gender(driver.get("gender")) if driver else None,
        "ct": hash_city(driver.get("city")) if driver else None,
        "st": hash_province(driver.get("province")) if driver else None,
        "zp": hash_postal_code(driver.get("postal_code")) if driver else None,
        # Ontario-only brokerage today — same constant every event, not read
        # from data.
        #
        # HASHED, like every other customer field. This was sent in the CLEAR
        # until 2026-08-07, on the belief that Meta's examples show it plain
        # and that only identifying values are hashed. Meta's customer
        # information parameters page lists country among the hashing-required
        # fields, and a test here ASSERTED the unhashed form — so the mistake
        # was locked in by the thing that should have caught it.
        "country": _sha256("ca"),
    }
    return {k: v for k, v in user_data.items() if v is not None}


def build_custom_data(*, total_new_premium: float | None) -> dict:
    """Assembles custom_data. NEVER empty: the Conversion Leads integration
    requires event_source and lead_event_source on EVERY event, not only on
    the ones carrying a value. Meta's setup guide is explicit — "Event_source
    field should always be 'crm'" — and this is one of the places the CRM
    integration's parameters differ from the web Conversions API those docs
    warn about.

    Returning {} for a Lead or CompleteRegistration (as this did while
    custom_data was thought to be a valued-event-only concern) made send_event
    drop the key entirely, so two required fields never reached Meta.

    value/currency are REQUIRED by Meta on a valued event (not optional), so a
    CompleteRegistration with no premium figure is a real gap, not a stylistic
    choice; the sender treats that case as a terminal failure rather than
    sending an incomplete event."""
    custom_data = {
        # Constant, per Meta's spec — identifies this as CRM-sourced rather
        # than a web/app event.
        "event_source": "crm",
        # The name of our CRM, which is what Meta asks for here (their
        # examples use "HubSpot", "Salesforce", ...).
        "lead_event_source": "MyDay CRM",
    }
    if total_new_premium is not None:
        custom_data["value"] = float(total_new_premium)
        custom_data["currency"] = "CAD"
    return custom_data


def check_token() -> dict:
    """Ask Meta whether our access token is still good.

    PROACTIVE, unlike everything else in this file. Every other signal that
    something is wrong arrives as a FAILED CONVERSION — by which point a lead
    has already gone unreported. This asks directly, so a revoked or
    misconfigured token is visible before it costs anything.

    NOT REALLY ABOUT EXPIRY, despite the obvious name. The token in use is a
    SYSTEM USER token, which Meta issues with expires_at = 0 (never). What
    does happen: an admin revokes it, the app gets restricted, the system user
    is removed, or the scopes are wrong for what we send. is_valid covers all
    of those, and expires_at is reported anyway for the day someone swaps in a
    token that does expire.

    Never raises. This feeds a status strip on an admin screen, and a health
    check that takes the page down with it is worse than one that says it
    could not tell — the caller gets ok=False and Meta's own words.
    """
    token = os.environ.get("META_CAPI_ACCESS_TOKEN")
    dataset_id = os.environ.get("META_CAPI_DATASET_ID")
    if not token or not dataset_id:
        return {
            "ok": False,
            "reason": "META_CAPI_ACCESS_TOKEN / META_CAPI_DATASET_ID are not configured",
            "dataset_id": dataset_id,
        }

    try:
        # The token debugs ITSELF: input_token is what we are asking about,
        # access_token is the credential for asking. Meta accepts the same
        # value for both, which avoids needing a second app token here.
        response = httpx.get(
            f"{CAPI_API_BASE}/debug_token",
            params={"input_token": token, "access_token": token},
            timeout=10.0,
        )
    except Exception as err:
        # A network failure says nothing about the token. Reported as its own
        # state rather than as "the token is bad", which would send an admin
        # to replace a credential that was fine.
        logger.warning("Could not reach Meta to check the token: %s", err)
        return {"ok": False, "reason": f"Could not reach Meta: {err}", "dataset_id": dataset_id}

    if response.status_code != 200:
        error = (response.json() or {}).get("error", {})
        return {
            "ok": False,
            "reason": error.get("message", f"Meta returned {response.status_code}"),
            "error_code": error.get("code"),
            "dataset_id": dataset_id,
        }

    data = (response.json() or {}).get("data", {})
    return {
        "ok": bool(data.get("is_valid")),
        "reason": None if data.get("is_valid") else "Meta reports this token is not valid",
        "type": data.get("type"),
        # 0 means "never expires", which every system user token reports. Sent
        # through as None so the screen does not have to know that.
        "expires_at": data.get("expires_at") or None,
        "data_access_expires_at": data.get("data_access_expires_at") or None,
        "scopes": data.get("scopes") or [],
        "app_id": data.get("app_id"),
        "dataset_id": dataset_id,
    }
