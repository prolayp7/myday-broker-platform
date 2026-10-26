"""Commission projection. Two rates, multiplied on read, nothing stored.

    auto     = new_auto_premium_annual                              x auto rate
    property = new_homeowner_premium_annual + new_tenant_premium_annual
                                                                x property rate
    total    = auto + property

THERE IS NO COMMISSION TABLE, and that is the design rather than an omission.
Every figure this module returns is computed from three premium columns and
two settings, at the moment it is asked for. Nothing is written, so nothing
can disagree with anything else.

The module this replaced (deleted in 0083) stored a row per lead per product,
and therefore had to rebuild those rows on every premium edit, every stage
change and every policy-type switch — five call sites across two services.
That fan-out was where its bugs lived. Multiplying on read has no fan-out to
have bugs in.

TWO CONSEQUENCES, both accepted:
  * a broker corrects a premium -> the projection follows it, immediately
  * an admin changes a rate     -> every projection uses the new one, months
                                   already reported included

These are reference figures a broker reads, not a payment record anyone is
paid from. Freezing a past month would mean storing something, and storing
something is the whole thing this avoids.
"""

import logging
from decimal import Decimal

from fastapi import HTTPException

from repositories import commission_settings as repo
from schemas.commission import UpdateCommissionRatesRequest

logger = logging.getLogger("myday.commission")


def get_rates() -> dict:
    """The two rates.

    500 rather than a default when the row is missing. The migration seeds it
    and a CHECK stops a second one existing, so an empty table means the
    migration did not run — and inventing a rate would report projections
    computed from a number nobody chose, which is worse than refusing.
    """
    rows = repo.get().data or []
    if not rows:
        logger.error("commission_settings is empty — migration 0084 has not run")
        raise HTTPException(status_code=500, detail="No commission rates configured")
    return rows[0]


def update_rates(request: UpdateCommissionRatesRequest) -> dict:
    """Change the rate card.

    ALL THREE, ALWAYS. They are one card, read together on every projection;
    accepting a partial change would allow a moment where one is the new value
    and one the old, with a report in between showing neither.

    Takes effect everywhere immediately, past months included — see the module
    docstring. The screen says so.
    """
    rows = repo.update(
        {
            "auto_rate_pct": str(request.auto_rate_pct),
            "property_rate_pct": str(request.property_rate_pct),
            "broker_share_pct": str(request.broker_share_pct),
        }
    ).data or []
    if not rows:
        raise HTTPException(status_code=500, detail="Could not save the rates")
    return rows[0]


def _money(value) -> Decimal:
    """A premium column as a Decimal, treating "not entered" as zero.

    COALESCED HERE, NOT IN SQL, and that is not a preference. The annual
    columns are `monthly * 12` (migrations/0067) with no coalesce of their
    own, so a product the broker never entered comes back as None — and
    Decimal(str(None)) raises rather than returning zero.

    str() first: PostgREST hands numerics back as strings, and Decimal(float)
    would reintroduce exactly the drift numeric exists to prevent.
    """
    return Decimal(str(value)) if value is not None else Decimal(0)


def project(premium: dict, rates: dict) -> dict:
    """What one issued policy projects, given the premium row and the rates.

    Takes both rather than fetching them, so a report over thirty policies
    reads the settings once instead of thirty times.

    CONDO HAS NO CASE HERE, deliberately — a condo is owner-occupied property
    and PRODUCTS_FOR maps "Auto and Condo" to {auto, homeowner}, so its
    premium is already in the homeowner column and the property line below
    covers it. The absence of a branch is the correct behaviour, not a gap.

    NO UNIT MULTIPLIER, AND THAT IS NOW CORRECT. This function reads the three
    new_*_premium_annual columns raw. Between 0101 and 0106 those columns held
    whatever the broker typed — so a monthly-entered policy would have
    projected a TWELFTH of the commission actually earned, silently. It never
    shipped that way only because project() has no production call site yet.
    0106 made the annual columns genuinely annual, which fixes this by
    construction rather than by adding a fifth hand-written ×12.

    Money leaves as STRINGS, like every other figure crossing this boundary:
    a Decimal survives the trip that way and a float does not. No rounding is
    imposed — schemas/premium_history.py declines to set that convention, and
    the frontend's money() already rounds at render.
    """
    auto_rate = _money(rates.get("auto_rate_pct")) / 100
    property_rate = _money(rates.get("property_rate_pct")) / 100

    auto_premium = _money(premium.get("new_auto_premium_annual"))
    property_premium = _money(premium.get("new_homeowner_premium_annual")) + _money(
        premium.get("new_tenant_premium_annual")
    )

    auto_commission = auto_premium * auto_rate
    property_commission = property_premium * property_rate

    return {
        "auto_premium_annual": str(auto_premium),
        "property_premium_annual": str(property_premium),
        "auto_commission": str(auto_commission),
        "property_commission": str(property_commission),
        "total_commission": str(auto_commission + property_commission),
    }


def broker_commission(premium_rows: list[dict], rates: dict) -> Decimal:
    """What the broker earned on these policies: each one's commission (see
    project above) summed, then the broker's share of the total (0134).

    One split for every broker, so the share comes from the same settings row
    as the rates. Summed BEFORE the share is applied, so rounding happens once
    at render rather than once per policy.

    A MISSING SHARE IS REFUSED, not read as 0%: that would show every broker
    $0 earned, a wrong number nobody would question. It means migration 0134
    has not run.
    """
    if rates.get("broker_share_pct") is None:
        raise ValueError("commission_settings.broker_share_pct is not set — migration 0134 has not run")
    total = sum(
        (Decimal(project(row, rates)["total_commission"]) for row in premium_rows),
        Decimal("0"),
    )
    return total * _money(rates.get("broker_share_pct")) / 100
