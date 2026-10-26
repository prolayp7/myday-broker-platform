"""Thin client for the calls MyDay makes TO Meta's Graph API (as opposed to
routes/webhooks.py, which handles calls FROM Meta). Two things: validating an
admin-entered platform_form_id before it's mapped, and — the real lead
ingestion step — fetching a lead's actual submitted answers.

WHY fetch_lead_data EXISTS: Meta's leadgen webhook notification carries only
leadgen_id, form_id, adgroup_id, ad_id, created_time — never the customer's
actual name/phone/email. Meta's own docs are explicit that you must use the
leadgen_id to call back into the Graph API for the submitted field_data; see
https://developers.facebook.com/documentation/ads-commerce/marketing-api/guides/lead-ads/retrieving.
This isn't a workaround, it's the documented two-step pattern every real
integration (Meta's own guide, and third-party CRMs) uses."""

import os
import re

import httpx

# THE ONE definition of Meta's API version, for both directions — meta_capi.py
# imports this rather than declaring its own. It carried a hand-written copy
# and a comment asking whoever edited it to "keep the two in step", which is
# exactly the arrangement that drifts.
#
# Configurable because Meta deprecates versions on THEIR schedule: when v21.0
# goes, moving off it should be an environment change, not a code edit and a
# redeploy. Same reasoning as META_CAPI_ACTION_SOURCE (meta_capi.py) — a
# documented default, so an environment that sets nothing is correct.
GRAPH_API_VERSION = os.environ.get("META_GRAPH_API_VERSION", "v21.0")

# Checked HERE, at import, so a typo stops the app from starting. Left
# unvalidated, "21.0" or "v21" builds a URL that Meta answers with a 404 —
# which surfaces hours later as a lead that never arrived, in someone else's
# error message. A bad deploy should be caught by the deploy.
if not re.fullmatch(r"v\d+\.\d+", GRAPH_API_VERSION):
    raise ValueError(
        f"META_GRAPH_API_VERSION must look like v21.0, got {GRAPH_API_VERSION!r}"
    )
# The FULL URL this module calls, straight from the environment.
#
# Three sources, in order:
#   1. META_GRAPH_API_BASE      — ordinary configuration, the way to set this
#   2. GRAPH_API_BASE_OVERRIDE  — the previous name, still honoured
#   3. built from GRAPH_API_VERSION above
#
# The OLD NAME IS STILL READ, deliberately. It is set in developers' own .env
# files — which are gitignored, so no rename can reach them — and in
# meta-lead-simulator/README.md. Dropping it would stop the simulator working
# with no error to say why.
#
# THE DEFAULT STAYS, also deliberately. Railway does not set either name, so
# `os.environ["..."]` with no fallback would raise at import, uvicorn would
# exit, and the container would restart-loop — the whole API down, not just
# Meta. With the default, this deploys as a no-op and the variables can be
# set whenever, in any order.
#
# Setting a full base makes META_GRAPH_API_VERSION irrelevant HERE: the URL is
# used verbatim, version and all. The version still builds the default and
# still keeps this module and meta_capi in step when neither base is set.
GRAPH_API_BASE = os.environ.get(
    "META_GRAPH_API_BASE",
    os.environ.get(
        "GRAPH_API_BASE_OVERRIDE",
        f"https://graph.facebook.com/{GRAPH_API_VERSION}",
    ),
)


class MetaObjectError(Exception):
    """Base: Meta's API itself confirmed the requested object is
    invalid/inaccessible (a 400 with Meta's own message). Distinct from a
    network/config failure, which raises a plain Exception — callers need to
    tell these apart to give an honest error message."""

    def __init__(self, reason: str):
        self.reason = reason
        super().__init__(reason)


class MetaFormValidationError(MetaObjectError):
    """The admin-entered form_id isn't a real, accessible, ACTIVE lead form."""


class MetaLeadFetchError(MetaObjectError):
    """The leadgen_id doesn't exist or isn't accessible (e.g. already expired,
    or the access token can't see it)."""


def _get_object(object_id: str, fields: str, error_cls: type[Exception], fallback_msg: str) -> dict:
    """Shared GET /{id}?fields=... against the Graph API. A 400 is Meta's
    deliberate "doesn't exist / no permission / wrong object type" answer and
    raises error_cls with Meta's own message; anything else non-2xx (network,
    bad token, 5xx) raises a plain httpx error for the caller to map."""
    # Token goes in the Authorization header, NOT a query param — httpx logs
    # request URLs at INFO level, so a query-param token would leak into every
    # server log line. Meta supports both; the header keeps logs clean.
    access_token = os.environ["META_PAGE_ACCESS_TOKEN"]

    response = httpx.get(
        f"{GRAPH_API_BASE}/{object_id}",
        params={"fields": fields},
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10.0,
    )

    if response.status_code == 400:
        message = response.json().get("error", {}).get("message", fallback_msg)
        raise error_cls(message)

    response.raise_for_status()
    return response.json()


def validate_form(platform_form_id: str) -> dict:
    """Confirm platform_form_id is a real, ACTIVE Meta lead form. Returns the
    form record (id, name, status, leads_count) on success; raises
    MetaFormValidationError otherwise (plain Exception for network/config
    failures — the caller maps that to a different HTTP status).

    id/name/status alone aren't enough to prove "this is a lead form": those
    fields also exist on ads, ad sets, campaigns, and other Meta object types,
    so a wrong-but-real id (e.g. an ad ID pasted where a form ID belongs) would
    pass. leads_count only exists on the leadgen_form object type, so asking
    for it makes Meta itself reject any non-form object with a 400."""
    form = _get_object(
        platform_form_id,
        fields="id,name,status,leads_count",
        error_cls=MetaFormValidationError,
        fallback_msg="Form not found or not accessible",
    )

    # A form that isn't ACTIVE (ARCHIVED/PAUSED/DELETED/DRAFT) exists and reads
    # fine, but Meta won't deliver leads from it — mapping one would create a
    # group that silently never receives leads. Reject it here so no caller can
    # map a dead form. (Meta doesn't allow deleting forms, only archiving, so
    # ARCHIVED is the common real case.)
    status = form.get("status")
    if status != "ACTIVE":
        raise MetaFormValidationError(
            f"this form is {status or 'not active'} on Meta — only ACTIVE forms can receive leads"
        )

    return form


def fetch_lead_data(leadgen_id: str) -> dict:
    """GET the actual customer answers (name, phone, email, ...) for one lead
    submission. Returns Meta's raw response: {"field_data": [{"name":
    "full_name", "values": ["Jane Doe"]}, ...], "id": "...", ...}. Parsing
    field_data into our fixed name/phone/email columns is a separate step (see
    meta_lead_parser.py) — this function only fetches the raw shape.

    Note: field_data does NOT include responses to optional custom disclaimer
    checkboxes — that's a separate custom_disclaimer_responses field, not
    fetched here since none of our forms use disclaimers yet.

    Raises MetaLeadFetchError if Meta says the leadgen_id doesn't exist or
    isn't accessible; a plain Exception for network/config failures — same
    split as validate_form, for the same reason."""
    return _get_object(
        leadgen_id,
        fields="field_data",
        error_cls=MetaLeadFetchError,
        fallback_msg="Lead not found or not accessible",
    )
