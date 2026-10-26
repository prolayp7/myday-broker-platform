"""Pydantic shapes for the vehicle domain.

Every field is optional, including on create. A quote is built over a phone
call and has to be saveable half-finished — the broker adds a car and fills it
in as the customer answers, so requiring anything here would mean they cannot
store what they have so far.

The Literal types mirror the check constraints in migrations/0050_vehicle.sql
and are kept in sync BY HAND, same convention as schemas/driver.py: a value
accepted here but rejected by the database surfaces as a 400 from db_error
rather than a silent write.
"""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from schemas.dates import WideDate

Condition = Literal["New", "Used"]
Ownership = Literal["Paid Off", "Financed", "Leased"]
ParkingAtNight = Literal[
    "Secure Condo/Apt Garage",
    "Private Garage",
    "Driveway",
    "Street",
    "Parking Lot",
    "Other",
]


class VehiclePayload(BaseModel):
    """One vehicle on a quote — what the broker submits or edits.

    Used for BOTH create and update. On create it is typically empty: the row
    is added first so the fields have somewhere to save to, then filled in one
    answer at a time.
    """

    # === Identity — read off the DASH report by the broker ===
    vin: str | None = Field(None, max_length=50)
    year_make_model: str | None = Field(None, max_length=200)

    # === Acquisition — asked of the customer ===
    condition: Condition | None = None
    # WideDate: a broker quotes a car the customer collects next month, so a
    # future purchase date is ordinary rather than a mistake.
    purchase_date: WideDate | None = None
    km_at_purchase: int | None = Field(
        None, ge=0, description="Odometer reading at purchase, in km."
    )
    ownership: Ownership | None = None
    finance_company: str | None = Field(
        None,
        max_length=200,
        description="The lienholder, when ownership is Financed or Leased.",
    )

    # === Usage — asked of the customer ===
    annual_km: int | None = Field(None, ge=0)
    one_day_distance_km: int | None = Field(
        None, ge=0, description="One-way commute distance in km."
    )
    parking_at_night: ParkingAtNight | None = None

    # === Equipment — both discount-bearing in Ontario ===
    winter_tires: bool | None = None
    anti_theft: bool | None = None

    # === Cover being quoted ===
    # Free text, not a Literal: what counts as "Full Coverage" varies by
    # insurer, so the database does not constrain it either.
    coverage_type: str | None = Field(None, max_length=100)


class VehicleResponse(VehiclePayload):
    """A stored vehicle, read back with its ids."""

    id: UUID
    driver_id: UUID
