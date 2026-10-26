"""HTTP layer for address lookup — the type-ahead behind the Address panel.

Gated by get_current_broker, matching driver and vehicle. Not because an
address is sensitive, but because this endpoint spends money: every call bills
to the brokerage's Google account, so it is not left open to the internet.

Two calls, mirroring how Places works: one while typing, one on picking. They
are separate because the second is the expensive one and should happen once
per address, not once per keystroke.
"""

from fastapi import APIRouter, Depends, Query

from auth_dependencies import get_current_broker
from schemas.address import AddressDetail, AddressSuggestion
from services import address as service

router = APIRouter(tags=["address"], dependencies=[Depends(get_current_broker)])


@router.get("/address/suggest", response_model=list[AddressSuggestion])
def suggest_addresses(q: str = Query(..., description="What the broker has typed so far")):
    """Canadian addresses matching `q`, for the dropdown under the field.

    Empty list for anything under three characters — not an error. A broker
    who has typed "50" has not yet said anything worth asking Google about.
    """
    return service.suggest(q)


@router.get("/address/{place_id}", response_model=AddressDetail)
def address_detail(place_id: str):
    """The picked address, already split into driver's own columns.

    No unit_number: Google does not return one. A unit is something the
    customer says out loud and the broker types, so the frontend leaves that
    field for them rather than pretending an answer exists.
    """
    return service.detail(place_id)
