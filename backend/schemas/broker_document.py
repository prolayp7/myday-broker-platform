from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel

# The three documents onboarding needs proof of. Matches the check constraint
# on broker_document.kind in migration 0070.
#
# A CLOSED SET, not free text. Every kind has a place on the screen and a
# meaning to compliance; an arbitrary "other" would be a filing cabinet, which
# is a different feature from proving a broker is licensed and insured.
DocumentKind = Literal["photo", "ribo_license", "eo_certificate"]

KIND_LABEL: dict[str, str] = {
    "photo": "Photo",
    "ribo_license": "RIBO licence",
    "eo_certificate": "E&O certificate",
}

# The label as it reads MID-SENTENCE, with its article. Written out rather than
# derived: lower-casing the labels above turns RIBO into "ribo" and E&O into
# "e&o", and picking a/an by first letter gets "a E&O" wrong — the article
# follows how the acronym is SAID ("an E-and-O"), not how it is spelled.
KIND_PHRASE: dict[str, str] = {
    "photo": "A photo",
    "ribo_license": "A RIBO licence",
    "eo_certificate": "An E&O certificate",
}

# Generous for a licence scan or a phone photo, small enough that nobody
# uploads a video by accident. Enforced against the bytes actually read, never
# a declared Content-Length — a client controls that header.
MAX_BYTES = 10 * 1024 * 1024

_IMAGES = frozenset({"image/jpeg", "image/png"})
_PDF = frozenset({"application/pdf"})

# What each kind will accept. The photo is deliberately narrower: it renders in
# an <img> in the header, and a PDF there is a broken image on every screen.
ALLOWED_TYPES: dict[str, frozenset[str]] = {
    "photo": _IMAGES,
    "ribo_license": _IMAGES | _PDF,
    "eo_certificate": _IMAGES | _PDF,
}

# For building the object key. Only extensions for types on the allow-list —
# the map is never reached for anything ALLOWED_TYPES has already refused.
EXTENSION: dict[str, str] = {
    "image/jpeg": "jpg",
    "image/png": "png",
    "application/pdf": "pdf",
}


class BrokerDocumentResponse(BaseModel):
    """What the browser is told about a stored document.

    DENY BY DEFAULT — the fields are listed rather than derived from the table,
    so a column added later is not published by accident. object_path is
    absent on purpose: it is an internal locator, and the only legitimate way
    to reach the bytes is a signed URL issued per request.
    """

    id: UUID
    kind: DocumentKind
    file_name: str
    content_type: str
    size_bytes: int
    uploaded_at: datetime
