"""Reading back from Meta what Meta says it received.

THE OTHER DIRECTION. meta_capi.py POSTs events; this GETs Meta's own count of
them. Everything else in this system reports from our database, which can only
say what we sent — an event we recorded as Sent may still have been counted
differently, or not at all, on Meta's side.

ITS OWN CREDENTIAL, hence its own module. meta_capi.py is
META_CAPI_ACCESS_TOKEN throughout and meta_graph.py is META_PAGE_ACCESS_TOKEN;
a third token living inside either would make it far too easy to reach for the
wrong one. META_DATASET_QUALITY_TOKEN belongs to the system user actually
assigned to the dataset, which is what lets it read at all.

WHAT THIS CANNOT TELL YOU. Meta's Event Match Quality — the 0-10 score in
Events Manager — is not available here. Their documentation says EMQ covers
web events and names "conversion leads" as an exception, and a controlled test
agreed: /dataset_quality returns 400 (#1) for this dataset while the same
token, same app, returns 200 for two datasets that carry no conversion-leads
traffic. So match_keys below means "Meta RECEIVED this identifier", never
"Meta successfully matched it to a person". Getting a real score is a
conversation with a Meta representative, not a code change.
"""

import logging
import os
from datetime import datetime, timedelta, timezone

import httpx

from meta_graph import GRAPH_API_VERSION

logger = logging.getLogger("myday.meta_reporting")

# ITS OWN BASE, deliberately not meta_graph's GRAPH_API_BASE.
#
# That one is pointed at a local mock server in development
# (GRAPH_API_BASE_OVERRIDE), because lead ingestion has to be testable without
# calling Meta. Reporting is the opposite: a mock has nothing to report, and
# silently reading fabricated numbers into an admin panel is worse than
# reading none. Same reasoning meta_capi.py keeps CAPI_API_BASE separate from
# the inbound base — the comment there records what happened when they were
# shared.
#
# Overridable for tests, defaulting to the real thing.
REPORTING_API_BASE = os.environ.get(
    "META_REPORTING_BASE", f"https://graph.facebook.com/{GRAPH_API_VERSION}"
)

# Same 10 seconds as every other outbound call in this codebase
# (meta_graph.py, meta_capi.py). An admin screen waiting on Meta is a screen
# nobody is reading.
TIMEOUT_SECONDS = 10.0


def _get(path: str, params: dict, token: str) -> dict | None:
    """One GET against the Graph API, or None if it did not work.

    NEVER RAISES. This feeds a reporting panel whose other half comes from our
    own database and is still true when Meta is unreachable — taking the page
    down over a failed external read would hide correct information to report
    an unavailable one.

    LOGS THE STATUS AND ERROR CODE ONLY. Meta quotes the access token back
    verbatim inside some error messages (see meta_capi.py::_redact_token), so
    the full body must never reach a log line.
    """
    try:
        response = httpx.get(
            # Header, not a query param: httpx logs request URLs at INFO, so a
            # token in the query string lands in every server log line.
            f"{REPORTING_API_BASE}/{path}",
            params=params,
            headers={"Authorization": f"Bearer {token}"},
            timeout=TIMEOUT_SECONDS,
        )
    except Exception as err:
        # Type only, not str(err) — an httpx error can embed the request URL.
        logger.warning("Could not reach Meta for %s: %s", path, type(err).__name__)
        return None

    if response.status_code != 200:
        code = None
        try:
            code = (response.json() or {}).get("error", {}).get("code")
        except Exception:
            pass
        logger.warning(
            "Meta returned %s (code %s) for %s", response.status_code, code, path
        )
        return None

    try:
        return response.json()
    except Exception:
        logger.warning("Meta returned a non-JSON body for %s", path)
        return None


def _sum_buckets(payload: dict | None, keyed_by_event: bool) -> dict:
    """Flatten /stats into totals.

    THE SHAPE IS NESTED AND VARIES BY AGGREGATION, and is not in Meta's own
    SDK model — so it is parsed from a captured real response rather than from
    documentation:

        data[] = one HOURLY bucket, each carrying its own data[]
          event_total_counts -> {"value": "Lead", "count": 38}
          match_keys         -> {"event": "Lead", "value": "email", "count": 49}

    Buckets must be summed across the window; reading only the first would
    report one hour as if it were a fortnight.
    """
    out: dict = {}
    for bucket in (payload or {}).get("data") or []:
        for entry in bucket.get("data") or []:
            try:
                count = int(entry.get("count") or 0)
            except (TypeError, ValueError):
                continue
            if keyed_by_event:
                event = entry.get("event")
                key = entry.get("value")
                if not event or not key:
                    continue
                out.setdefault(event, {})
                out[event][key] = out[event].get(key, 0) + count
            else:
                key = entry.get("value")
                if not key:
                    continue
                out[key] = out.get(key, 0) + count
    return out


def dataset_snapshot(days: int = 14, dataset_id: str | None = None) -> dict | None:
    """What META says it received, against what we say we sent.

    Returns None when the token is not configured or Meta cannot be reached,
    so the caller can show "unavailable" rather than an empty panel that reads
    like "nothing arrived".

    The caller normally passes services/meta_signal.py::dataset_id(), the one
    place that says where events are sent, so this reports on the same dataset
    the sender writes to. This module stays what it is: the thing that asks
    Meta a question about a dataset id it was handed.

    The env fallback remains for callers that have not been given one, so
    nothing that worked before this argument existed stops working.

    THE WINDOW MUST BE EXPLICIT. /stats defaults to roughly 24 hours, which is
    exactly what made a healthy integration look 12 days stale during the
    investigation that prompted this. start_time and end_time are unix
    SECONDS, not milliseconds and not ISO strings.
    """
    token = os.environ.get("META_DATASET_QUALITY_TOKEN")
    dataset_id = dataset_id or os.environ.get("META_CAPI_DATASET_ID")
    if not token or not dataset_id:
        return None

    now = datetime.now(timezone.utc)
    window = {
        "start_time": int((now - timedelta(days=days)).timestamp()),
        "end_time": int(now.timestamp()),
    }

    # Only last_fired_time is asked for. matched_entries, valid_entries,
    # match_rate_approx, event_stats and event_time_max were all tested
    # against the live dataset and come back 0, -1, "{}" or null — unpopulated
    # for this integration, so showing them would be showing noise.
    # server_last_fired_time is not a field on v21.0 at all.
    dataset = _get(dataset_id, {"fields": "id,name,last_fired_time"}, token)
    totals = _get(
        f"{dataset_id}/stats", {"aggregation": "event_total_counts", **window}, token
    )
    match_keys = _get(
        f"{dataset_id}/stats", {"aggregation": "match_keys", **window}, token
    )

    # All three failing means Meta is unreachable or the credential is wrong —
    # reported as unavailable. A partial failure still carries real
    # information, so it is kept.
    if dataset is None and totals is None and match_keys is None:
        return None

    return {
        "dataset_id": dataset_id,
        "last_fired_time": (dataset or {}).get("last_fired_time"),
        "totals": _sum_buckets(totals, keyed_by_event=False),
        "match_keys": _sum_buckets(match_keys, keyed_by_event=True),
    }
