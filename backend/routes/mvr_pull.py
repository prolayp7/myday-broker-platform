"""The MVR register: brokers log the MVRs they pull, head office reads them.

TWO ROUTERS, mounted separately — the routes/lead_cost.py idiom. `router` is
behind a broker login (services/mvr_pull.py decides broker vs admin). The
`report_router` is PUBLIC: head office opens it with a password, not an
account, so it cannot sit behind get_current_broker. Its one write
(/unlock) is rate-limited the way login is.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from starlette.concurrency import run_in_threadpool

from auth_dependencies import get_current_broker, rate_limit, require_admin
from schemas.mvr_pull import (
    MvrMonthResponse,
    MvrPullResponse,
    MvrReportPasswordRequest,
    MvrReportUnlockRequest,
    MvrReportUnlockResponse,
    MvrUploadResult,
    UpdateMvrPullRequest,
)
from services import mvr_pull as service

router = APIRouter(tags=["mvr"], dependencies=[Depends(get_current_broker)])

report_router = APIRouter(tags=["mvr"])

_bearer = HTTPBearer(auto_error=False)

# An MVR printout is one or two pages. Anything far larger is not one, and
# reading it would spend Azure pages to find that out — the routes/dash.py cap.
_MAX_BYTES = 20 * 1024 * 1024
# A broker drops a family's worth at once, not a month's.
_MAX_FILES = 20

_MONTH = Query(pattern=r"^\d{4}-\d{2}$")


@router.post("/mvr-pulls", response_model=list[MvrUploadResult])
async def upload_mvrs(
    files: list[UploadFile] = File(...),
    broker: dict = Depends(get_current_broker),
):
    """Log one or more MVR PDFs. One result per file, in the order sent.

    The PDFs are read and dropped — see migrations/0132.
    """
    if len(files) > _MAX_FILES:
        raise HTTPException(status_code=400, detail=f"Drop at most {_MAX_FILES} MVRs at a time")

    results = []
    for upload in files:
        contents = await upload.read()
        name = upload.filename or "MVR"
        if not contents:
            results.append({"file_name": name, "status": "unreadable", "detail": "That file is empty", "pulls": []})
            continue
        if len(contents) > _MAX_BYTES:
            results.append({"file_name": name, "status": "unreadable", "detail": "That file is too large to be an MVR", "pulls": []})
            continue
        # OFF THE EVENT LOOP, as routes/dash.py explains: Azure's SDK blocks
        # until the analysis finishes, and on the loop that froze every other
        # request in the API for the duration.
        results.append(await run_in_threadpool(service.log_pdf, broker, name, contents))
    return results


@router.get("/mvr-pulls", response_model=MvrMonthResponse)
def list_mvrs(
    month: str = _MONTH,
    broker_id: UUID | None = None,
    broker: dict = Depends(get_current_broker),
):
    """One month. A broker gets their own lines whatever broker_id says."""
    return service.list_month(broker, month, str(broker_id) if broker_id else None)


@router.patch("/mvr-pulls/{pull_id}", response_model=MvrPullResponse)
def update_mvr(pull_id: UUID, request: UpdateMvrPullRequest, broker: dict = Depends(get_current_broker)):
    """Change the Business Reason or Information Retrieved dropdown."""
    return service.update(broker, str(pull_id), request.model_dump())


@router.delete("/mvr-pulls/{pull_id}", status_code=204)
def delete_mvr(pull_id: UUID, broker: dict = Depends(get_current_broker)):
    service.delete(broker, str(pull_id))


@router.put("/mvr-report/password", status_code=204)
def set_report_password(request: MvrReportPasswordRequest, admin: dict = Depends(require_admin)):
    """Change the head-office password. Anyone holding the old one is locked
    out on their next request."""
    service.set_report_password(admin, request.password)


@report_router.post(
    "/mvr-report/unlock",
    response_model=MvrReportUnlockResponse,
    dependencies=[Depends(rate_limit(10, 60))],
)
def unlock_report(request: MvrReportUnlockRequest):
    return service.unlock(request.password)


@report_router.get("/mvr-report", response_model=MvrMonthResponse)
def read_report(
    month: str = _MONTH,
    broker_id: UUID | None = None,
    credentials: HTTPAuthorizationCredentials | None = Depends(_bearer),
):
    """The head-office view: every broker, licence in full, read only."""
    return service.report(
        credentials.credentials if credentials else None,
        month,
        str(broker_id) if broker_id else None,
    )
