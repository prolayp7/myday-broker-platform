"""Turns Azure's markdown of a DASH report into the fields we prefill.

PLAIN PYTHON, NO MODEL. An earlier design assumed DASH needed one, because
choosing between several operator blocks looked like judgement. Reading a real
8-page report showed it is not: every block prints the licence number, and
every claim names its own driver. What was left is string matching, which is
testable in a way a prompt is not.

WHAT IS PARSED, and nothing else — the report is used to fill the broker's
form and is then discarded, so anything the form has no home for is skipped:

  driver     name, licence number + province, gender, marital status, address
  operators  the other people on the most recent policy: name and licence
  vehicles   year/make/model, VIN, one-way and annual commuting distance

DATE OF BIRTH ONLY IF IT IS A DATE. Every report seen so far prints the YEAR
alone ("Date of Birth: 1997"), and driver.date_of_birth is a date column —
inventing a month and day would put a wrong value in a field the broker is
REQUIRED to complete before Quote Done. A report that prints a full date is
taken, so the rule is checked against the value rather than assumed.

NOT claims, policies or coverage. The broker reads the DASH document itself
for those, so a partial copy here would be a second place to look rather than
a saving.
"""

import re
from datetime import date

# Azure renders DASH's tables as HTML. Cell boundaries are where one fact ends
# and the next begins, so they become spaces rather than being deleted —
# stripping them outright would run "M1A1A1" into the next label.
_TAG = re.compile(r"<[^>]+>")

# DASH prints this when it has no data. Kept as text wherever it is stored,
# never coerced to null: "the bureau has nothing" and "we have nothing" are
# different facts. Here it simply means "do not fill".
_NOT_AVAILABLE = "not available"

# A POLICY TERM ON THE SUMMARY: "2024-02-16 to 2026-02-16".
#
# Anchored on the word "to" between two ISO dates rather than on the "#1"
# marker beside it, for the reason _VEHICLE_LINE records below: DASH puts the
# marker and the term in separate table cells, so Azure can render them on
# separate lines. The pair of dates is what identifies the row.
_POLICY_TERM = re.compile(
    r"(?P<start>\d{4}-\d{2}-\d{2})\s+to\s+(?P<end>\d{4}-\d{2}-\d{2})"
)

# THE CAR ITSELF, not the "Vehicle #N:" marker in front of it.
#
#     2024 HONDA - CIVIC TOURING 4DR - 1AAAA1A1AAA000001
#
# Anchoring on the marker was the obvious approach and it silently lost a car.
# DASH prints the label and the car in separate table cells, so Azure renders
# them on separate lines and sometimes puts "Vehicle Code:" between them:
#
#     Vehicle #1: Vehicle Code:
#     2024 HONDA - CIVIC TOURING 4DR - 1AAAA1A1AAA000001
#
# The VIN is what actually identifies the line — 17 characters in DASH's own
# alphabet, which excludes I, O and Q — so matching backwards from it finds
# the car however the cell boundaries fell. Found by running the real report,
# not by reading: the first version returned one vehicle where the policy had
# two, and the parse looked healthy.
_VEHICLE_LINE = re.compile(
    r"(?P<make>\d{4}\s+[A-Z][^\n]*?)\s+-\s+(?P<vin>[A-HJ-NPR-Z0-9]{17})\b",
    re.IGNORECASE,
)

_DISTANCE = re.compile(
    r"Commuting Distance \(One-Way / Annual\):\s*(\d+)\s*km\s*/\s*(\d+)\s*km",
    re.IGNORECASE,
)

# The class TEXT, never the leading code. DASH prints "01 / Private Passenger
# Vehicle", "02 / ...", "03 / ..." for Pleasure, Commute and Business, and all
# three are private passenger cars. Matching the number would have excluded
# two thirds of them — checked against a real report rather than assumed.
_PRIVATE_PASSENGER = "private passenger vehicle"

_EFFECTIVE = re.compile(r"Effective Date:\s*(\d{4}-\d{2}-\d{2})")


# A person listed on a policy. The name stops at the next label or at a vehicle
# role, because Azure interleaves the two table columns —
#
#     Operator: FRANCIS,MILLAT F Vehicle #1: Principal Operator
#
# — so taking the rest of the line would capture "Principal Operator" as part
# of the name.
_OPERATOR = re.compile(
    r"Operator:\s*(?P<name>[^\n]*?)(?=\s+(?:Vehicle\s*#|DLN:|Relationship|Year of Birth|Start of)|\s*$)",
    re.MULTILINE,
)

# "DLN: V0303-26756-20728 Ontario". The province is optional: DASH prints it on
# every block in the reports seen so far, but a missing one is not a reason to
# lose the licence number, which is the field that identifies the person.
_DLN = re.compile(
    r"DLN:\s*(?P<number>[A-Z0-9-]+)(?:\s+(?P<province>[A-Z][a-zA-Z ]+?))?(?=\s+[A-Z][A-Za-z/ ]{2,30}:|\s+Vehicle\s*#|\s*$)",
    re.MULTILINE,
)


def _text(markdown: str) -> str:
    """Markdown to plain text, with cell boundaries preserved as spaces."""
    return _TAG.sub(" ", markdown)


def _field(text: str, label: str) -> str | None:
    """The value printed after `label:`, up to the next label or line end.

    DASH runs several facts onto one line ("Gender: Male Marital Status:
    Married"), so a match cannot simply take the rest of the line — it stops at
    the next `Word:` that looks like a label.
    """
    match = re.search(
        rf"{re.escape(label)}:\s*(?P<value>.+?)(?=\s+[A-Z][A-Za-z/ ]{{2,30}}:|\s*$)",
        text,
        re.MULTILINE,
    )
    if not match:
        return None
    value = " ".join(match.group("value").split())
    if not value or value.lower() == _NOT_AVAILABLE:
        return None
    return value


def parse_driver(markdown: str) -> dict:
    """The subject's own details, from the header above the first section.

    ONLY the header. The same labels appear again inside policy blocks for
    other operators, and reading those would fill the applicant's form with a
    co-insured's details — so everything from the first "## " onward is cut
    away before anything is read.
    """
    header = _text(markdown.split("\n## ", 1)[0])

    out: dict[str, str] = {}

    # The name is the first line that looks like one — printed alone, before
    # any "Label:" line. It carries DASH's own spacing ("SAMPLE,TESTCASE A"),
    # which is kept exactly: normalising it here would undo the reason this
    # runs on Azure rather than a model.
    for line in header.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or ":" in line:
            continue
        if line.isupper() and "," in line:
            out["name"] = line
            break

    # "DLN: S1234-56789-01234 Ontario" — number and province on one line.
    dln = _field(header, "DLN")
    if dln:
        parts = dln.split()
        out["license_number"] = parts[0]
        if len(parts) > 1:
            out["license_province"] = " ".join(parts[1:])

    for label, column in (("Gender", "gender"), ("Marital Status", "marital_status")):
        value = _field(header, label)
        if value:
            out[column] = value

    # AT-FAULT CLAIMS — shown to the broker and stored on the principal driver.
    #
    # DASH'S OWN COUNT. The report also prints a Claims table with a percentage
    # per claim (0%, 50%, 100%) and *THIRD PARTY* rows belonging to other
    # people; counting those would mean deciding whether 50% is at-fault and
    # whose claim is whose. This line has already made both judgements.
    #
    # READ DIRECTLY, AND ONLY DIRECTLY. It shares a physical line with its
    # neighbour in every report seen:
    #
    #   Number of Policies at this Address: Not available Number of At-Fault Claims in Last 6 Years: 0
    #
    # _field stops a value at the next `Label:` via [A-Z][A-Za-z/ ]{2,30}: —
    # letters, slash and space only. This label has a hyphen (At-Fault) and
    # digits (6 Years), so it cannot act as a stop-label: reading the NEIGHBOUR
    # would run straight through "Not available" and swallow this text. That is a
    # pre-existing bug in a field nothing reads, and widening the stop-class
    # would change where Gender, Marital Status and DLN stop. Target this label
    # itself and leave the regex alone.
    #
    # Measured before building: 25 of the 137 real reports in C:\Auto Insurance
    # were read and all 25 carried this line — values 0 (fifteen), 1 (nine),
    # 3 (one).
    at_fault = _field(header, "Number of At-Fault Claims in Last 6 Years")
    if at_fault:
        # INT, and the first numeric field this parser produces. Everything else
        # here is text; driver.at_fault_claims_6y is an int column (0114).
        #
        # A VALUE THAT IS NOT A NUMBER IS DROPPED, never coerced to 0. "No
        # at-fault claims" and "we could not read it" are opposite facts, and a
        # silent zero would send an unvetted lead to the premium dataset looking
        # spotless. _field already returns None for "Not available", so this
        # guard is for anything unexpected DASH prints instead.
        try:
            out["at_fault_claims_6y"] = int(at_fault.strip())
        except ValueError:
            pass

    # DATE OF BIRTH, ONLY IF IT IS A DATE. Every report seen so far prints the
    # YEAR alone ("Date of Birth: 1997"), and driver.date_of_birth is a date —
    # inventing a month and day would put a wrong value in a field the broker
    # is REQUIRED to complete before Quote Done. But a report that does print
    # a full date is worth taking, so the check is on the value rather than
    # assumed from the format.
    born = _field(header, "Date of Birth")
    if born:
        # PARSED, not pattern-matched. A regex checks the SHAPE — "1997-99-99"
        # has four digits, a dash, two digits — and Postgres rejects it later,
        # failing the whole update and taking the address and licence with it.
        # date.fromisoformat answers the only question worth asking, and
        # rejects a bare year on its own without a second check.
        try:
            out["date_of_birth"] = date.fromisoformat(born.strip()).isoformat()
        except ValueError:
            pass

    address = _field(header, "Address")
    if address:
        out.update(_split_address(address))

    return out


def _split_address(address: str) -> dict:
    """"1 EXAMPLE ST, , SAMPLETOWN ON M1A1A1" into its four parts.

    THE EMPTY PART IS REAL. DASH prints ", ," where a unit number would go, and
    Azure preserves it — so the split has to drop empty segments rather than
    treating the blank as the city. That is exactly the byte-faithfulness that
    made Azure worth using, and it would be wasted by a naive split.

    The last segment carries city, province and postcode together
    ("SAMPLETOWN ON M1A1A1"), so the province and postcode are taken from the
    end and whatever remains is the city.

    A NON-EMPTY MIDDLE SEGMENT JOINS THE STREET. That slot is where DASH puts
    a unit number, and it is usually blank — but "5 FESTIVAL DR, Unit 405,
    NORTH YORK ON M2R3T9" is a real address and the unit used to vanish
    silently. driver.unit_number exists as a column and has no control on New
    Policy, so writing it there would store something the broker cannot see;
    appending keeps it in a field they can read and edit. Same rule that kept
    license_province out.
    """
    parts = [p.strip() for p in address.split(",") if p.strip()]
    if not parts:
        return {}

    out = {"street_address": parts[0]}
    if len(parts) == 1:
        return out

    if len(parts) > 2:
        out["street_address"] = ", ".join(parts[:-1])

    tail = parts[-1].split()

    # A Canadian postcode, with or without the middle space.
    if tail and re.fullmatch(r"[A-Z]\d[A-Z]\s?\d[A-Z]\d", " ".join(tail[-2:]).upper()):
        out["postal_code"] = " ".join(tail[-2:])
        tail = tail[:-2]
    elif tail and re.fullmatch(r"[A-Z]\d[A-Z]\d[A-Z]\d", tail[-1].upper()):
        out["postal_code"] = tail[-1]
        tail = tail[:-1]

    if tail and re.fullmatch(r"[A-Z]{2}", tail[-1].upper()):
        out["province"] = tail[-1]
        tail = tail[:-1]

    city = " ".join(tail).strip()
    if city:
        out["city"] = city
    elif len(parts) > 2:
        # The city was its own segment rather than sharing the last one.
        out["city"] = parts[-2]

    return out


def _as_date(value: str) -> str | None:
    """An ISO date, or None if it is not one.

    PARSED, NOT PATTERN-MATCHED, for the reason parse_driver gives about
    date_of_birth: a regex checks the SHAPE, so "2026-99-99" passes it and
    Postgres rejects the row later - failing the whole update and taking the
    address and licence down with one bad policy date.
    """
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError:
        return None


def parse_policy_dates(markdown: str) -> dict:
    """The three dates a broker reads off the Policies summary.

    DASH prints the policy history newest first:

        #1  2024-02-16 to 2026-02-16   Allstate      Active
        #2  2018-02-16 to 2019-02-17   AVIVA         Cancelled
        #3  2016-02-16 to 2018-02-16   AVIVA         Expired

    so all three are corner values of that table:

        renewal_date           end of the FIRST term    2026-02-16
        insured_since          start of the LAST term   2016-02-16
        current_insurer_since  start of the FIRST term  2024-02-16

    THE SUMMARY ONLY. Every policy further down the report repeats its own
    Effective and Expiry dates, and reading those would mix per-policy detail
    into figures meant to describe the history as a whole - the same reason
    parse_driver cuts everything from the first "## " onward.

    NO GAP INTERPRETATION. The sample above has a five-year hole that DASH
    marks *POSSIBLE GAP*, and its own header then reports one year of
    continuous insurance rather than the ten the earliest start date implies.
    These are the dates as printed; whether a gap breaks continuity is a
    judgement the broker makes with the report in front of them.
    """
    marker = "\n## Policies"
    if marker not in markdown:
        return {}

    section = markdown.split(marker, 1)[1].split("\n## ", 1)[0]
    terms = _POLICY_TERM.findall(_text(section))
    if not terms:
        return {}

    first_start, first_end = terms[0]
    last_start, _ = terms[-1]

    out = {
        "renewal_date": _as_date(first_end),
        "current_insurer_since": _as_date(first_start),
        # A one-row table answers all three from that row, which is correct: a
        # driver with a single policy has been insured since it started.
        "insured_since": _as_date(last_start),
    }
    # Dropped rather than kept as None: the caller writes blank-only, and a
    # None would look like a value it had chosen not to fill.
    return {k: v for k, v in out.items() if v}


def parse_vehicles(markdown: str) -> list[dict]:
    """The cars on the MOST RECENT policy.

    Every vehicle on it, whoever the listed operator is — a policy's cars are
    the cars being insured, and matching them to one operator would drop the
    second car on a two-driver household.

    PRIVATE PASSENGER ONLY, judged on the class text. Commercial vehicles are
    on a different form and are not what this quote covers.

    Deduplicated by VIN: one report lists the same car on a policy and its
    renewal, and they are the same physical car.
    """
    blocks = _policy_blocks(markdown)
    if not blocks:
        return []

    text = _text(blocks[0])
    vehicles: list[dict] = []
    seen: set[str] = set()

    # Split on the detail lines so each car's own facts — its distance, its
    # class — are read from ITS segment rather than the whole policy's.
    for match in _VEHICLE_LINE.finditer(text):
        vin = match.group("vin").upper()
        if vin in seen:
            continue

        segment = text[match.start() : match.start() + 400]
        if _PRIVATE_PASSENGER not in segment.lower():
            continue

        seen.add(vin)
        vehicle = {
            "vin": vin,
            "year_make_model": " ".join(match.group("make").split()),
        }

        distance = _DISTANCE.search(segment)
        if distance:
            vehicle["one_day_distance_km"] = int(distance.group(1))
            vehicle["annual_km"] = int(distance.group(2))

        vehicles.append(vehicle)

    return vehicles


def parse_operators(markdown: str) -> list[dict]:
    """The OTHER people on the most recent policy — never the subject.

    Same block the vehicles come from, for the same reason: the most recent
    policy is the household as it stands now. An operator on a 2016 policy may
    have moved out, and adding them would put a stranger on the quote.

    THREE FIELDS, and only three. DASH prints a name, a licence and a province
    per operator and nothing else we have a home for:

        Operator: VALLOMKUNNEL,FRANCIS J
        DLN: V0303-26756-20728 Ontario
        Relationship to Policyholder: NA
        Year of Birth: 1962

    Not the G1/G2/G dates, which the report does not carry for ANYONE — its
    only licensing figure is the subject's own "Years Licensed", a count. The
    broker types those, as they already do for the applicant.

    Not "Relationship to Policyholder": that is the relationship to whoever
    owns the policy, who need not be our applicant, and it reads "NA" on every
    block of the real report anyway.

    Not "Year of Birth": a year, and date_of_birth is a date.

    THE SUBJECT IS EXCLUDED BY LICENCE NUMBER. He is listed as an operator on
    his own policy — the real report has FRANCIS,MILLAT F alongside
    VALLOMKUNNEL,FRANCIS J — so taking every operator would create a second
    copy of the applicant as an "additional driver". Matched on the DLN rather
    than the name because names vary within one report: the same man is
    "VALLOMKUNNEL,FRANCIS J" on one policy and "Francis Vallomkunnel" on
    another. Licence numbers do not vary.
    """
    blocks = _policy_blocks(markdown)
    if not blocks:
        return []

    subject = (parse_driver(markdown).get("license_number") or "").upper()

    text = _text(blocks[0])
    # FAIL CLOSED. Without the subject's own licence there is nothing to
    # exclude him BY, and he is listed as an operator on his own policy — so
    # importing anyway would put the applicant on screen twice, from his own
    # report. Returning nothing costs the broker some typing; the alternative
    # costs them a duplicate they have to notice and delete.
    #
    # The applicant's own fields and the vehicles do not depend on this and
    # are filled either way (dash_apply.apply_report).
    if not subject:
        return []

    operators: list[dict] = []

    # Each operator's facts run from their own marker to the next one. The
    # fields are NOT on tidy consecutive lines: Azure reads DASH's two table
    # columns in reading order, so a vehicle role lands between the name and
    # the licence —
    #
    #     Operator: FRANCIS,MILLAT F Vehicle #1: Principal Operator
    #     DLN: F7172-55339-70224 Ontario
    #
    # — which is why the DLN is searched for within the segment rather than
    # read from the line below. The same interleaving is what made the first
    # vehicle parser lose a car.
    for match in _OPERATOR.finditer(text):
        segment = text[match.end() : _next_operator(text, match.end())]

        name = " ".join(match.group("name").split())
        if not name:
            continue

        # Whatever is printed, taken. A licence-less operator is still a name
        # the broker does not have to type — the point of this is to ask the
        # customer for less, not to build a register of verified people.
        operator = {"name": name}

        dln = _DLN.search(segment)
        if dln:
            if dln.group("number").upper() == subject:
                # The subject of the report, listed on his own policy.
                continue
            operator["license_number"] = dln.group("number")
            province = dln.group("province")
            if province:
                operator["license_province"] = province.strip()

        operators.append(operator)

    return operators


def _next_operator(text: str, start: int) -> int:
    """Where this operator's facts stop — the next block, or the vehicles."""
    ends = [
        m.start()
        for m in (_OPERATOR.search(text, start), _VEHICLE_LINE.search(text, start))
        if m
    ]
    return min(ends) if ends else len(text)


def _policy_blocks(markdown: str) -> list[str]:
    """The policy detail blocks, most recent first.

    Ordered by effective date rather than by the "#1, #2" DASH prints — the
    numbering is DASH's own and nothing promises it is chronological. A block
    with no readable date sorts last rather than being dropped, since a
    missing date is not a reason to lose a car.
    """
    parts = re.split(r"(?=Policy #:)", markdown)
    blocks = [p for p in parts if "Policy #:" in p]

    def effective(block: str) -> str:
        match = _EFFECTIVE.search(_text(block))
        return match.group(1) if match else ""

    return sorted(blocks, key=effective, reverse=True)
