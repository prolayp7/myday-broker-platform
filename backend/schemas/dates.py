"""Bounded date types, shared by every schema that collects one.

WHY THIS EXISTS: a plain `date | None` accepts any date Python can represent,
so "1800-12-05" and "0001-01-01" were saved as vehicle purchase dates without
complaint. A date typed with a slipped digit looks like data — nothing
downstream can tell 2098 from 1998 — so the place to catch it is the moment it
arrives.

TWO SHAPES, not one blanket rule. A date of birth cannot be in the future; a
car's purchase date can, because a broker quotes a car the customer is picking
up next month. Applying either rule to the other field would be wrong in one
direction or the other, so the distinction is named here rather than left to
whoever adds the next field.

The lower bound is 1900 throughout: earlier than that is a typo in every
context this system collects dates for.
"""

from datetime import date
from typing import Annotated

from pydantic import AfterValidator

_EARLIEST = date(1900, 1, 1)
# A century out. Generous on purpose — the bound exists to catch a slipped
# digit ("2098" for "2028"), not to second-guess a broker who has a reason for
# a date further out than usual.
_FUTURE_YEARS = 100


def _validate_past(value: date) -> date:
    """A date that has already happened."""
    if value < _EARLIEST:
        raise ValueError(f"must be on or after {_EARLIEST.isoformat()}")
    if value > date.today():
        raise ValueError("can't be in the future")
    return value


def _validate_wide(value: date) -> date:
    """A date that may be in the future, within reason."""
    if value < _EARLIEST:
        raise ValueError(f"must be on or after {_EARLIEST.isoformat()}")
    latest = date(date.today().year + _FUTURE_YEARS, 12, 31)
    if value > latest:
        raise ValueError(f"must be on or before {latest.isoformat()}")
    return value


#: For anything that has already happened — a birth, a licence, a policy that
#: started. None of these can be in the future.
PastDate = Annotated[date, AfterValidator(_validate_past)]

#: For anything that may still be ahead — a car being bought next month, a
#: policy starting on renewal.
WideDate = Annotated[date, AfterValidator(_validate_wide)]
