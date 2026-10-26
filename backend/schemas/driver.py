"""Pydantic shapes for the driver domain.

A driver is the VERIFIED intake profile — what the broker confirmed with the
customer over a call — as opposed to the lead, which holds whatever the
customer typed into an ad form. Every field is optional because the row is
created blank the first time a broker opens a lead and fills up over a call.
"""

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from schemas.dates import PastDate

# Matches the check constraints in migrations/0035_driver.sql. Kept in sync by
# hand: a value here that the DB rejects surfaces as a 400 from db_error, not a
# silent write.
RelationshipToApplicant = Literal[
    "Applicant", "Spouse", "Common Law", "Same Sex Partner",
    "Co-habitant", "Child", "Sibling", "Parent",
    # What a DASH import writes (migrations/0081): a person the report says is
    # on the policy, whose relationship to OUR applicant nobody has stated yet.
    # Deliberately absent from AddDriverRequest's own dialog — a broker adding
    # someone by hand knows who they are.
    "NA",
]
Gender = Literal["Male", "Female", "Other"]
MaritalStatus = Literal["Single", "Married", "Common Law", "Divorced", "Widowed"]


class AddDriverRequest(BaseModel):
    """Adding an additional driver to a lead — a spouse, a child.

    Relationship is the ONLY field here; everything else is filled in on New
    Policy once the row exists.

    'Applicant' is accepted by the type but rejected by the service: a lead has
    exactly one applicant, created with the lead itself.

    'NA' is likewise accepted here and not offered by the dialog. It exists for
    a DASH import, which knows a person is on the policy but cannot know their
    relationship to our applicant — a broker adding someone by hand does know,
    so choosing "not stated" would only be a way to skip the question. Not
    rejected outright, though: it is a legitimate value for the row to hold,
    and refusing it here while the update endpoint accepts it would be a
    distinction with no purpose.
    """

    relationship_to_applicant: RelationshipToApplicant


class UpdateDriverRequest(BaseModel):
    """A partial update. Only the fields actually sent are written — the service
    uses exclude_unset, so omitting a field leaves it alone while explicitly
    sending null clears it. That distinction matters on a form filled over
    several calls."""

    relationship_to_applicant: RelationshipToApplicant | None = None

    # Seeded from the lead when the applicant row is created, then owned by the
    # broker. No format validation on phone/email: the lead's own columns have
    # none either, and a broker mid-call should not be blocked by a regex over
    # a number the customer just read out.
    name: str | None = Field(None, max_length=200)
    # The split of `name`, and what Meta actually hashes (fn/ln). Editable
    # because Claude cannot always be right about which word is the surname
    # — the broker seeing three boxes IS the review step. middle_name is
    # never sent to Meta; it exists so a middle name stays out of
    # first_name, where it would hash to something matching nobody.
    first_name: str | None = Field(None, max_length=100)
    middle_name: str | None = Field(None, max_length=100)
    last_name: str | None = Field(None, max_length=100)
    phone: str | None = Field(None, max_length=50)
    email: str | None = Field(None, max_length=200)
    date_of_birth: PastDate | None = None
    gender: Gender | None = None
    marital_status: MaritalStatus | None = None

    license_number: str | None = Field(None, max_length=50)
    license_province: str | None = Field(None, max_length=50)

    # Graduated licensing — G1 (learner) -> G2 -> G. Collected from the
    # customer over the call.
    g1_date: PastDate | None = None
    g2_date: PastDate | None = None
    g_date: PastDate | None = None

    # FROM THE DASH POLICIES SUMMARY (migration 0098). Dates as printed; gaps
    # are not interpreted.
    #
    # renewal_date is NOT PastDate: it is when the customer's cover runs out,
    # so it is normally in the FUTURE. The graduated-licence dates above are
    # past by definition; this one would be rejected by the same rule.
    renewal_date: date | None = None
    insured_since: PastDate | None = None
    current_insurer_since: PastDate | None = None

    street_address: str | None = Field(None, max_length=200)
    unit_number: str | None = Field(None, max_length=20)
    city: str | None = Field(None, max_length=100)
    province: str | None = Field(None, max_length=50)
    postal_code: str | None = Field(None, max_length=10)

    # THE BROKER'S ANSWER, not DASH's (0114). Convictions are on the MVR
    # abstract rather than the DASH report, and the broker has the customer on
    # the phone when the qualification dialog asks — so this is typed, unlike
    # at_fault_claims_6y beside it in the table, which only the report writes.
    #
    # ge=0 so a negative is refused here, naming the field, rather than arriving
    # as a 400 from the column's own CHECK.
    convictions_3y: int | None = Field(None, ge=0)

    # Has the customer been licensed more than five years (0117)? The broker's
    # answer in the qualify dialog rather than a parse — DASH does
    # print "Years Licensed", but asking covers every lead rather than only the
    # ones with a report.
    #
    # THREE STATES, and null is the important one: it means nobody has been
    # asked. A bool that defaulted to False would claim
    # "not licensed long enough" about every driver by silence.
    licensed_5y_plus: bool | None = None


class DriverResponse(BaseModel):
    """A driver as stored. Mirrors the repository's select list."""

    id: UUID
    lead_id: UUID
    is_principal: bool
    relationship_to_applicant: str | None = None

    name: str | None = None
    first_name: str | None = None
    middle_name: str | None = None
    last_name: str | None = None
    phone: str | None = None
    email: str | None = None
    date_of_birth: PastDate | None = None
    gender: str | None = None
    marital_status: str | None = None

    license_number: str | None = None
    license_province: str | None = None
    g1_date: PastDate | None = None
    g2_date: PastDate | None = None
    g_date: PastDate | None = None

    renewal_date: date | None = None
    insured_since: PastDate | None = None
    current_insurer_since: PastDate | None = None

    street_address: str | None = None
    unit_number: str | None = None
    city: str | None = None
    province: str | None = None
    postal_code: str | None = None

    # Read back so the qualification dialog can prefill what the broker already
    # answered. at_fault_claims_6y rides along too: written only by the DASH
    # path, but the screen needs to show it.
    convictions_3y: int | None = None
    at_fault_claims_6y: int | None = None
    licensed_5y_plus: bool | None = None

    # WHERE THIS DRIVER'S DASH PDF IS KEPT (0102), or null if none was ever
    # uploaded. Read-only from the client's point of view: the screen uses it
    # to decide whether the Download DASH button is live, and the upload path
    # is the only thing that writes it.
    #
    # Deliberately NOT on UpdateDriverRequest — a broker setting a storage
    # path by hand is not something that should be possible.
    dash_report_path: str | None = None
