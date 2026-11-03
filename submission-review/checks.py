"""Every review rule. Pure functions on saved rows, no I/O and no Claude.

run_checks() takes a file as dictionaries and returns what is wrong with the
application. A blank detail never passes: the filled check reports it. An
unclear detail goes to a person. Rule numbers are the build plan's, section 8.
"""

import re
from dataclasses import dataclass, field
from datetime import date

from dash_rules import add_years, normalize_dln, within_years

ERROR, WARNING = "error", "warning"

_MARITAL = {
    "m": "married", "mar": "married", "married": "married",
    "s": "single", "sin": "single", "single": "single",
    "w": "widowed", "wid": "widowed", "widowed": "widowed",
    "d": "divorced", "div": "divorced", "divorced": "divorced",
    "sep": "separated", "separated": "separated",
    "cl": "common law", "common law": "common law", "common-law": "common law",
}
_PROVINCES = {"on": "ontario", "ontario": "ontario"}
_NOT_AVAILABLE = ("", "not available", "n/a", "unknown")


@dataclass
class Result:
    findings: list[dict] = field(default_factory=list)
    unclear: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, rule, section, severity, message, driver_no=None, auto_no=None,
            application_value=None, other_value=None, source_document_id=None):
        self.findings.append({
            "rule_no": str(rule),
            "section": section,
            "severity": severity,
            "message": message,
            "driver_no": driver_no,
            "auto_no": auto_no,
            "application_value": None if application_value is None else str(application_value),
            "other_value": None if other_value is None else str(other_value),
            "source_document_id": source_document_id,
        })


def _as_date(value) -> date | None:
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(str(value or "")[:10])
    except ValueError:
        return None


def _blank(value) -> bool:
    return value is None or str(value).strip() in ("", "blank")


def _name_key(value: str | None) -> str:
    words = re.findall(r"[a-z0-9]+", (value or "").lower())
    return " ".join(sorted(words))


def _names_fit(left: str | None, right: str | None) -> bool:
    """A form prints a middle initial, or nothing, where the MVR has the full
    name. Every word of the shorter name must fit, with two in full."""
    short, long = sorted((_name_key(left).split(), _name_key(right).split()), key=len)
    whole = 0
    for word in short:
        if word in long:
            whole += 1
            long.remove(word)
            continue
        initial = next((w for w in long if len(word) == 1 and w.startswith(word)), None)
        if initial is None:
            return False
        long.remove(initial)
    return whole >= 2


def _same_name(left: str | None, right: str | None) -> bool:
    if _name_key(left) == _name_key(right) or _names_fit(left, right):
        return True
    # A PDF can drop the spaces, so compare the letters as well.
    return sorted(re.sub(r"[^a-z0-9]", "", (left or "").lower())) == sorted(
        re.sub(r"[^a-z0-9]", "", (right or "").lower())
    )


def _applicant_is(applicant: str | None, name: str | None) -> bool:
    """A joint application prints both names; the driver only has to be one."""
    wanted = set(_name_key(name).split())
    if not wanted:
        return False
    parts = re.split(r"&|\band\b", applicant or "", flags=re.IGNORECASE)
    return any(
        wanted <= set(_name_key(part).split()) or _same_name(part, name) for part in parts
    ) or _same_name(applicant, name)


def _same_text(left: str | None, right: str | None) -> bool:
    clean = lambda v: re.sub(r"[^a-z0-9]", "", (v or "").lower())
    return clean(left) == clean(right)


def _marital(value: str | None) -> str:
    text = (value or "").strip().lower()
    return _MARITAL.get(text, text)


def _gender(value: str | None) -> str:
    return (value or "").strip().upper()[:1]


def _tick_rule(result, rule, section, value, wanted, message, label, **where):
    """A Yes/No rule. Blank belongs to the filled check, unclear to a person."""
    if value == "unclear":
        result.unclear.append(label)
    elif not _blank(value) and value != wanted:
        result.add(rule, section, WARNING, message, application_value=value, **where)


def same_form_rules(file: dict, result: Result) -> None:
    app = file["application"]
    effective, expiry = _as_date(app.get("effective_date")), _as_date(app.get("expiry_date"))

    if effective and expiry and expiry != add_years(effective, 1):
        result.add(1, "Policy period", WARNING, "The policy term is not exactly 1 year",
                   application_value=f"{effective} to {expiry}")
    for label, value in (("Effective", app.get("effective_time")), ("Expiry", app.get("expiry_time"))):
        if value and not str(value).startswith("00:01"):
            result.add(2, "Policy period", WARNING, f"{label} time is not 12:01 a.m.",
                       application_value=value)

    for vehicle in file["vehicles"]:
        auto = vehicle["auto_no"]
        where = {"auto_no": auto}
        _tick_rule(result, 3, "Vehicles", vehicle.get("use_pleasure"), "yes",
                   "Automobile use is not Pleasure", f"vehicle.{auto}.use_pleasure", **where)
        if (vehicle.get("business_pct") or 0) > 0 or "yes" in (
            vehicle.get("use_farm"), vehicle.get("use_commercial")
        ):
            result.add(3, "Vehicles", WARNING, "The vehicle has business use", auto_no=auto,
                       application_value=f"{vehicle.get('business_pct') or 0}%")
        _tick_rule(result, 4, "Vehicles", vehicle.get("car_pool"), "no",
                   "Car pooling is not No", f"vehicle.{auto}.car_pool", **where)
        fuel = vehicle.get("fuel")
        if fuel and fuel.strip().lower() != "gasoline":
            result.add(5, "Vehicles", WARNING, "Fuel is not Gasoline", auto_no=auto,
                       application_value=fuel)
        _tick_rule(result, 6, "Vehicles", vehicle.get("unrepaired_damage"), "no",
                   "Unrepaired damage is not No", f"vehicle.{auto}.unrepaired_damage", **where)
        _tick_rule(result, 7, "Vehicles", vehicle.get("modified"), "no",
                   "Modified/customized is not No", f"vehicle.{auto}.modified", **where)

    _tick_rule(result, 8, "Vehicles", app.get("registered_owner"), "yes",
               "The applicant is not both registered and actual owner", "registered_owner")

    autos = app.get("household_autos")
    if autos is not None and autos != len(file["vehicles"]):
        result.add(9, "Vehicles", ERROR,
                   "Total automobiles does not equal the vehicles listed",
                   application_value=autos, other_value=len(file["vehicles"]))

    for label, name in (
        ("prior_insurer", "Prior insurer"), ("prior_policy_no", "Prior policy number"),
        ("prior_expiry", "Prior expiry date"),
    ):
        if _blank(app.get(label)):
            result.add(10, "Prior insurance", WARNING, f"{name} is not filled in")

    for label in ("q_suspended_6y", "q_cancelled_3y", "q_misrep_3y", "q_fraud"):
        _tick_rule(result, 11, "Questions", app.get(label), "no",
                   "A question that should be No is not No", label)

    for vehicle in file["vehicles"]:
        auto = vehicle["auto_no"]
        rows = [c for c in file["coverages"] if c["auto_no"] == auto]
        loss_of_use = next((c for c in rows if "loss_of_use" in c["code"]), None)
        if not loss_of_use or not (loss_of_use.get("limit_amount") or loss_of_use.get("limit_text")):
            result.add(12, "Coverages", WARNING, "OPCF 20 Loss of Use has no amount", auto_no=auto)
        mortgage = next((c for c in rows if "23a" in c["code"]), None)
        # A printed premium, even 0.00, is the only sign the form gives.
        if mortgage and mortgage.get("premium") is not None and not (
            vehicle.get("lienholder_name") and vehicle.get("lienholder_address")
        ):
            result.add(13, "Coverages", WARNING,
                       "OPCF 23A is on the vehicle but the lienholder is not filled in",
                       auto_no=auto)


def _driver_lookup(file: dict) -> dict[str, int]:
    return {normalize_dln(d.get("licence_no")): d["driver_no"] for d in file["drivers"]}


def document_rules(file: dict, result: Result) -> None:
    app = file["application"]
    mvrs, dashes = file.get("mvrs", {}), file.get("dashes", {})
    by_no = {d["driver_no"]: d for d in file["drivers"]}

    first = by_no.get(min(by_no)) if by_no else None
    mvr = mvrs.get(normalize_dln(first.get("licence_no"))) if first else None
    if mvr:
        report = mvr["report"]
        source = mvr.get("document_id")
        if not _applicant_is(app.get("applicant_name"), report.get("name")):
            result.add(14, "Applicant", WARNING, "Applicant name differs from the MVR",
                       application_value=app.get("applicant_name"),
                       other_value=report.get("name"), source_document_id=source)
        province = lambda v: _PROVINCES.get((v or "").strip().lower(), (v or "").strip().lower())
        for label, left, right, same in (
            ("Street", app.get("street"), report.get("street"), _same_text),
            ("City", app.get("city"), report.get("city"), _same_text),
            ("Province", province(app.get("province")), province(report.get("province")), _same_text),
            ("Postal code", app.get("postal_code"), report.get("postal_code"), _same_text),
        ):
            if left and right and not same(left, right):
                result.add(14, "Applicant", WARNING, f"{label} differs from the MVR",
                           application_value=left, other_value=right, source_document_id=source)

    for driver in file["drivers"]:
        number, licence = driver["driver_no"], normalize_dln(driver.get("licence_no"))
        mvr = mvrs.get(licence)
        if mvr:
            report, source = mvr["report"], mvr.get("document_id")
            if not _same_name(driver.get("name"), report.get("name")):
                result.add(15, "Drivers", WARNING, "Driver name differs from the MVR",
                           driver_no=number, application_value=driver.get("name"),
                           other_value=report.get("name"), source_document_id=source)
            left, right = _as_date(driver.get("dob")), _as_date(report.get("dob"))
            if left and right and left != right:
                result.add(15, "Drivers", WARNING, "Date of birth differs from the MVR",
                           driver_no=number, application_value=left, other_value=right,
                           source_document_id=source)
        dash = dashes.get(licence)
        if dash:
            report, source = dash["report"], dash.get("document_id")
            gender = (report.get("gender") or "").strip()
            if gender.lower() in _NOT_AVAILABLE:
                result.notes.append(f"Driver {number}: DASH has no gender, check skipped")
            elif driver.get("gender") and _gender(driver["gender"]) != _gender(gender):
                result.add(16, "Drivers", WARNING, "Gender differs from the DASH", driver_no=number,
                           application_value=driver["gender"], other_value=gender,
                           source_document_id=source)
            marital = (report.get("marital") or "").strip()
            if marital.lower() in _NOT_AVAILABLE:
                result.notes.append(f"Driver {number}: DASH has no marital status, check skipped")
            elif driver.get("marital") and _marital(driver["marital"]) != _marital(marital):
                result.add(16, "Drivers", WARNING, "Marital status differs from the DASH",
                           driver_no=number, application_value=driver["marital"],
                           other_value=marital, source_document_id=source)

    _claims_rule(file, result)
    _convictions_rule(file, result)


def _distinct_claims(file: dict, effective: date, result: Result) -> list[dict]:
    """Every DASH claim in the 6-year limit, once. Never merged on a guess."""
    records = []
    for licence, dash in file.get("dashes", {}).items():
        for claim in dash["claims"]:
            day = _as_date(claim.get("date_of_loss"))
            if day and within_years(day, effective, 6) and day <= effective:
                records.append({**claim, "_day": day, "_report": licence,
                                "_source": dash.get("document_id")})

    distinct: dict[tuple, dict] = {}
    loose = []
    for record in records:
        number = _name_key(record.get("claim_number"))
        insurer = _name_key(record.get("insurer"))
        if number and insurer:
            distinct.setdefault((insurer, number), record)
        else:
            loose.append(record)
    claims = list(distinct.values())

    for record in loose:
        same_date_elsewhere = [
            c for c in claims + [r for r in loose if r is not record]
            if c["_day"] == record["_day"] and c["_report"] != record["_report"]
        ]
        if same_date_elsewhere:
            result.unclear.append(f"possible duplicate claim on {record['_day']}")
        else:
            claims.append(record)

    keyed = list(distinct.values())
    for index, claim in enumerate(keyed):
        for other in keyed[index + 1:]:
            if claim["_day"] == other["_day"] and claim["_report"] != other["_report"]:
                result.unclear.append(f"possible duplicate claim on {claim['_day']}")
    return claims


def _claims_rule(file: dict, result: Result) -> None:
    effective = _as_date(file["application"].get("effective_date"))
    if not effective:
        return
    drivers = _driver_lookup(file)
    rows = list(file["claims"])
    claims = sorted(_distinct_claims(file, effective, result), key=lambda c: c["_day"])

    def take(day: date, driver_no: int | None) -> dict | None:
        for row in rows:
            if _as_date(row.get("loss_date")) == day and (
                driver_no is None or row.get("driver_no") == driver_no
            ):
                rows.remove(row)
                return row
        return None

    # Exact matches first, so a loose claim cannot use up a named driver's row.
    waiting = []
    for claim in claims:
        driver_no = drivers.get(normalize_dln(claim.get("driver_licence")))
        if driver_no and take(claim["_day"], driver_no):
            continue
        waiting.append((claim, driver_no))

    for claim, driver_no in waiting:
        row = take(claim["_day"], None)
        if row and driver_no:
            result.add(17, "Claims", WARNING, "The claim is listed under a different driver",
                       driver_no=driver_no, application_value=row.get("driver_no"),
                       other_value=claim["_day"], source_document_id=claim["_source"])
        elif not row:
            result.add(17, "Claims", ERROR,
                       "A DASH claim in the last 6 years is not listed on the application",
                       driver_no=driver_no, other_value=claim["_day"],
                       source_document_id=claim["_source"])


def _convictions_rule(file: dict, result: Result) -> None:
    effective = _as_date(file["application"].get("effective_date"))
    if not effective:
        return
    rows = list(file["convictions"])
    for driver in file["drivers"]:
        number = driver["driver_no"]
        mvr = file.get("mvrs", {}).get(normalize_dln(driver.get("licence_no")))
        if not mvr:
            continue
        for entry in mvr["entries"]:
            day = _as_date(entry.get("entry_date"))
            if entry.get("entry_type") == "reinstatement" or not day:
                continue
            if not within_years(day, effective, 3) or day > effective:
                continue
            same_day = [r for r in rows if _as_date(r.get("convicted_date")) == day]
            row = next((r for r in same_day if r.get("driver_no") == number), None)
            unnumbered = next((r for r in same_day if r.get("driver_no") is None), None)
            if row:
                rows.remove(row)
            elif unnumbered:
                rows.remove(unnumbered)
                result.add(18, "Convictions", WARNING,
                           "An MVR entry is listed with no driver number on the application",
                           driver_no=number, other_value=day,
                           source_document_id=mvr.get("document_id"))
            else:
                result.add(18, "Convictions", WARNING,
                           f"An MVR {entry.get('entry_type')} in the last 3 years is not listed",
                           driver_no=number, other_value=day,
                           source_document_id=mvr.get("document_id"))


_APPLICANT_FILLED = (
    ("applicant_name", "Applicant name"), ("street", "Street"), ("city", "City"),
    ("province", "Province"), ("postal_code", "Postal code"),
    ("effective_date", "Effective date"), ("effective_time", "Effective time"),
    ("expiry_date", "Expiry date"), ("expiry_time", "Expiry time"),
    ("registered_owner", "Registered owner"), ("household_autos", "Total automobiles"),
    ("q_suspended_6y", "Suspended question"), ("q_cancelled_3y", "Cancelled question"),
    ("q_misrep_3y", "Misrepresentation question"), ("q_fraud", "Fraud question"),
    ("total_premium", "Total premium"),
)
_DRIVER_FILLED = (
    ("name", "name"), ("licence_no", "licence number"), ("dob", "date of birth"),
    ("gender", "gender"), ("marital", "marital status"),
    ("first_lic_class", "first licence class"), ("first_lic_year", "first licence date"),
)
_VEHICLE_FILLED = (
    ("model_year", "year"), ("make_model", "make and model"), ("vin", "VIN"),
    ("annual_km", "annual km"), ("fuel", "fuel"), ("car_pool", "car pool"),
    ("unrepaired_damage", "unrepaired damage"), ("modified", "modified"),
)


def filled_check(file: dict, result: Result) -> None:
    app = file["application"]
    def missing(message, **where):
        result.add("filled", "Filled check", WARNING, f"{message} is not filled in", **where)

    for key, label in _APPLICANT_FILLED:
        if _blank(app.get(key)):
            missing(label)
    for driver in file["drivers"]:
        for key, label in _DRIVER_FILLED:
            if _blank(driver.get(key)):
                missing(f"Driver {driver['driver_no']} {label}", driver_no=driver["driver_no"])
    for vehicle in file["vehicles"]:
        auto = vehicle["auto_no"]
        for key, label in _VEHICLE_FILLED:
            if _blank(vehicle.get(key)):
                missing(f"Vehicle {auto} {label}", auto_no=auto)
        if "yes" not in (vehicle.get("owned"), vehicle.get("leased")):
            missing(f"Vehicle {auto} owned or leased", auto_no=auto)
        if "yes" not in (vehicle.get("use_pleasure"), vehicle.get("use_farm"), vehicle.get("use_commercial")):
            missing(f"Vehicle {auto} use", auto_no=auto)
        if not any(u["auto_no"] == auto for u in file.get("driver_use", [])):
            missing(f"Vehicle {auto} % use by driver", auto_no=auto)
        if vehicle.get("leased") == "yes" and not (app.get("lessor_name") and app.get("lessor_address")):
            missing(f"Vehicle {auto} lessor name and address", auto_no=auto)

    answers = ("q_suspended_6y", "q_cancelled_3y", "q_misrep_3y", "q_fraud")
    if "yes" in (app.get(key) for key in answers) and _blank(app.get("remarks")):
        missing("Remarks for a Yes answer")


def run_checks(file: dict) -> Result:
    result = Result()
    filled_check(file, result)
    same_form_rules(file, result)
    document_rules(file, result)
    return result
