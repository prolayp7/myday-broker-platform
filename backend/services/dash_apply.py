"""Writes a parsed DASH report onto the broker's form.

ONE RULE, AND IT IS THE WHOLE POINT: never overwrite the broker.

DASH fills what is empty and leaves everything else exactly as typed. That is
what makes this safe to offer as an OPTION beside typing rather than a
replacement for it — a broker who corrected a licence number on the phone
keeps their correction, and a report uploaded afterwards cannot silently undo
it. Without that rule the feature is a liability: the broker's own knowledge
loses to a document, quietly, with nothing on screen to say so.

Returns what was filled and what was skipped, so the caller can say "6 fields
filled, 2 already had values" rather than leaving the broker to compare the
form against the PDF.

AND WHOSE REPORT IT WAS. Nothing here refuses a report for the wrong person —
that is the broker's call. But "fill blanks only" is no protection when the
fields ARE blank, so the subject's name and licence come back for the screen
to state plainly. See _result().
"""

import logging

import claude_client
from repositories import driver as driver_repo
from repositories import vehicle as vehicle_repo
from services import dash_parse
from services import vehicle as vehicle_service

logger = logging.getLogger("myday.dash")

# Everything the parser can produce for a driver. Spelled out rather than
# taking whatever the parser returns: a field added to dash_parse should be a
# deliberate decision here too, not something that starts writing to the
# database on its own.
# THE ONLY FIELDS DASH IS ALLOWED TO REPLACE. Everything else in
# _DRIVER_FIELDS keeps the never-overwrite rule below.
#
# These four are seeded automatically at applicant creation from the lead
# form's name — nobody typed them, so keeping them would mean preferring a
# lead-form guess over the licensing record. The report is the better source:
# a lead form gives "Sajida", DASH gives "CHEEMA,SAJIDA" with the surname.
#
# A broker's correction is still safe. They correct AFTER the report is
# uploaded, and nothing re-parses a report on its own.
_SEEDED_NAME_FIELDS = ("name", "first_name", "middle_name", "last_name")

_DRIVER_FIELDS = (
    "name",
    # The split of the name above, and what Meta actually hashes. Listed
    # here for the reason this tuple exists at all: a field the parser can
    # produce but this list omits is written nowhere and fails silently.
    "first_name",
    "middle_name",
    "last_name",
    # ADDED LATE, and its absence is worth remembering. dash_parse gained
    # full-date-of-birth support with a test proving it PARSES one — which
    # passed while this list dropped the value, so the feature did nothing and
    # looked healthy. The test that catches it asserts on the update, not the
    # parse.
    "date_of_birth",
    "license_number",
    "gender",
    "marital_status",
    "street_address",
    "city",
    "province",
    "postal_code",
    # THE THREE POLICY DATES, off the DASH Policies summary. Ordinary fields:
    # they follow the never-overwrite rule below, so a broker's correction
    # survives a re-parse. Deliberately NOT in _SEEDED_NAME_FIELDS, which is
    # the narrow exception for a name nobody typed.
    "renewal_date",
    "insured_since",
    "current_insurer_since",
    # THE ONE NUMERIC FIELD. int, not text (0114) — the loop below passes values
    # through untouched, so it reaches Postgres as an int and lands in an int
    # column. Listed here for the reason this tuple exists at all, stated at the
    # top: a field the parser can produce but this list omits is written nowhere
    # and fails silently, exactly as date_of_birth did.
    "at_fault_claims_6y",
)

# Fields the database constrains to a fixed list (migrations/0035). DASH is
# free text and we do not control what it prints, so a value outside these is
# checked for here rather than discovered by Postgres.
#
# WHY IT MATTERS DISPROPORTIONATELY: an update is one statement. A single
# unexpected word — "Separated", say, which is a perfectly reasonable thing for
# a bureau to print — raises 23514 and fails the WHOLE call, so the address,
# the licence and the date of birth in the same update are lost with it.
# Confirmed against the live database, not assumed.
#
# Skipping leaves the field for the broker, which is this feature's ordinary
# outcome and costs nothing. Mapping the value to a near-neighbour would be
# guessing on a rating factor.
_VOCABULARY = {
    "gender": {"Male", "Female", "Other"},
    "marital_status": {"Single", "Married", "Common Law", "Divorced", "Widowed"},
}

# NOT license_province, though the parser reads it. There is no field for it on
# New Policy and there is not going to be one: this brokerage writes Ontario
# business, so a licence-province control would be a dropdown with one option.
# Writing it anyway would put a value in the database that the broker cannot
# see, cannot verify, and cannot correct if DASH had it wrong — which is worse
# than not collecting it. `province` above is the ADDRESS province, which does
# have a field.
#
# Kept in the parser rather than deleted there: it costs nothing, and it is
# what the screen would need the day a second province matters.


def apply_report(driver_id: str, markdown: str) -> dict:
    """Fill this driver and their vehicles from a DASH report.

    The driver row must already exist — it is created when the broker opens
    the lead, so by the time a report is uploaded there is always one to fill.
    """
    driver_rows = driver_repo.get_by_id(driver_id).data or []
    if not driver_rows:
        return empty_result()
    driver = driver_rows[0]

    parsed = dash_parse.parse_driver(markdown)
    # THE POLICY DATES RIDE ALONG. They come from the Policies summary rather
    # than the header, so they are merged here rather than threaded through
    # parse_driver — which reads the header alone and is documented as doing
    # so. From _apply_driver's point of view they are ordinary fields, and get
    # the never-overwrite rule for free.
    parsed = {**parsed, **dash_parse.parse_policy_dates(markdown)}
    filled, skipped = _apply_driver(driver, parsed)

    # NO CARS ON A POLICY THAT INSURES NONE, and this is the door the guard in
    # services/vehicle.py did not cover: add_vehicle refuses one, and this
    # function wrote straight to the repository — so a broker could pick
    # Homeowner Only, upload a report, and put cars in the database that the
    # screen deliberately hides.
    #
    # SKIPPED, NOT REFUSED, unlike add_vehicle. Every other failure in this
    # file leaves the broker a field to type rather than losing the upload:
    # the applicant's own details are already written above and are what they
    # are waiting on. vehicles_added: 0 already tells them nothing was added.
    added = (
        _apply_vehicles(driver_id, dash_parse.parse_vehicles(markdown))
        if vehicle_service.lead_insures_vehicles(str(driver["lead_id"]))
        else 0
    )
    drivers = _apply_operators(driver["lead_id"], dash_parse.parse_operators(markdown))

    # No conversion call here. at_fault_claims_6y, written above, used to make a
    # lead "Preferred" and queue extra Meta events; since 0133 there is one
    # dataset and driver facts change no signal at all.

    return _result(filled, skipped, added, parsed, drivers)


def empty_result() -> dict:
    """Nothing was read, said in the SAME SHAPE as a successful read.

    A scanned PDF and a missing driver both land here. Returning a short dict
    instead — three keys where the caller's type declares six — is a contract
    the screen only survives because JavaScript tolerates undefined.
    """
    return _result([], [], 0, {}, 0)


def _result(
    filled: list[str],
    skipped: list[str],
    added: int,
    parsed: dict,
    drivers: int,
) -> dict:
    """What the caller gets back, including WHOSE report this was.

    THE SUBJECT COMES FROM THE PARSED REPORT, NOT FROM WHAT WAS WRITTEN, and
    that distinction is the whole point. Nothing here rejects a report for the
    wrong person — the broker decides that, and a system that argued with them
    would make an optional convenience into a step in the way. What it must
    not do is stay silent: a report for the spouse, or for the customer still
    open in another tab, fills exactly the blank fields it should not, and
    "fill blanks only" is no protection at all when the fields are blank.

    So the name and licence are handed back for the screen to show. Reporting
    only what was FILLED would go quiet in the one case that matters most —
    a broker who already typed a name gets that field skipped, and a mismatch
    is likeliest precisely when they have been typing.
    """
    return {
        "driver_filled": filled,
        "driver_skipped": skipped,
        "vehicles_added": added,
        "drivers_added": drivers,
        # As printed on the report, in DASH's own LAST,FIRST form. Not
        # reformatted: the broker is comparing this against the document in
        # front of them, and prettifying it makes that harder.
        "subject_name": parsed.get("name"),
        "subject_license_number": parsed.get("license_number"),
    }


def _apply_driver(driver: dict, parsed: dict) -> tuple[list[str], list[str]]:
    """Blank fields only.

    "Blank" means null or empty string — a driver row is created with the
    lead's name and phone and nothing else, so most fields arrive as null,
    while a field the broker cleared can come back as "".
    """
    # str | int, since 0114: at_fault_claims_6y is an int column and the loop
    # below passes parsed values through without coercion. Stringifying it here
    # would store "2" in an int column and rely on Postgres to cast back.
    values: dict[str, str | int] = {}
    skipped: list[str] = []

    # THE SPLIT RIDES ALONG WITH THE NAME. Derived here rather than in
    # dash_parse because it is an interpretation of what the report says, not
    # a reading of it — the parser stays byte-faithful (see dash_azure on why
    # that matters). Blank parts are dropped by the `new is None` check below,
    # so an ambiguous name Claude could not resolve leaves the columns empty
    # for the broker rather than filling them with a guess.

    if parsed.get("name"):
        first, middle, last = claude_client.split_name(parsed["name"])
        parsed = {
            **parsed,
            "first_name": first or None,
            "middle_name": middle or None,
            "last_name": last or None,
        }

    for field in _DRIVER_FIELDS:
        new = parsed.get(field)
        if new is None:
            continue
        if field in _VOCABULARY and new not in _VOCABULARY[field]:
            # A value the database would refuse. Skipped rather than sent —
            # see _VOCABULARY for what that would otherwise cost.
            logger.info("DASH %s=%r is not a value this form offers", field, new)
            continue
        existing = driver.get(field)
        if existing not in (None, "") and field not in _SEEDED_NAME_FIELDS:
            # The broker got here first. Recorded so the caller can say so,
            # rather than the difference being invisible.
            skipped.append(field)
            continue
        values[field] = new

    if values:
        driver_repo.update(driver["id"], values)

    return sorted(values), sorted(skipped)


def _apply_operators(lead_id: str, parsed: list[dict]) -> int:
    """Add the other people on the policy as additional drivers.

    Whatever the report lists, added. The point is to save the broker asking
    the customer for details a document in front of them already carries — so
    a name with no licence is still a name they do not have to type.

    A LICENCE ALREADY ON THE LEAD IS SKIPPED — retry safety, and nothing more
    than that. An earlier commit removed this check on the reasoning that "the
    report is not stored, so deduplication is unnecessary". That reasoning was
    wrong: the report is not stored, but THESE ROWS ARE. A broker who uploads
    the same PDF twice would get the spouse twice, and having to delete a
    duplicate because a retry succeeded is not a thing to ask of them.

    Exact match on the licence, which is the same trade the VIN check makes
    one function below — not normalised, not fuzzy, no index. A licence-less
    operator inserts every time, accepted for the same reason a VIN-less car
    does: it is still a name the broker does not have to type, and it is
    visible enough to delete.

    The other exclusion lives in dash_parse.parse_operators: the report's
    subject is listed as an operator on his own policy, so without it the
    applicant appears on screen twice.

    RELATIONSHIP IS 'NA' (migrations/0081). DASH knows the person is on the
    policy and cannot know what they are to OUR applicant — its own
    "Relationship to Policyholder" is about whoever owns the policy, who need
    not be the same person. So the row says "not stated" and the broker picks
    it on New Policy, where they are looking at that driver anyway.

    Counts what was actually inserted. A failure part-way through leaves the
    drivers already added on screen, and reporting zero would make the toast
    and the panel disagree.
    """
    try:
        existing = driver_repo.list_for_lead(lead_id).data or []
        # Case-folded, matching what the vehicle loop does with VINs: a licence
        # typed in lower case is the same person.
        known = {
            d["license_number"].upper() for d in existing if d.get("license_number")
        }
    except Exception:
        # Could not read who is already here. Import anyway rather than
        # refusing: a duplicate the broker can see beats a spouse who never
        # arrived because a read failed.
        logger.exception("Could not read existing drivers on lead %s", lead_id)
        known = set()

    added = 0
    for operator in parsed:
        licence = (operator.get("license_number") or "").upper()
        if licence and licence in known:
            continue
        try:
            # Name and licence only — the same reason license_province is
            # absent from _DRIVER_FIELDS above: there is no field for it, so
            # writing it would store something the broker cannot see.
            driver_repo.insert_additional(
                lead_id,
                "NA",
                {k: operator[k] for k in ("name", "license_number") if k in operator},
            )
            # Recorded so the SAME licence twice in ONE report inserts once.
            # The vehicle loop below has always done this; the driver loop did
            # not, so a report listing someone on two policies added them
            # twice even though the check was there.
            if licence:
                known.add(licence)
            added += 1
        except Exception:
            # Never raises, for the reason the vehicles do not: the applicant's
            # own fields are already written and are what the broker is waiting
            # on. A driver that would not insert is one the broker adds by hand.
            logger.exception("A DASH driver could not be added to lead %s", lead_id)
    return added


def _apply_vehicles(driver_id: str, parsed: list[dict]) -> int:
    """Add cars that are not already on the driver.

    THE RULES COME FROM THE DELETED services/vehicle.py, carried over rather
    than reinvented — it had already worked them out against real data:

      1. NOTHING IS EVER DELETED. A car missing from the report stays put; the
         broker may have added it deliberately, and a report is not evidence
         that a car does not exist.
      2. MATCHED BY VIN. Its own comment: "Verified against the live database,
         not assumed." (It also matched on dash_vehicle_id; that column is
         gone with the dash_* tables, and the same comment argued the VIN was
         the reliable key anyway.)
      3. A KNOWN VIN IS LEFT EXACTLY AS TYPED. Not updated, not merged.
      4. A VIN-LESS CAR INSERTS EVERY TIME. Accepted deliberately: matching on
         make and model could silently merge two genuinely different cars,
         which is worse than a duplicate the broker can see and delete.

    Never raises. The driver's own fields are already written and are what the
    broker is waiting on — failing the whole upload because a vehicle row
    could not be inserted would trade the important thing for the convenient
    one. A failure here means the broker adds the car by hand.
    """
    if not parsed:
        return 0

    try:
        existing = vehicle_repo.list_for_driver(driver_id).data or []
        known = {(v.get("vin") or "").upper() for v in existing if v.get("vin")}
    except Exception:
        # Could not read what is already here. Import anyway rather than
        # refusing: a duplicate the broker can see beats a car that never
        # arrived because a read failed.
        logger.exception("Could not read existing vehicles for driver %s", driver_id)
        known = set()

    added = 0
    for vehicle in parsed:
        vin = (vehicle.get("vin") or "").upper()
        if vin and vin in known:
            continue
        try:
            vehicle_repo.insert({**vehicle, "driver_id": driver_id})
            if vin:
                known.add(vin)
            added += 1
        except Exception:
            # PER CAR, not around the loop. Wrapping the whole thing meant one
            # bad insert reported zero while an earlier car sat on screen — the
            # toast and the panel disagreeing. Same fix the driver loop above
            # already carries.
            logger.exception("A DASH vehicle could not be added to driver %s", driver_id)
    return added
