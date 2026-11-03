"""Which file a document belongs to and what a file still needs. No I/O.

Every route stays within the submitting broker's own files: `files` must only
ever hold that broker's. Build plan, section 5.
"""

import re

_REFERENCE = re.compile(r"\bSR-\d+\b", re.IGNORECASE)


def reference(subject: str | None) -> str | None:
    match = _REFERENCE.search(subject or "")
    return match.group(0).upper() if match else None


def decide(
    doc_type: str, licences: set[str], ref: str | None, files: list[dict]
) -> tuple[str, dict | None, str]:
    """(attach | new | hold | manual | which_file, the file, why)."""
    if ref:
        file = next((f for f in files if f["reference"] == ref), None)
        if not file:
            return "manual", None, f"{ref} is not one of this broker's files"
        if not licences & file["licences"]:
            return "manual", file, f"The licence on this document does not fit {ref}"
        return "attach", file, ""

    fits = [f for f in files if licences & f["licences"]]
    if len(fits) > 1:
        return "which_file", None, "The licence matches more than one open file"
    if fits:
        return "attach", fits[0], ""
    if doc_type == "application":
        return "new", None, ""
    return "hold", None, ""


def missing(drivers: list[dict], slots: set[tuple[str, str]]) -> list[str]:
    """One line per report still needed, in driver order."""
    lines = []
    for driver in sorted(drivers, key=lambda d: d["driver_no"]):
        for doc_type, label in (("dash", "DASH"), ("mvr", "MVR")):
            if (doc_type, driver["licence_no"]) not in slots:
                lines.append(f"{label} for driver {driver['driver_no']}, {driver['name']}")
    return lines
