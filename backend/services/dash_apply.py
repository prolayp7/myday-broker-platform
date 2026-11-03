"""Writes a read DASH report onto the broker's form.

TWO READERS, ONE SHAPE. A report is read by Claude (dash_claude) or, if that
fails, by the old regex parser (dash_parse). from_claude() and from_fallback()
turn either into the same report dict, so apply_report() does not care which
one read it:

    source            "claude" | "fallback"
    driver            column -> value offered for the applicant row
    vehicles          cars to add
    operators         additional drivers to add
    gaps, claims      lists, or None when this reader cannot read them
    notes             things the broker should look at
    subject_name / subject_license_number / lead_match

ONE RULE FOR THE APPLICANT'S FIELDS: never overwrite the broker. DASH fills
what is empty and leaves everything else exactly as typed — except the name,
which the lead form seeds and nobody typed (see _SEEDED_NAME_FIELDS).

GAPS AND CLAIMS ARE DASH FACTS, NOT BROKER INPUT, so a new reading replaces
them — but only a reading that could see them. The fallback parser cannot, and
returns None for both; None means "leave what is saved", never "there are
none".
"""

import logging
from datetime import date

import claude_client
from repositories import dash_history as history_repo
from repositories import driver as driver_repo
from repositories import vehicle as vehicle_repo
from services import dash_parse
from services import dash_rules as rules
from services import vehicle as vehicle_service

logger = logging.getLogger("myday.dash")

# The only fields DASH is allowed to REPLACE. The lead form seeds them at
# applicant creation ("Sajida"); the report has the licensing record
# ("CHEEMA,SAJIDA"), which is the better source.
_SEEDED_NAME_FIELDS = ("name", "first_name", "middle_name", "last_name")

# Everything a reader can offer for the applicant row. Spelled out so a new
# field is a deliberate decision here, not something that starts writing to
# the database on its own.
_DRIVER_FIELDS = (
    "name",
    "first_name",
    "middle_name",
    "last_name",
    "date_of_birth",
    "license_number",
    "gender",
    "marital_status",
    "street_address",
    "city",
    "province",
    "postal_code",
    "renewal_date",
    "insured_since",
    "current_insurer_since",
    # int column (0114); passed through without coercion.
    "at_fault_claims_6y",
)

# Fields the database constrains to a fixed list (0035). A value outside it
# raises 23514 and fails the WHOLE update, so it is skipped here and left for
# the broker rather than discovered by Postgres.
_VOCABULARY = {
    "gender": {"Male", "Female", "Other"},
    "marital_status": {"Single", "Married", "Common Law", "Divorced", "Widowed"},
}

# How far back each list counts (agreed: gaps 3, claims 8). Older rows are kept
# and shown struck through, so the broker can see they were not missed.
GAP_YEARS = 3
CLAIM_YEARS = 8


# ── Building the report ────────────────────────────────────────────────────


def from_claude(raw: dict) -> dict:
    """Claude's reading, with the exact calculations done in Python."""
    name = raw.get("name") or {}
    licence = raw.get("license") or {}
    address = raw.get("address") or {}
    dln, province = licence.get("number"), licence.get("province")
    printed_dob = raw.get("date_of_birth_printed")
    report_date = _date(raw.get("report_date"))

    gender = raw.get("gender")
    if not gender:
        # Backup only: the licence encodes M/F, and only when its year agrees.
        gender = rules.licence_facts(dln, province, printed_dob)[1]

    driver = {
        "name": name.get("full_name"),
        "first_name": name.get("first_name"),
        "middle_name": name.get("middle_name"),
        "last_name": name.get("last_name"),
        "license_number": dln,
        "date_of_birth": rules.date_of_birth(printed_dob, dln, province),
        "gender": gender,
        "marital_status": raw.get("marital_status"),
        "street_address": address.get("street"),
        "city": address.get("city"),
        "province": address.get("province"),
        "postal_code": address.get("postal_code"),
        "renewal_date": rules.renewal_date(raw.get("report_date"), raw.get("newest_policy_end")),
        "insured_since": _date_text(raw.get("insured_since")),
        "current_insurer_since": _date_text(raw.get("current_insurer_since")),
        "at_fault_claims_6y": raw.get("at_fault_claims_6y"),
    }

    notes = []
    if name.get("unsure"):
        notes.append(f"Name: {name.get('note') or 'check the name split'}")
    if address.get("note"):
        notes.append(f"Address: {address['note']}")
    for problem in rules.address_problems(
        driver["street_address"], driver["city"], driver["province"], driver["postal_code"]
    ):
        notes.append(f"Address: {problem} — check it against the PDF")
    if raw.get("policy_dates_unsure"):
        notes.append(f"Policy dates: {raw.get('policy_dates_note') or 'check the dates'}")

    gaps = (
        rules.find_gaps(raw.get("applicant_terms") or [])
        if raw.get("possible_gap_marker")
        else []
    )
    claims, claim_notes = _claims(raw.get("claims") or [], report_date)

    return {
        "source": "claude",
        "driver": {k: v for k, v in driver.items() if v not in (None, "")},
        "vehicles": [_vehicle(v) for v in raw.get("vehicles") or [] if v.get("vin") or v.get("year_make_model")],
        "operators": [
            {k: v for k, v in op.items() if v}
            for op in raw.get("additional_drivers") or []
            if op.get("name")
        ],
        "gaps": gaps,
        "claims": claims,
        "notes": notes + claim_notes,
        "subject_name": name.get("full_name"),
        "subject_license_number": dln,
        "lead_match": raw.get("lead_match"),
    }


def from_fallback(markdown: str) -> dict:
    """The regex parser's reading — today's behaviour, used when Claude fails.

    It cannot read gaps or claims, so both are None: what is already saved
    stays saved.
    """
    driver = {**dash_parse.parse_driver(markdown), **dash_parse.parse_policy_dates(markdown)}
    if driver.get("name"):
        # A split is an interpretation of the name, not a reading of it.
        # Blank parts stay blank for the broker rather than becoming a guess.
        first, middle, last = claude_client.split_name(driver["name"])
        driver.update(first_name=first or None, middle_name=middle or None, last_name=last or None)

    operators = dash_parse.parse_operators(markdown)
    for operator in operators:
        first, middle, last = claude_client.split_name(operator.get("name"))
        operator.update({k: v for k, v in
                         (("first_name", first), ("middle_name", middle), ("last_name", last)) if v})

    return {
        "source": "fallback",
        "driver": driver,
        "vehicles": dash_parse.parse_vehicles(markdown),
        "operators": operators,
        "gaps": None,
        "claims": None,
        "notes": [
            "Read by the backup method — check the name, address and dates "
            "against the PDF. Insurance gaps and claims were not read; what was "
            "saved before is unchanged."
        ],
        "subject_name": driver.get("name"),
        "subject_license_number": driver.get("license_number"),
        "lead_match": None,
    }


def _date(value) -> date | None:
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def _date_text(value) -> str | None:
    day = _date(value)
    return day.isoformat() if day else None


def _vehicle(raw: dict) -> dict:
    vehicle = {
        "vin": (raw.get("vin") or "").replace(" ", "").upper() or None,
        "year_make_model": raw.get("year_make_model"),
        "one_day_distance_km": raw.get("one_way_km"),
        "annual_km": raw.get("annual_km"),
    }
    return {k: v for k, v in vehicle.items() if v is not None}


def _claims(raw_claims: list[dict], report_date: date | None) -> tuple[list[dict], list[str]]:
    """Every claim, with its amounts worked out exactly."""
    claims, notes = [], []
    for raw in raw_claims:
        loss_date = _date(raw.get("date_of_loss"))
        if not loss_date:
            continue
        lines = raw.get("kol_lines") or []
        totals, unknown = rules.claim_amounts(lines)
        percent = raw.get("at_fault_percent")
        claim = {
            "date_of_loss": loss_date.isoformat(),
            "at_fault_percent": percent,
            "whose": raw.get("whose") or "unknown",
            "claim_driver_license": raw.get("driver_license"),
            "claim_driver_name": raw.get("driver_name"),
            "insurer": raw.get("insurer"),
            "claim_number": raw.get("claim_number"),
            # Whole dollars as strings: exact, and JSON-safe for the RPC.
            **{box: str(amount) for box, amount in totals.items()},
        }
        claims.append(claim)

        # A claim past the window is struck through: nothing there to check.
        if report_date and not rules.within_years(loss_date, report_date, CLAIM_YEARS):
            continue

        label = f"Claim {loss_date.isoformat()}"
        if unknown:
            notes.append(f"{label}: loss code(s) {', '.join(unknown)} not recognised")
        if not rules.claim_totals_match(lines, raw.get("total_loss"), raw.get("total_expense")):
            notes.append(f"{label}: amounts don't add up to DASH's totals — check the PDF")
    _same_date_driver(claims)
    return claims, notes


_DRIVER_KEYS = ("whose", "claim_driver_license", "claim_driver_name")


def _same_date_driver(claims: list[dict]) -> None:
    """A claim with no driver takes the driver of a claim on the SAME date of
    loss — the broker's rule: one accident is often recorded as two claims and
    only one names who was driving.

    Only when that date names exactly one driver; nothing else changes.
    """
    named: dict[str, set[tuple]] = {}
    for claim in claims:
        if claim["whose"] != "unknown":
            named.setdefault(claim["date_of_loss"], set()).add(
                tuple(claim[k] for k in _DRIVER_KEYS)
            )
    for claim in claims:
        drivers = named.get(claim["date_of_loss"], set())
        if claim["whose"] == "unknown" and len(drivers) == 1:
            claim.update(zip(_DRIVER_KEYS, next(iter(drivers))))


# ── Writing it ─────────────────────────────────────────────────────────────


def apply_report(driver_id: str, report: dict) -> dict:
    """Fill this driver, their vehicles and drivers, and replace gaps/claims."""
    driver_rows = driver_repo.get_by_id(driver_id).data or []
    if not driver_rows:
        return empty_result()
    driver = driver_rows[0]

    filled, skipped = _apply_driver(driver, report["driver"])

    # No cars on a policy that insures none (Homeowner Only) — skipped rather
    # than refused, so the applicant's fields above are not lost with them.
    added = (
        _apply_vehicles(driver_id, report["vehicles"])
        if vehicle_service.lead_insures_vehicles(str(driver["lead_id"]))
        else 0
    )
    drivers = _apply_operators(driver["lead_id"], report["operators"])

    # "Check these" — replaced by every reading, cleared by Dismiss. Its own
    # update, outside the fill-blanks rule: these are about THIS reading.
    try:
        driver_repo.update(driver_id, {"dash_notes": report.get("notes") or None})
    except Exception:
        logger.exception("Could not save the DASH notes for driver %s", driver_id)

    if report.get("gaps") is not None and report.get("claims") is not None:
        # One database statement: both lists are replaced together or not at
        # all, so a failure part-way can never leave them empty.
        history_repo.replace(driver_id, report["gaps"], report["claims"])

    return _result(filled, skipped, added, drivers, report)


def empty_result() -> dict:
    """Nothing was read, in the SAME SHAPE as a successful read."""
    return _result([], [], 0, 0, {"source": None, "notes": []})


def _result(filled, skipped, added, drivers, report) -> dict:
    """What the screen gets back, including WHOSE report this was.

    The subject comes from the report, not from what was written — a broker
    who already typed a name gets that field skipped, and a wrong report is
    likeliest exactly then.
    """
    return {
        "driver_filled": filled,
        "driver_skipped": skipped,
        "vehicles_added": added,
        "drivers_added": drivers,
        "subject_name": report.get("subject_name"),
        "subject_license_number": report.get("subject_license_number"),
        "source": report.get("source"),
        "notes": report.get("notes") or [],
        "gaps_found": None if report.get("gaps") is None else len(report["gaps"]),
        "claims_found": None if report.get("claims") is None else len(report["claims"]),
    }


def _apply_driver(driver: dict, offered: dict) -> tuple[list[str], list[str]]:
    """Blank fields only (null or ""), apart from the seeded name fields."""
    values: dict[str, str | int] = {}
    skipped: list[str] = []

    for field in _DRIVER_FIELDS:
        new = offered.get(field)
        if new is None:
            continue
        if field in _VOCABULARY and new not in _VOCABULARY[field]:
            logger.info("DASH %s=%r is not a value this form offers", field, new)
            continue
        existing = driver.get(field)
        if existing not in (None, "") and field not in _SEEDED_NAME_FIELDS:
            skipped.append(field)
            continue
        values[field] = new

    if values:
        driver_repo.update(driver["id"], values)

    return sorted(values), sorted(skipped)


def _apply_operators(lead_id: str, parsed: list[dict]) -> int:
    """Add the other people on the policy as additional drivers.

    A licence already on the lead is skipped, so uploading the same report
    twice does not add the spouse twice. A licence-less operator inserts every
    time — still a name the broker does not have to type, and easy to delete.

    Relationship is 'NA' (0081): DASH cannot know what they are to OUR
    applicant, so the broker picks it on New Policy.
    """
    try:
        existing = driver_repo.list_for_lead(lead_id).data or []
        known = {d["license_number"].upper() for d in existing if d.get("license_number")}
    except Exception:
        # A duplicate the broker can see beats a spouse who never arrived.
        logger.exception("Could not read existing drivers on lead %s", lead_id)
        known = set()

    added = 0
    for operator in parsed:
        licence = (operator.get("license_number") or "").upper()
        if licence and licence in known:
            continue
        try:
            # Name (printed and split — the screen shows the split) and
            # licence only. No field for the licence province, so writing it
            # would store something nobody can see.
            driver_repo.insert_additional(
                lead_id,
                "NA",
                {
                    k: operator[k]
                    for k in ("name", "first_name", "middle_name", "last_name", "license_number")
                    if operator.get(k)
                },
            )
            if licence:
                known.add(licence)
            added += 1
        except Exception:
            # Never raises: the applicant's own fields are already written.
            logger.exception("A DASH driver could not be added to lead %s", lead_id)
    return added


def _apply_vehicles(driver_id: str, parsed: list[dict]) -> int:
    """Add cars that are not already on the driver.

      1. Nothing is ever deleted — a report is not evidence a car is gone.
      2. Matched by VIN; a known VIN is left exactly as typed.
      3. A VIN-less car inserts every time: matching on make and model could
         merge two different cars, which is worse than a visible duplicate.

    Never raises — a failure here means the broker adds the car by hand.
    """
    if not parsed:
        return 0

    try:
        existing = vehicle_repo.list_for_driver(driver_id).data or []
        known = {(v.get("vin") or "").upper() for v in existing if v.get("vin")}
    except Exception:
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
            # Per car, so one bad insert doesn't hide the cars that did land.
            logger.exception("A DASH vehicle could not be added to driver %s", driver_id)
    return added
