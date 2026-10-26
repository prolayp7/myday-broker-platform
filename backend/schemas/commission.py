from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class CommissionRatesResponse(BaseModel):
    """The two rates every projection is multiplied by, and the broker's share
    of the result (0134)."""

    auto_rate_pct: Decimal
    property_rate_pct: Decimal
    broker_share_pct: Decimal


class UpdateCommissionRatesRequest(BaseModel):
    """Change the rate card.

    EVERY FIGURE, ALWAYS REQUIRED. They are one card — read together on every
    projection — so a partial update would leave a moment where one is the new
    value and one the old, and any report opened in between would show a
    combination nobody chose.

    Decimal, never float: these multiply money. 0-100 because they are
    percentages typed the way an admin says them (15 means 15%), and because a
    rate entered as 1500 instead of 15 would report a projection a hundred
    times too large — which a broker would believe before they questioned it.

    extra="forbid" so a body carrying a homeowner or tenant rate is REFUSED
    rather than silently ignored. There is one property rate on purpose, and
    somebody sending three should be told so, not handed a 200.
    """

    model_config = ConfigDict(extra="forbid")

    auto_rate_pct: Decimal = Field(ge=0, le=100)
    property_rate_pct: Decimal = Field(ge=0, le=100)
    # What the broker keeps of a policy's commission (0134). Part of the same
    # card, so it is required with the other two for the same reason.
    broker_share_pct: Decimal = Field(ge=0, le=100)
