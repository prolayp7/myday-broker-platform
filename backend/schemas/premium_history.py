from datetime import date, datetime
from decimal import Decimal
from uuid import UUID

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# Decimal, never float — money. Matches schemas/property.py's contents_value,
# the only other numeric in this codebase. Bare `numeric` in SQL, so no
# precision is imposed here either; introducing 2dp quantisation would be a
# new convention and is deliberately not smuggled in with this change.


class SavePremiumRequest(BaseModel):
    """What a broker types on New Policy — the figures and the facts about the
    quote.

    TWELVE PREMIUM FIELDS, SIX PAIRS. Each product on each side has a monthly
    and an annual field, and the broker's unit says which one they typed into
    (0106). The client sends ONLY the typed one; the database derives the
    other. Sending both is allowed by this model but pointless — the trigger
    overwrites the derived half from the typed half.

    NOTHING IS CONVERTED BEFORE IT ARRIVES. A monthly figure is sent as a
    monthly figure, to the monthly column. Until 0106 the form multiplied by
    twelve first, or later sent a flag saying "read this as monthly", and both
    arrangements put the ×12 rule in application code where four separate
    copies of it drifted apart. The UI displays; Postgres calculates.

    The totals and the savings stay OUT of this model, as they have been since
    0099: they are generated columns, and accepting them would let a client
    send a total that disagrees with its own parts.

    extra="forbid" IS LOAD-BEARING, NOT TIDINESS. This model silently ignored
    unknown fields until 0099. A browser tab left open across that deploy
    would POST current_auto_premium_monthly, be told 200, and save nothing —
    the broker sees a successful save and loses the figure, which is worse
    than any error. Refused outright instead, the same guard
    UpdateLeadCostSettingsRequest carries for the same reason.

    Every field optional, and the service dumps with exclude_unset: a broker
    fills this in over several conversations, so omitting a field must leave
    it alone while sending an explicit null clears it. Same patch semantics as
    schemas/property.py.

    Condo policies use the HOMEOWNER pair — a condo is owner-occupied
    property, and a separate pair would be a distinction nothing uses.
    """

    model_config = ConfigDict(extra="forbid")

    # The pairs, listed together so a product's two halves are never edited
    # apart. Whichever one the broker's unit names is the typed figure; the
    # other is derived by the database and need not be sent.
    current_auto_premium_annual: Decimal | None = Field(None, ge=0)
    current_auto_premium_monthly: Decimal | None = Field(None, ge=0)
    new_auto_premium_annual: Decimal | None = Field(None, ge=0)
    new_auto_premium_monthly: Decimal | None = Field(None, ge=0)
    current_homeowner_premium_annual: Decimal | None = Field(None, ge=0)
    current_homeowner_premium_monthly: Decimal | None = Field(None, ge=0)
    new_homeowner_premium_annual: Decimal | None = Field(None, ge=0)
    new_homeowner_premium_monthly: Decimal | None = Field(None, ge=0)
    current_tenant_premium_annual: Decimal | None = Field(None, ge=0)
    current_tenant_premium_monthly: Decimal | None = Field(None, ge=0)
    new_tenant_premium_annual: Decimal | None = Field(None, ge=0)
    new_tenant_premium_monthly: Decimal | None = Field(None, ge=0)

    policy_number: str | None = Field(None, max_length=100)
    effective_date: date | None = None

    # HOW THE BROKER TYPED THE FIGURES, saved the moment they click Year or
    # Month so the choice survives a reload. Nothing converts here — the six
    # figures are stored exactly as entered and stay drafts until the lead
    # reaches Quote Done, which is where the multiplication happens (0101).
    #
    # No default: this model patches with exclude_unset, so omitting a unit
    # must leave the stored one alone rather than silently resetting it to
    # year and making a monthly figure read as annual.
    current_premium_unit: Literal["year", "month"] | None = None
    new_premium_unit: Literal["year", "month"] | None = None


class PremiumResponse(BaseModel):
    """The row as the screen reads it: what was typed, plus everything
    Postgres derived from it."""

    id: UUID
    lead_id: UUID

    # BOTH READINGS OF EVERY PRODUCT, always populated (0106). One half of
    # each pair is what the broker typed and the other is what the database
    # derived, and the response does not distinguish them — the screen shows
    # whichever the unit calls for and never divides anything itself.
    current_auto_premium_annual: Decimal | None = None
    current_auto_premium_monthly: Decimal | None = None
    new_auto_premium_annual: Decimal | None = None
    new_auto_premium_monthly: Decimal | None = None
    current_homeowner_premium_annual: Decimal | None = None
    current_homeowner_premium_monthly: Decimal | None = None
    new_homeowner_premium_annual: Decimal | None = None
    new_homeowner_premium_monthly: Decimal | None = None
    current_tenant_premium_annual: Decimal | None = None
    current_tenant_premium_monthly: Decimal | None = None
    new_tenant_premium_annual: Decimal | None = None
    new_tenant_premium_monthly: Decimal | None = None

    # Generated by Postgres — read here, never written. Plain sums since 0106:
    # the annual columns are genuinely annual, so no unit multiplier is
    # applied here or anywhere else.
    total_current_premium_annual: Decimal | None = None
    total_current_premium_monthly: Decimal | None = None
    total_new_premium_annual: Decimal | None = None
    total_new_premium_monthly: Decimal | None = None
    savings_annual: Decimal | None = None
    savings_monthly: Decimal | None = None

    policy_number: str | None = None
    # Set when the broker marks the policy Issued (Stage 2), not here.
    issued_at: date | None = None
    effective_date: date | None = None

    # What the Year/Month buttons render from on load, and NULL WHEN THE
    # BROKER HAS NOT CHOSEN YET (0107) — which is why neither button can be
    # lit on a fresh lead.
    #
    # THIS DEFAULTED TO "year" AND IT BROKE THE SCREEN TWICE OVER. 0107 made
    # the column nullable, so a row nobody had answered came back as None and
    # failed validation against a non-nullable field: every save 500'd and the
    # broker was told "Could not save that premium". Any row that did validate
    # had "year" substituted for the missing answer, so all six toggles lit up
    # Year again — the exact thing 0107 removed from the database.
    #
    # A response model that supplies a value the database does not hold is
    # inventing data, and here it was inventing an answer to the one question
    # this feature exists to ask.
    current_premium_unit: Literal["year", "month"] | None = None
    new_premium_unit: Literal["year", "month"] | None = None

    created_at: datetime
    updated_at: datetime
