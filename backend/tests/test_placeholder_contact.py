"""What counts as "no value was collected".

A manually created lead carries the literal "(not provided)" in whichever
contact detail was not collected (services/lead.py::_NOT_PROVIDED), so a plain
truthiness check reads that as a real answer and lets a lead through with
nothing for Meta to match on. services/driver.py::_real_or_none is the rule.

THIS FILE EXISTS BECAUSE THE RULE WAS ONCE WRONG. _real_or_none tested
`if value and ...` BEFORE stripping, so "   " was truthy, never reached the
placeholder match, and counted as a real answer. An applicant whose address and
city held only spaces was reported complete and could reach Quote Done. The
last test here is that regression, at the level a broker meets it.

It was originally a PARITY test: migration 0095 needed the same rule in SQL for
its applicant-complete trigger, and this asserted the two agreed. 0096 removed
that trigger and dropped the SQL function with it, so there is one
implementation again and nothing to compare against.
"""

import sys

import pytest

sys.path.insert(0, ".")

from services.driver import _real_or_none  # noqa: E402

# (value, is_empty). One list, both implementations — see the docstring.
CASES = [
    (None, True),
    ("", True),
    # THE BUG. Python's .strip() takes all whitespace, so the SQL uses
    # `v ~ '^\s*$'` rather than btrim(), which only strips spaces.
    (" ", True),
    ("   ", True),
    ("\t", True),
    ("\n", True),
    (" \t \n ", True),
    # What create_manual_lead writes for a detail it did not collect.
    ("(not provided)", True),
    ("Not Provided", True),
    (" Not Provided ", True),
    ("not available", True),
    ("n/a", True),
    ("N/A", True),
    ("(N/A)", True),
    ("na", True),
    ("unknown", True),
    ("none", True),
    ("None", True),
    # NEAR-MISSES. A prefix match instead of a full match would wrongly kill
    # both of these — "no" is not "none", "nonesuch" is a real word.
    ("no", False),
    ("nonesuch", False),
    ("nan", False),
    # Real values. 'Anna Lakshmi' guards the internal space: a fix that
    # COLLAPSED whitespace rather than stripping it would break this name, and
    # nothing else in the list would notice.
    ("Mitali", False),
    ("Anna Lakshmi", False),
    ("1 King St W", False),
    ("M5H 1A1", False),
]


@pytest.mark.parametrize("value,expected_empty", CASES)
def test_python_treats_it_as_empty_or_not(value, expected_empty):
    assert (_real_or_none(value) is None) is expected_empty, (
        f"disagreement on {value!r}"
    )


def test_whitespace_leaves_the_applicant_incomplete():
    """THE REGRESSION, at the level the broker meets it.

    Before the fix this applicant reported `missing == []` and could reach
    Quote Done — firing CompleteRegistration for a lead that could never have
    fired Lead.
    """
    from services.lead import _REQUIRED_APPLICANT_FIELDS

    applicant = {
        "first_name": "Anna",
        "last_name": "Lakshmi",
        "phone": "+15550001111",
        "email": "anna@example.com",
        "date_of_birth": "1990-01-01",
        "gender": "Female",
        "street_address": "   ",
        "city": " ",
        "postal_code": "M5H 1A1",
    }
    missing = [
        label
        for column, label in _REQUIRED_APPLICANT_FIELDS.items()
        if not _real_or_none(applicant.get(column))
    ]
    assert missing == ["address", "city"]
