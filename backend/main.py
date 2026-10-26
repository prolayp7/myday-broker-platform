import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from routes import ad, ad_group, ad_platform, address, auth, broker, broker_document, broker_group, campaign, commission, community, contact_log, country, dash, driver, lead, lead_cost, lead_form, lead_phone, meta_signal, mvr_pull, premium_history, property, reminder, vehicle, webhooks, website_lead, whatsapp_webhooks
from services import lead_ingestion as lead_ingestion_service
from services import meta_signal as meta_signal_service
from services import reminder as reminder_service
from services import renewal_notification as renewal_service
from services import website_lead as website_lead_service
from services import whatsapp as whatsapp_service

# Send our own loggers (e.g. the db_error handler) to the console alongside
# uvicorn's own output, so unexpected 500s leave a server-side trail.
logging.basicConfig(level=logging.INFO)


class _RedactWebhookQueryString(logging.Filter):
    """Keep META_WEBHOOK_VERIFY_TOKEN out of uvicorn's access log.

    Meta verifies a webhook subscription by calling us with the shared secret
    as a QUERY PARAMETER — ?hub.verify_token=... (routes/webhooks.py) — and
    uvicorn's access log records the full request line, query string included:

        127.0.0.1 - "GET /webhooks/meta?hub.verify_token=SECRET HTTP/1.1" 200

    We cannot change how Meta sends it, so the log is the only place to fix.

    A FILTER, NOT --no-access-log. Disabling the access log entirely would
    remove the per-request trail for every other route, which is the first
    thing anyone reads when debugging a 500. This drops the query string on
    this one path and leaves every other line untouched.

    Uvicorn logs the path as positional arg 2 (see the access_logger.info call
    in uvicorn/protocols/http/h11_impl.py), so the argument is rewritten
    directly rather than regexed out of the formatted message — no pattern to
    drift when uvicorn changes its format string.
    """

    PATH = "/webhooks/meta"

    def filter(self, record: logging.LogRecord) -> bool:
        args = record.args
        if isinstance(args, tuple) and len(args) >= 3:
            path = args[2]
            if isinstance(path, str) and path.startswith(f"{self.PATH}?"):
                record.args = (*args[:2], f"{self.PATH}?<redacted>", *args[3:])
        return True


logging.getLogger("uvicorn.access").addFilter(_RedactWebhookQueryString())


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Starts the Meta CAPI drain alongside the app and stops it cleanly on
    shutdown. The drain is what actually sends queued conversion signals to
    Meta — the database triggers that create them cannot make HTTP calls, so
    without this running, signals would accumulate unsent until they passed
    Meta's 7-day limit and became permanently unsendable.

    And the webhook retry drain, which is the same idea for INBOUND leads. The
    webhook ingests the lead it just received and nothing else, so an event
    that failed — Meta briefly unreachable, a form not yet mapped — used to sit
    stored-but-unprocessed forever. Meta's own redelivery could not rescue it
    either, because the dedup on leadgen_id discards a retry before ingestion
    is reconsidered. An event was found stranded that way for nine days.

    And the WhatsApp notifications drain, which tells a broker on their phone
    that a lead has just been assigned to them. Same reason as the two above:
    leads are assigned by a Postgres function (assign_lead, 0019/0024) which
    cannot make an HTTP call, so the trigger in 0109 can only record that a
    message is due. It polls every five seconds rather than every few minutes
    because this one is the feature — a broker told ten minutes late has
    already lost the lead to whoever rang first. It stops immediately if the
    Twilio env vars are unset, so an environment without them pays nothing.

    All started tasks are cancelled explicitly on shutdown (rather than left
    to be killed) so an in-flight send, download or ingestion isn't abandoned
    mid-request during a normal restart."""
    drain = asyncio.create_task(meta_signal_service.run_drain_forever())
    lead_retries = asyncio.create_task(lead_ingestion_service.run_retry_drain_forever())
    # Renewal reminders (0104). Hourly, and idempotent by unique index, so a
    # restart or an overlapping run creates nothing twice.
    reminders = asyncio.create_task(reminder_service.run_sweep_forever())
    # WhatsApp lead notifications (0109). Five-second poll, because a broker
    # hearing about a lead late has already lost it to whoever called first.
    whatsapp = asyncio.create_task(whatsapp_service.run_send_forever())
    # Renewal nudges (0118). Sixty seconds, not five: a renewal due at 9am is
    # not lost to whoever calls first. Returns immediately when the renewal
    # template is unconfigured, which is the state until Meta approves it.
    renewals = asyncio.create_task(renewal_service.run_send_forever())
    # Website leads (0127). Sixty seconds, as a fallback only: every
    # submission and every admin change already runs the drain straight away.
    # This pass catches what nothing triggers — a broker unpausing, the daily
    # reset freeing limits, a run that failed.
    website_leads = asyncio.create_task(website_lead_service.run_drain_forever())
    background = [drain, lead_retries, reminders, whatsapp, renewals, website_leads]
    try:
        yield
    finally:
        for task in background:
            task.cancel()
        # Await the cancellations so shutdown doesn't race ahead of them.
        for task in background:
            try:
                await task
            except asyncio.CancelledError:
                pass


app = FastAPI(lifespan=lifespan)

# Allow the frontend to call this API from the browser. Browsers block
# cross-origin requests by default (different port/host = different origin)
# unless the server explicitly allows it. Comma-separate multiple origins
# (e.g. staging + prod) in FRONTEND_ORIGIN; defaults to the local dev servers.
# Vite hops to the next free port (5174, 5175, ...) when 5173 is taken, and
# localhost/127.0.0.1 are distinct origins to the browser — so allow the common
# dev range on both hosts, otherwise a port hop silently breaks every request
# with a CORS error. FRONTEND_ORIGIN overrides this entirely in prod.
_DEV_ORIGINS = [
    f"http://{host}:{port}"
    for host in ("localhost", "127.0.0.1")
    for port in range(5173, 5181)
]
frontend_origins = os.environ.get("FRONTEND_ORIGIN", ",".join(_DEV_ORIGINS)).split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=frontend_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(broker_group.router)
app.include_router(broker.router)
app.include_router(broker.broker_router)
app.include_router(broker_document.router)
app.include_router(commission.router)
app.include_router(community.router)
app.include_router(country.router)
app.include_router(ad_platform.router)
app.include_router(campaign.router)
app.include_router(ad_group.router)
app.include_router(ad.router)
app.include_router(lead_form.router)
app.include_router(lead.router)
app.include_router(lead.broker_router)
app.include_router(driver.router)
app.include_router(dash.router)
app.include_router(contact_log.router)
app.include_router(lead_phone.router)
app.include_router(lead_cost.router)
app.include_router(lead_cost.broker_router)
app.include_router(mvr_pull.router)
app.include_router(mvr_pull.report_router)
app.include_router(reminder.router)
app.include_router(vehicle.router)
app.include_router(property.router)
app.include_router(premium_history.router)
app.include_router(address.router)
app.include_router(webhooks.router)
app.include_router(website_lead.router)
app.include_router(meta_signal.router)
app.include_router(whatsapp_webhooks.router)


@app.get("/")
def read_root():
    return {"message": "Meta CRM backend is running"}
