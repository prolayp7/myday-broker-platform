from fastapi import APIRouter, Depends

from auth_dependencies import require_admin
from schemas.commission import CommissionRatesResponse, UpdateCommissionRatesRequest
from services import commission as service

router = APIRouter(tags=["commission"], dependencies=[Depends(require_admin)])


@router.get("/commission-rates", response_model=CommissionRatesResponse)
def get_commission_rates():
    """The two rates every commission projection is multiplied by.

    Admin-only, like setting them. A broker sees the RESULT on their own
    report; the rate card itself is the brokerage's business.
    """
    return service.get_rates()


@router.put("/commission-rates", response_model=CommissionRatesResponse)
def update_commission_rates(request: UpdateCommissionRatesRequest):
    """Change the rate card — both rates together.

    TAKES EFFECT EVERYWHERE, INCLUDING MONTHS ALREADY REPORTED. Nothing is
    stored per policy, so every projection is recomputed from these two
    numbers whenever it is shown. The screen says so above the Save button,
    because a figure that moves without explanation is read as a bug.
    """
    return service.update_rates(request)
