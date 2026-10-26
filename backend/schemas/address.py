"""Shapes for address lookup. Nothing here is stored — these describe a call
out to Google Places and the parsed answer coming back, not a table."""

from pydantic import BaseModel


class AddressSuggestion(BaseModel):
    """One line in the dropdown as the broker types."""

    # Google's opaque id for this place. Sent straight back to
    # GET /address/{place_id} to fetch the parsed fields — never stored, and
    # meaningless to anything but Google.
    place_id: str
    # The whole address on one line, e.g. "50 Pinnacle Dr, Kitchener, ON,
    # Canada" — what the broker reads and clicks.
    description: str


class AddressDetail(BaseModel):
    """A chosen address, already split into the columns driver actually has.

    Mirrors driver.street_address/city/province/postal_code exactly, so the
    frontend can PATCH this object through without a translation step.

    unit_number is absent on purpose: Google does not return one. A unit is
    something the customer says out loud ("apartment 12") and the broker types
    — see routes/address.py.
    """

    street_address: str | None = None
    city: str | None = None
    province: str | None = None
    postal_code: str | None = None
    # The full formatted line, for confirming the pick was the intended one.
    formatted: str | None = None
