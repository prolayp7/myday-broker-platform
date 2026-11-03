"""The exact calculations on a DASH report. Pure functions, no I/O.

Claude reads the report and makes the judgement calls; everything here is
arithmetic on what it read, and lives in Python because arithmetic should give
the same answer every time and be provable by a test. The split was agreed
field by field — see the DASH parsing plan, Sections 1, 4, 7 and 8.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation

from meta_capi import _PROVINCE_CODES

# ── Licence (Ontario DLN) ──────────────────────────────────────────────────

_ONTARIO_DLN = re.compile(r"^[A-Z]\d{4}-?\d{5}-?\d{5}$")


def normalize_dln(value: str | None) -> str:
    """A licence number reduced to what identifies it: no dashes, spaces or case."""
    return re.sub(r"[\s-]", "", value or "").upper()


def _is_ontario(dln: str | None, province: str | None) -> bool:
    return bool(
        dln
        and _ONTARIO_DLN.match(dln.strip().upper())
        and (province or "").strip().lower() in ("ontario", "on")
    )


def _as_date(value: str | None) -> date | None:
    """An ISO date, or None. Parsed rather than pattern-matched: a regex
    accepts "2026-99-99", and Postgres rejecting it later fails the whole
    driver update."""
    try:
        return date.fromisoformat((value or "").strip())
    except ValueError:
        return None


def licence_facts(
    dln: str | None, province: str | None, printed_dob: str | None
) -> tuple[date | None, str | None]:
    """Date of birth and gender encoded in an Ontario licence number.

    The last six digits are YYMMDD, with 50 added to the month for a woman:
    S2068-40208-35221 → 83 52 21 → 1983-02-21, female. Verified against the
    printed date on four real reports.

    TRUSTED ONLY WHEN THE YEAR AGREES. The century comes from the printed year,
    and the two-digit year must match it — a misread digit or a licence typed
    for someone else shows up as a mismatch, and then nothing is taken from the
    licence at all.
    """
    if not _is_ontario(dln, province):
        return None, None
    year_text = (printed_dob or "").strip()[:4]
    if not year_text.isdigit():
        return None, None

    digits = normalize_dln(dln)[-6:]
    yy, mm, dd = int(digits[:2]), int(digits[2:4]), int(digits[4:])
    if yy != int(year_text) % 100:
        return None, None

    gender = "Female" if mm > 50 else "Male"
    if mm > 50:
        mm -= 50
    try:
        born = date(int(year_text), mm, dd)
    except ValueError:
        return None, None
    return born, gender


def date_of_birth(
    printed: str | None, dln: str | None, province: str | None
) -> str | None:
    """A full printed date as is; a year alone completed from the licence."""
    full = _as_date(printed)
    if full:
        return full.isoformat()
    born, _ = licence_facts(dln, province, printed)
    return born.isoformat() if born else None


# ── Address ────────────────────────────────────────────────────────────────

# The codes only. The table also maps full names ("ontario"), which must not
# count as a code here.
_PROVINCE_CODE_SET = {code.upper() for code in _PROVINCE_CODES.values()}
_POSTAL = re.compile(r"^[A-Z]\d[A-Z]\d[A-Z]\d$")


def _words(text: str | None) -> list[str]:
    return re.findall(r"[A-Z0-9]+", (text or "").upper())


def address_problems(
    street: str | None, city: str | None, province: str | None, postal_code: str | None
) -> list[str]:
    """What looks wrong in an address split, for the broker to check.

    FLAGS ONLY, never fixes: a guessed town is a wrong rating factor and a wrong
    match key for Meta. Catches the shapes seen in real data — "235 Clarke St
    Oshawa" with city "ON", a whole line left in the street, "406 HAMILTON" —
    not every wrong town. A plausible wrong town passes.
    """
    problems = []

    # Peeled from the end, whole words only, so the street is flagged whether
    # or not the postal field was filled.
    tail = _words(street)
    leftover = False
    if len(tail) >= 2 and _POSTAL.match(tail[-2] + tail[-1]):
        tail, leftover = tail[:-2], True
    elif tail and _POSTAL.match(tail[-1]):
        tail, leftover = tail[:-1], True
    if tail and tail[-1] in _PROVINCE_CODE_SET:
        tail, leftover = tail[:-1], True
    town = _words(city)
    if town and len(tail) > len(town) and tail[-len(town):] == town:
        leftover = True
    if leftover:
        problems.append("the street still holds the town, province or postal code")

    city_text = (city or "").strip()
    if city_text.upper() in _PROVINCE_CODE_SET:
        problems.append(f'the city is "{city_text}", a province code')
    elif any(ch.isdigit() for ch in city_text):
        problems.append(f'the city "{city_text}" has a number in it')

    province_text = (province or "").strip()
    if province_text and province_text.upper() not in _PROVINCE_CODE_SET:
        problems.append(f'the province "{province_text}" is not a 2-letter province code')

    return problems


# ── Dates ──────────────────────────────────────────────────────────────────


def add_years(day: date, years: int) -> date:
    """`day` moved by whole years. Feb 29 lands on Feb 28 in a common year."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def renewal_date(report_date: str | None, newest_end: str | None) -> str | None:
    """The renewal date, corrected for an insurer that pre-renewed.

    About a month before renewal insurers process the next term, and DASH then
    shows NEXT year's end date — 21 of 137 sample reports did. An end date more
    than twelve months after the report can only be that, so it goes back a
    year. An end date already in the past means there is no policy to renew,
    and the field is left blank (18 of 137).
    """
    reported, end = _as_date(report_date), _as_date(newest_end)
    if not end:
        return None
    if not reported:
        return end.isoformat()
    if end < reported:
        return None
    if end > add_years(reported, 1):
        return add_years(end, -1).isoformat()
    return end.isoformat()


def within_years(day: date, report_date: date, years: int) -> bool:
    """On or after the same date `years` before the report."""
    return day >= add_years(report_date, -years)


# ── Insurance gaps (Section 7) ─────────────────────────────────────────────

GAP_REASONS = ("Non-payment", "Possible new vehicle", "Other")


def find_gaps(terms: list[dict]) -> list[dict]:
    """Every break in the main driver's coverage, with its reason.

    MERGED, NOT PAIRWISE. Comparing each term only with the next one invents a
    gap when policies overlap: Jan–Dec, Mar–Apr, Jun–Dec has none, but Apr→Jun
    looks like one. So the terms are walked in start order against the LATEST
    end seen so far, and a gap is only a day no term covers — even a single day.

    The reason comes from the term that ended it: cancelled for non-payment, or
    else a vehicle on the next policy that was not on that one, or else Other.

    All of them, however old: the screen strikes through the ones past the
    window rather than hiding them.
    """
    rows = []
    for term in terms:
        start, end = _as_date(term.get("start")), _as_date(term.get("end"))
        if start and end:
            rows.append((start, end, term))
    rows.sort(key=lambda r: (r[0], r[1]))
    if not rows:
        return []

    gaps = []
    covered_to, last = rows[0][1], rows[0][2]
    for start, end, term in rows[1:]:
        if start > covered_to:
            gaps.append({
                "from": covered_to.isoformat(),
                "to": start.isoformat(),
                "days": (start - covered_to).days,
                "reason": _gap_reason(last, term),
            })
        if end >= covered_to:
            covered_to, last = end, term
    return gaps


def _gap_reason(before: dict, after: dict) -> str:
    if "non-payment" in (before.get("status") or "").lower():
        return "Non-payment"
    vins_before = {v.upper() for v in before.get("vins") or []}
    vins_after = {v.upper() for v in after.get("vins") or []}
    if vins_after - vins_before:
        return "Possible new vehicle"
    return "Other"


# ── Claims (Section 8) ─────────────────────────────────────────────────────

# DASH kind-of-loss codes (IBC list, Oct 12 2023) into the four boxes a quote
# asks for. Every code has exactly one box.
_BOX_CODES = {
    "tp_bi": ["01", "02", "03", "04", "05", "06", "35", "36", "39"],
    "tp_pd": ["09", "14", "15", "16", "17", "18", "19", "38"],
    "ab": (
        ["07", "30", "31", "32", "34", "37"]
        + [str(c) for c in range(40, 50)]
        + [str(c) for c in range(60, 70)]
        + [str(c) for c in range(80, 88)]
        + [str(c) for c in range(90, 98)]
    ),
    "coll": ["12", "20", "21", "22", "23", "24", "25", "26", "27", "28", "29", "2A"],
}
KOL_BOX = {code: box for box, codes in _BOX_CODES.items() for code in codes}
BOXES = ("tp_bi", "tp_pd", "ab", "coll")


def _money(value) -> Decimal:
    """A DASH amount ("$24,307.00", 24307, None) as an exact Decimal."""
    text = re.sub(r"[$,\s]", "", str(value if value is not None else "0"))
    try:
        return Decimal(text or "0")
    except InvalidOperation:
        return Decimal("0")


def claim_amounts(kol_lines: list[dict]) -> tuple[dict[str, Decimal], list[str]]:
    """Loss + Expense of every KOL line, added into its box.

    Returns the four totals and the codes no box knows — those are kept so the
    screen can show them, not silently dropped.
    """
    totals = {box: Decimal("0") for box in BOXES}
    unknown = []
    for line in kol_lines:
        code = str(line.get("code") or "").upper().removeprefix("KOL").strip()
        amount = _money(line.get("loss")) + _money(line.get("expense"))
        box = KOL_BOX.get(code)
        if box:
            totals[box] += amount
        else:
            unknown.append(code)
    return totals, unknown


def claim_totals_match(kol_lines: list[dict], printed_loss, printed_expense) -> bool:
    """Whether the KOL lines add up to DASH's own printed Total Loss + Expense.

    A mismatch means a line was misread or missed, so the claim is flagged for
    the broker to check against the PDF. No printed totals → nothing to check.
    """
    if printed_loss is None and printed_expense is None:
        return True
    read = sum((_money(l.get("loss")) + _money(l.get("expense")) for l in kol_lines), Decimal("0"))
    return read == _money(printed_loss) + _money(printed_expense)
