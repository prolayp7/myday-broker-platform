"""Pydantic shapes for the property domain — the homes on a combined quote.

Every field is optional, including on create. The Primary row is created the
moment the broker picks a policy type, which is the START of the conversation:
nothing about the building is known yet, and the row exists so the answers have
somewhere to go as they arrive.

The Literal types mirror the check constraints in migrations/0052_property.sql
and are kept in sync BY HAND, same convention as schemas/vehicle.py: a value
accepted here but rejected by the database surfaces as a 400 from db_error
rather than a silent write.
"""

from datetime import date, datetime
from decimal import Decimal
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from schemas.dates import PastDate, WideDate

# WHICH property this is on the quote. 'Primary' is where the applicant lives
# and is created by the policy-type flow, never added by hand; the other two are
# investment properties a Homeowner or Condo applicant rents out.
PropertyRole = Literal["Primary", "Rental Dwelling", "Rented Condo"]

# WHICH intake form applies to this row, and therefore which fields below are
# relevant. Per row, not per lead: a Rental Dwelling under a Condo primary is
# rated as a house, not as a condo.
PropertyType = Literal["Tenant", "Homeowner", "Condo"]

Structure = Literal[
    "High Rise Apartment",
    "Low Rise Apartment",
    "Basement Apartment",
    "Row House/Town House (End)",
    "Row House/Town House (Inside)",
    "Detached",
    "Duplex",
    "Triplex",
    "Multi-plex (4 units or more)",
    "Other",
]


class AddPropertyRequest(BaseModel):
    """Adding an investment property to a quote.

    role and type are required and everything else is not: the broker says WHAT
    they are adding, and the details follow over the call. 'Primary' is
    rejected by the service — that row comes from the policy-type flow, not
    from here.
    """

    property_role: PropertyRole
    property_type: PropertyType


class PropertyPayload(BaseModel):
    """A property's details — what the broker edits, one answer at a time.

    Patch semantics: the service uses exclude_unset, so omitting a field leaves
    it alone while sending an explicit null clears it. That distinction matters
    on a form filled over several calls.

    property_role is absent deliberately: what a property IS does not change
    because a broker edits a field. A Primary becoming a rental would need the
    policy-type flow, which owns that decision.
    """

    # A row's type CAN change — a broker told "it's actually a condo" corrects
    # it in place rather than deleting and re-adding, which would lose every
    # answer already collected.
    property_type: PropertyType | None = None

    # === Where it is ===
    # The Primary's is seeded from the applicant's address when the row is
    # created; a rental's is typed fresh, being a different building.
    street_address: str | None = Field(None, max_length=200)
    city: str | None = Field(None, max_length=100)
    province: str | None = Field(None, max_length=50)
    postal_code: str | None = Field(None, max_length=10)

    # === Common to all three forms ===
    # Already happened: a policy cannot have started after today.
    insured_since: PastDate | None = None
    # WideDate: a broker records cover that begins on the next renewal, so a
    # date ahead of today is ordinary here.
    insured_with_property_since: WideDate | None = None
    structure: Structure | None = None
    year_built: int | None = Field(None, ge=1800, le=2100)
    notes: str | None = None

    # === Homeowner and Condo only ===
    stories: Decimal | None = Field(None, ge=0)
    full_baths: int | None = Field(None, ge=0)
    half_baths: int | None = Field(None, ge=0)
    number_of_mortgages: int | None = Field(None, ge=0)
    # Free text, not an enum: the real dropdown values are not confirmed yet, and
    # a CHECK on guessed ones would reject what a broker is actually told. Same
    # for burglar_alarm and sump_type.
    finished_percent: str | None = Field(None, max_length=50)

    # Year each system was last done. Bounded so a mistyped "20" or "20155" is
    # caught here rather than stored as a plausible-looking wrong answer.
    electrical_year: int | None = Field(None, ge=1800, le=2100)
    plumbing_year: int | None = Field(None, ge=1800, le=2100)
    heating_year: int | None = Field(None, ge=1800, le=2100)
    roof_year: int | None = Field(None, ge=1800, le=2100)
    water_heater_year: int | None = Field(None, ge=1800, le=2100)

    burglar_alarm: str | None = Field(None, max_length=100)
    smoke_detectors: int | None = Field(None, ge=0)
    fire_extinguishers: int | None = Field(None, ge=0)
    sump_type: str | None = Field(None, max_length=100)

    # === Condo only ===
    contents_value: Decimal | None = Field(None, ge=0)


class PropertyResponse(BaseModel):
    """A property as stored. Mirrors the repository's select list."""

    id: UUID
    driver_id: UUID
    property_role: str
    property_type: str

    street_address: str | None = None
    city: str | None = None
    province: str | None = None
    postal_code: str | None = None

    insured_since: date | None = None
    insured_with_property_since: date | None = None
    structure: str | None = None
    year_built: int | None = None
    notes: str | None = None

    stories: Decimal | None = None
    full_baths: int | None = None
    half_baths: int | None = None
    number_of_mortgages: int | None = None
    finished_percent: str | None = None

    electrical_year: int | None = None
    plumbing_year: int | None = None
    heating_year: int | None = None
    roof_year: int | None = None
    water_heater_year: int | None = None

    burglar_alarm: str | None = None
    smoke_detectors: int | None = None
    fire_extinguishers: int | None = None
    sump_type: str | None = None

    contents_value: Decimal | None = None

    created_at: datetime
    updated_at: datetime
