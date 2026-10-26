"""Request and response shapes for lead cost.

Money is Decimal here and leaves as a string in the computed responses — the
house convention (services/commission.py, schemas/premium_history.py).
PostgREST hands numerics back as strings, and a float would reintroduce the
drift `numeric` exists to prevent.
"""

from decimal import Decimal
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# The vocabulary the CHECK in 0088 enforces. Declared here too so a bad reason
# is a 422 naming the allowed values, rather than a database constraint error.
CreditReason = Literal["Wrong number", "Duplicate", "Test lead", "Other"]


class LeadCostSettingsResponse(BaseModel):
    cost_per_lead: Decimal


class UpdateLeadCostSettingsRequest(BaseModel):
    """extra='forbid' so a typo'd field name is refused rather than ignored —
    silently dropping `cost` when the field is `cost_per_lead` would look like
    a save that did nothing."""

    model_config = ConfigDict(extra="forbid")

    # max_digits/decimal_places MATCH THE COLUMN, which is numeric(10,2).
    # Without them 20.999 validated, Postgres rounded it, and every later
    # delivery was billed at 21.00 — a price the admin never typed and the
    # screen never showed them. Refused up front instead.
    cost_per_lead: Decimal = Field(ge=0, max_digits=10, decimal_places=2)


class AddCreditRequest(BaseModel):
    """What the admin sends to take a lead off the bill.

    NO AMOUNT AND NO BROKER. The backend credits exactly the delivery's own
    cost_at_delivery and derives the broker from the month. A client able to
    name either could bill the wrong person the wrong figure.
    """

    model_config = ConfigDict(extra="forbid")

    lead_id: str
    reason: CreditReason


class BrokerCostSummary(BaseModel):
    broker_id: str
    name: str
    leads: int
    allocated: str
    credited: str
    charged: str


class GroupCostResponse(BaseModel):
    month: str
    is_open: bool
    # Deliveries with no price stamped — a configuration failure, counted
    # separately so it can never be read as "these leads were free".
    unpriced_count: int
    brokers: list[BrokerCostSummary]
    total_allocated: str
    total_credited: str
    total_charged: str


class LedgerLine(BaseModel):
    lead_id: str
    name: str | None
    phone: str | None
    delivered_at: str
    cost: str
    unpriced: bool
    credit_reason: str | None


class LedgerResponse(BaseModel):
    month: str
    is_open: bool
    broker_id: str
    unpriced_count: int
    # YYYY-MM, Ontario. The screen stops its month list here so a broker is not
    # offered months that ended before they were onboarded.
    broker_since_month: str
    lines: list[LedgerLine]
    allocated: str
    credited: str
    charged: str
