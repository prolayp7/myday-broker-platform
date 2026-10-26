"""Address lookup against Google Places (New).

WHY THIS IS PROXIED rather than called from the browser: the API key stays on
the server. A key shipped to the frontend is public no matter what referrer
restrictions sit in front of it, and this one bills to a real account. Proxying
also means swapping providers later — Canada Post, Mapbox — touches this file
and nothing else.

NO DATABASE. Nothing here is stored: a suggestion is a thing the broker looked
at for two seconds, and the address they pick is written to driver by the
existing PATCH. This service has no repository for that reason.
"""

import logging
import os

import requests
from fastapi import HTTPException

logger = logging.getLogger("myday.address")

# Google Places, from the environment. ONE variable for both calls: they share
# a base and differ only in path, so two would be two things to keep in step
# for no benefit. Optional — the default is the real Google URL, so this
# deploys as a no-op.
#
# rstrip so a trailing slash in the env value does not produce "//places".
_PLACES_BASE = os.environ.get(
    "GOOGLE_PLACES_BASE", "https://places.googleapis.com/v1"
).rstrip("/")

_AUTOCOMPLETE_URL = f"{_PLACES_BASE}/places:autocomplete"
# DOUBLED BRACE, deliberately: this stays a TEMPLATE. The caller below does
# _DETAILS_URL.format(place_id=...), so a single brace would resolve the
# placeholder here and leave that call with nothing to substitute.
_DETAILS_URL = f"{_PLACES_BASE}/places/{{place_id}}"

# Canada only. An Ontario brokerage cannot write a policy on a Michigan
# address, so a US street in the dropdown is noise at best and a mis-typed
# quote at worst.
_REGION = ["ca"]

# Google charges for the fields it returns, so ask for exactly what is used.
_SUGGEST_FIELDS = "suggestions.placePrediction.placeId,suggestions.placePrediction.text"
_DETAIL_FIELDS = "addressComponents,formattedAddress"

# Slow enough to cover a cold connection, short enough that a broker mid-call
# is not left watching a spinner. A lookup that takes longer than this has
# already failed the broker whether or not it eventually answers.
_TIMEOUT = 8


def _api_key() -> str:
    key = os.environ.get("GOOGLE_PLACES_API_KEY")
    if not key:
        # A configuration fault, not a bad request — say so plainly rather
        # than letting Google return a cryptic 403 the broker cannot act on.
        raise HTTPException(503, "Address lookup is not configured")
    return key


def suggest(query: str) -> list[dict]:
    """Addresses matching what the broker has typed so far.

    Returns [] for a query too short to be meaningful rather than asking
    Google — two characters match half of Ontario, and every call is billed.
    """
    query = query.strip()
    if len(query) < 3:
        return []

    try:
        response = requests.post(
            _AUTOCOMPLETE_URL,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": _api_key(),
                "X-Goog-FieldMask": _SUGGEST_FIELDS,
            },
            json={"input": query, "includedRegionCodes": _REGION},
            timeout=_TIMEOUT,
        )
    except requests.RequestException as err:
        logger.warning("Places autocomplete unreachable: %s", err)
        raise HTTPException(502, "Could not reach the address service")

    if response.status_code != 200:
        logger.warning(
            "Places autocomplete failed: %s %s", response.status_code, response.text[:300]
        )
        raise HTTPException(502, "Address lookup failed")

    return [
        {
            "place_id": s["placePrediction"]["placeId"],
            "description": s["placePrediction"]["text"]["text"],
        }
        for s in response.json().get("suggestions", [])
        # Defensive: Google's response also carries queryPrediction entries
        # (search-term guesses, not places), which have no placeId.
        if "placePrediction" in s
    ]


def detail(place_id: str) -> dict:
    """The chosen address, split into driver's own columns.

    Google returns a flat list of typed components, not a structured address,
    so the mapping below is the whole point of this function.
    """
    try:
        response = requests.get(
            _DETAILS_URL.format(place_id=place_id),
            headers={
                "X-Goog-Api-Key": _api_key(),
                "X-Goog-FieldMask": _DETAIL_FIELDS,
            },
            timeout=_TIMEOUT,
        )
    except requests.RequestException as err:
        logger.warning("Places details unreachable: %s", err)
        raise HTTPException(502, "Could not reach the address service")

    if response.status_code == 404:
        # A stale place_id — the dropdown was open long enough for Google to
        # expire it. Recoverable by typing again, so say that.
        raise HTTPException(404, "That address is no longer available — search again")
    if response.status_code != 200:
        logger.warning("Places details failed: %s %s", response.status_code, response.text[:300])
        raise HTTPException(502, "Address lookup failed")

    body = response.json()
    parts: dict[str, str] = {}
    for component in body.get("addressComponents", []):
        for kind in component.get("types", []):
            # shortText for the codes a form wants ('ON', not 'Ontario'),
            # longText for names. Keyed by type, first one wins — Google can
            # repeat a type (locality AND administrative_area_level_3 both say
            # "Kitchener") and the earlier entry is the more specific.
            parts.setdefault(kind, {"short": component["shortText"], "long": component["longText"]})

    def short(kind: str) -> str | None:
        entry = parts.get(kind)
        return entry["short"] if entry else None

    def long(kind: str) -> str | None:
        entry = parts.get(kind)
        entry_value = entry["long"] if entry else None
        return entry_value

    # "50" + "Pinnacle Dr" -> "50 Pinnacle Dr". route's SHORT text is used
    # because that is how an address is written on a policy ("Dr", not
    # "Drive"). Either part can be missing — a rural route has no street
    # number — so this joins whatever is present rather than assuming both.
    street = " ".join(p for p in (short("street_number"), short("route")) if p) or None

    return {
        "street_address": street,
        # locality is the city proper. Falls back to the sublocality types some
        # smaller Ontario places use instead.
        "city": long("locality") or long("postal_town") or long("sublocality"),
        # 'ON', not 'Ontario' — the short form is what a policy carries.
        "province": short("administrative_area_level_1"),
        "postal_code": short("postal_code"),
        "formatted": body.get("formattedAddress"),
    }
