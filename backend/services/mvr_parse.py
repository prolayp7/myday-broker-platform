"""Reads the register line out of an MVR, from Azure's markdown.

THE CGI RAPIDWEB PRINTOUT ONLY ("MVRPrintAbstracts"), which is what brokers
save from the MTO portal. Azure returns its labelled fields as table cells —
`<td>Licence Number :</td><td>J4273-00008-60108</td>` — and sometimes puts a
space before the colon, sometimes not. So the tags are flattened to spaces and
each label matched with an optional space.

Only four facts are read, because the register needs only four: licence
number, name, the date it was requested, and the receipt number (the ARIS
order number). Convictions, class, demerit points and the rest stay on the MVR
itself. Nothing here writes to a driver.

Pure functions, no I/O — tested from a fixture with no Azure call.
"""

import re
from datetime import date

_TAG = re.compile(r"<[^>]+>")

# Each abstract opens with this heading. A PDF holding several abstracts is one
# register line per abstract, so the text is split on it.
_BLOCK = re.compile(r"ONTARIO\s+Driving\s+Record", re.IGNORECASE)

_LICENCE = re.compile(r"Licence\s+Number\s*:\s*([A-Z][0-9]{4}-?[0-9]{5}-?[0-9]{5})")
# The name runs until the next label, which on this printout is Gender (or
# Birth Date when the layout puts that column first).
_NAME = re.compile(r"Name\s*:\s*(.+?)\s+(?:Gender|Birth\s+Date|Address)\s*:")
_REQUESTED_ON = re.compile(r"Requested\s+On\s*:\s*([0-9]{2})/([0-9]{2})/([0-9]{4})")
_RECEIPT = re.compile(r"Receipt\s+Number\s*:\s*([0-9]{6,})")


def _text(markdown: str) -> str:
    """Markdown to one line of plain text, cell boundaries kept as spaces."""
    return " ".join(_TAG.sub(" ", markdown).split())


def _dmy(match: re.Match | None) -> date | None:
    """dd/mm/yyyy, the order this printout uses for every date.

    Read explicitly rather than with a lenient parser: 08/01/2025 is the 8th of
    January here, and a parser guessing month-first would file it in August.
    """
    if not match:
        return None
    day, month, year = (int(g) for g in match.groups())
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _name(block: str) -> str | None:
    match = _NAME.search(block)
    if not match:
        return None
    # "JIJO JOY," — the printout puts the comma after a surname with no given
    # name. Trailing punctuation is noise; the comma between parts is not.
    value = match.group(1).strip().rstrip(",").strip()
    return value or None


def parse(markdown: str) -> list[dict]:
    """One dict per abstract that can go in the register.

    Each has licence_number (dashes removed, as the register writes it),
    driver_name, requested_on (a date, or None) and aris_order_number.

    An abstract missing its licence or receipt number is skipped: without the
    licence the line names nobody, and without the receipt it cannot be told
    apart from the same MVR uploaded again. An empty list means "this is not an
    MVR we can read" — the caller says so to the broker.
    """
    text = _text(markdown)
    parts = _BLOCK.split(text)
    if len(parts) < 2:
        return []

    # The summary table ABOVE the first abstract lists every licence with its
    # receipt ("J42730000860108 25/04/2026 Receipt Number : 600935396"). Used
    # when an abstract's own trailing receipt is missing.
    summary = parts[0]

    out: list[dict] = []
    for block in parts[1:]:
        licence = _LICENCE.search(block)
        if not licence:
            continue
        licence_number = licence.group(1).replace("-", "")

        receipt = _RECEIPT.search(block)
        if not receipt:
            receipt = re.search(
                rf"{licence_number}\s+.*?Receipt\s+Number\s*:\s*([0-9]{{6,}})", summary
            )
        if not receipt:
            continue

        out.append({
            "licence_number": licence_number,
            "driver_name": _name(block),
            "requested_on": _dmy(_REQUESTED_ON.search(block)),
            "aris_order_number": receipt.group(1),
        })
    return out
