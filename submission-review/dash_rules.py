"""The exact calculations on a DASH report. Pure functions, no I/O.

Copied from MyDay's backend/services/dash_rules.py so a change here can never
affect MyDay. Same code, trimmed to what the review uses.
"""

import re
from datetime import date
from decimal import Decimal, InvalidOperation


def normalize_dln(value: str | None) -> str:
    """A licence number reduced to what identifies it: no dashes, spaces or case."""
    return re.sub(r"[\s-]", "", value or "").upper()


def add_years(day: date, years: int) -> date:
    """`day` moved by whole years. Feb 29 lands on Feb 28 in a common year."""
    try:
        return day.replace(year=day.year + years)
    except ValueError:
        return day.replace(year=day.year + years, day=28)


def within_years(day: date, from_date: date, years: int) -> bool:
    """On or after the same date `years` before. The anniversary itself counts."""
    return day >= add_years(from_date, -years)


# DASH kind-of-loss codes (IBC list, Oct 12 2023) into the four boxes.
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


def money(value) -> Decimal:
    """A DASH amount ("$24,307.00", 24307, None) as an exact Decimal."""
    text = re.sub(r"[$,\s]", "", str(value if value is not None else "0"))
    try:
        return Decimal(text or "0")
    except InvalidOperation:
        return Decimal("0")


def claim_amounts(kol_lines: list[dict]) -> tuple[dict[str, Decimal], list[str]]:
    """Loss + Expense of every KOL line, added into its box, and any unknown codes."""
    totals = {box: Decimal("0") for box in BOXES}
    unknown = []
    for line in kol_lines:
        code = str(line.get("code") or "").upper().removeprefix("KOL").strip()
        amount = money(line.get("loss")) + money(line.get("expense"))
        box = KOL_BOX.get(code)
        if box:
            totals[box] += amount
        else:
            unknown.append(code)
    return totals, unknown


def claim_totals_match(kol_lines: list[dict], printed_loss, printed_expense) -> bool:
    """Whether the KOL lines add up to DASH's own printed Total Loss + Expense."""
    if printed_loss is None and printed_expense is None:
        return True
    read = sum(
        (money(l.get("loss")) + money(l.get("expense")) for l in kol_lines),
        Decimal("0"),
    )
    return read == money(printed_loss) + money(printed_expense)
