# Proposed fix: Meta CAPI access token leaking into logs

**Status:** proposed, not applied — code is unchanged.
**Severity:** High
**Found by:** VAPT scan, 2026-08-06

## File

`backend/meta_capi.py`, function `send_event()`, around line 114.

## The problem

```python
response = httpx.post(
    f"{CAPI_API_BASE}/{dataset_id}/events",
    params={"access_token": access_token},
    json=body,
    timeout=10.0,
)
```

The access token is passed as a URL query parameter. httpx 0.28.1 logs the
full request URL — including the query string — at `INFO` level via
`logging.getLogger("httpx")`. `backend/main.py:15` sets root logging to
`logging.basicConfig(level=logging.INFO)` and nothing raises the `httpx`
logger back to `WARNING`. `send_event()` runs continuously in production
(called on every pending Meta signal from the drain started in `main.py`'s
lifespan), so the live `META_CAPI_ACCESS_TOKEN` is written to stdout in
cleartext on every send.

Confirmed by installing the pinned httpx version in an isolated venv,
reading the `logger.info(...)` call in `httpx/_client.py`, and reproducing
the rendered log line with a fake token.

## Why this file specifically

`backend/meta_graph.py`'s `_get_object()` already avoids this exact issue —
it sends its token via an `Authorization: Bearer` header instead of a query
param, with a comment explaining why:

```python
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
```

`meta_capi.py`'s `send_event()` is the one call site that missed this
pattern.

## Proposed change

Replace the `params={"access_token": access_token}` line in
`send_event()` with a header, matching `meta_graph.py`:

```python
response = httpx.post(
    f"{CAPI_API_BASE}/{dataset_id}/events",
    headers={"Authorization": f"Bearer {access_token}"},
    json=body,
    timeout=10.0,
)
```

Meta's Conversions API accepts the token via either the query string or an
`Authorization` header, so this is a drop-in change with no behavior
difference on Meta's side.

## Additional recommended hardening

Add defense in depth in `backend/main.py`, near the existing
`logging.basicConfig(level=logging.INFO)` call, so any future accidental
`params=` token doesn't get logged either:

```python
logging.getLogger("httpx").setLevel(logging.WARNING)
```

## Operational follow-up

Rotate `META_CAPI_ACCESS_TOKEN` after this fix ships — the current token has
been written to logs on every event send since deploy, so it should be
treated as already exposed.
