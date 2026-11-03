"""Request shapes for reading a DASH report."""

from pydantic import BaseModel, HttpUrl


class DashUrlRequest(BaseModel):
    """A link to a DASH report the broker already has open.

    HttpUrl rather than str: the value is fetched server-side, so anything
    that is not a real http(s) URL should be refused at the door rather than
    handed to an HTTP client to interpret.
    """

    url: HttpUrl
    # The broker's "Use it anyway" after a wrong-lead warning.
    confirm: bool = False


class DashClaimLeftOut(BaseModel):
    left_out: bool
