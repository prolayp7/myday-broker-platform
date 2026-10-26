"""Reading a DASH report to prefill a driver.

OPTIONAL, not a replacement. The broker can keep typing what they see on
DASH's own website — this only removes the typing for anyone who would rather
upload the report. Migration 0048 deleted an earlier version of this on the
instruction "no auto-import of any kind"; it returns as a choice rather than a
default.

Two ways in because brokers get the report both ways: some download the PDF,
some have the link open already.
"""

import logging
from uuid import UUID

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from starlette.concurrency import run_in_threadpool

from auth_dependencies import get_current_broker
from schemas.dash import DashUrlRequest
from services import dash as service

logger = logging.getLogger("myday.dash")

# Same gate as routes/driver.py — any authenticated broker, and the service
# checks the driver is on a lead they actually hold.
router = APIRouter(tags=["dash"], dependencies=[Depends(get_current_broker)])

# A DASH report is a handful of pages. Anything much larger is not one, and
# reading it would spend an Azure page allowance to find that out.
_MAX_BYTES = 20 * 1024 * 1024


@router.post("/drivers/{driver_id}/dash-report")
async def upload_dash_report(
    driver_id: UUID,
    file: UploadFile = File(...),
    broker: dict = Depends(get_current_broker),
):
    """Read an uploaded DASH PDF and fill this driver's blank fields.

    Returns what was filled and what was left alone, so the broker can see the
    result without comparing the form against the PDF themselves.

    THE PDF IS NOT STORED. It is read, used, and dropped — the broker keeps
    the report on DASH's own site, and holding a second copy of someone's
    claims history would be retaining customer data for no purpose this
    feature needs.
    """
    contents = await file.read()
    if not contents:
        raise HTTPException(status_code=400, detail="That file is empty")
    if len(contents) > _MAX_BYTES:
        raise HTTPException(status_code=400, detail="That file is too large to be a DASH report")

    # OFF THE EVENT LOOP. Everything below apply_pdf is synchronous — a
    # Supabase read, then Azure's SDK, which blocks at poller.result() until
    # the analysis finishes. This handler is `async def`, so calling it
    # directly ran that on the event loop and froze every other in-flight
    # request across the whole API for the duration of one report.
    #
    # Same fix, and the same measurement, as routes/webhooks.py: a direct call
    # stalled concurrent requests for 2.27s at a 2s timeout; run_in_threadpool
    # brought it to 0.07s. Azure has no timeout of ours at all, so the stall
    # was bounded only by how long the analysis took.
    #
    # Still synchronous from the BROKER's point of view: the fields are filled
    # before the response returns. The threadpool changes who is blocked, not
    # the ordering.
    return await run_in_threadpool(
        service.apply_pdf, str(driver_id), broker["id"], contents
    )


@router.post("/drivers/{driver_id}/dash-report/url")
async def parse_dash_report_url(
    driver_id: UUID,
    request: DashUrlRequest,
    broker: dict = Depends(get_current_broker),
):
    """Fetch a DASH report from a link and fill this driver's blank fields.

    Fetched server-side rather than asked of the browser, because the response
    goes straight to Azure — routing the PDF through the client would move a
    customer's claims history through one more place for no benefit.
    """
    try:
        async with httpx.AsyncClient(timeout=30.0, follow_redirects=True) as client:
            response = await client.get(str(request.url))
            response.raise_for_status()
    except Exception as err:
        # The URL is the broker's, so this is usually a bad link or an expired
        # session — their problem to fix, not a server fault.
        logger.warning("Could not fetch a DASH report from a supplied URL: %s", err)
        raise HTTPException(
            status_code=400,
            detail="That link could not be opened — check it is still valid and public",
        )

    if len(response.content) > _MAX_BYTES:
        raise HTTPException(status_code=400, detail="That file is too large to be a DASH report")

    # Off the event loop, for the reason spelled out on the upload route.
    return await run_in_threadpool(
        service.apply_pdf, str(driver_id), broker["id"], response.content
    )


@router.get("/drivers/{driver_id}/dash-report/file")
async def download_dash_report(
    driver_id: UUID,
    broker: dict = Depends(get_current_broker),
):
    """A short-lived link to the stored copy of this driver's DASH report.

    A LINK, NOT THE BYTES. The bucket is private, so the browser cannot reach
    the object directly; a signed URL is minted per request and expires in
    fifteen minutes. Storing the URL instead of the path would be storing
    something that stops working — the reason driver.dash_report_path holds a
    path (0102).

    Same ownership guard as the upload routes, in the service: a broker may
    only reach a report for a customer they currently hold.

    404 when nothing has been uploaded, which is the ordinary case for a lead
    whose broker has not pulled a report yet.
    """
    return {"url": await run_in_threadpool(service.report_url, str(driver_id), broker["id"])}
