"""Request and response shapes for a lead's phone numbers."""

from pydantic import BaseModel, Field


class RecordDialIntentRequest(BaseModel):
    """What the broker is about to dial.

    The raw number as typed or displayed — normalisation happens in the
    service, through the database's own normalize_phone_e164, so there is never
    a second implementation to disagree with the triggers.

    broker_id is NOT here and never will be: it comes from the session. A
    client able to name the broker could plant an intent that captures somebody
    else's next call.
    """

    phone: str = Field(min_length=1, max_length=50)


class DialIntentResponse(BaseModel):
    id: str
    lead_id: str
    phone_e164: str


class LeadPhoneResponse(BaseModel):
    """One number on a lead, from whichever table holds it.

    phone_e164 is null for a number that will not normalise — a placeholder, an
    extension, an international number. It is still shown, because the broker
    wants to see what the ad captured, but nothing can ever match a call
    against it.
    """

    phone: str
    phone_e164: str | None
    # 'lead' | 'driver' | 'secondary'
    source: str
    # Set only for secondary numbers — the only ones with a row of their own.
    phone_id: str | None
    # False for lead and driver numbers. Also false for a secondary that
    # duplicates one of those: deleting it would not stop the number matching,
    # so offering the control would promise something it cannot do.
    deletable: bool
