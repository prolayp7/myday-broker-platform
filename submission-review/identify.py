"""Works out a document's type from fixed wording on the page. No Claude."""

import re

_MARKERS = (
    ("application", r"Ontario\s+Application\s+for\s+Automobile\s+Insurance|ON1001e"),
    ("dash", r"Number\s+of\s+Claims\s+in\s+Last\s+6\s+Years"),
    ("mvr", r"ONTARIO\s+Driving\s+Record|MVRPrintAbstracts"),
)


def document_type(text: str) -> str:
    """application, dash, mvr or other. Two types at once is other: a person decides."""
    found = [name for name, marker in _MARKERS if re.search(marker, text, re.IGNORECASE)]
    return found[0] if len(found) == 1 else "other"
