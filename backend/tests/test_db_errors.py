"""Tests for db_error — what it routes, and what it must never write to a log.

WHY THIS FILE EXISTS. db_error is the single handler behind 38 call sites, and
it used to log str(err): the whole postgrest APIError dict. Postgres splits a
failure into a `message` naming the constraint and a `details` carrying the
DATA that broke it —

    message  violates unique constraint "broker_email_key"
    details  Key (email)=(someone@example.com) already exists.

— and on a check or not-null violation `details` is "Failing row contains
(...)": the entire row, every column. On Railway that went to a retained,
searchable log store, one line per failed write. The two constraints that
expose real people are broker.email and lead_phone.phone_e164, a CUSTOMER's
phone number.

Nothing failed and nothing looked wrong, which is why it needs a test rather
than a review.

The routing tests matter just as much. The three branches used to substring-
match against str(err); they now read .code. That is a behaviour change in the
same edit as the logging change, and getting it wrong turns a mapped 409 into
a generic 500 — visible to users, unlike the leak.
"""

import logging

import pytest
from fastapi import HTTPException

from db_errors import db_error


class FakeAPIError(Exception):
    """Shaped like postgrest's APIError: the attributes db_error reads, and a
    str() carrying everything — which is exactly what used to be logged."""

    def __init__(self, message, code, details=None, hint=None):
        self.message = message
        self.code = code
        self.details = details
        self.hint = hint
        super().__init__(
            {"message": message, "code": code, "details": details, "hint": hint}
        )


EMAIL = "someone@example.com"
PHONE = "+14165550199"

UNIQUE_EMAIL = FakeAPIError(
    'duplicate key value violates unique constraint "broker_email_key"',
    "23505",
    details=f"Key (email)=({EMAIL}) already exists.",
)
UNIQUE_PHONE = FakeAPIError(
    'duplicate key value violates unique constraint "idx_lead_phone_lead_number"',
    "23505",
    details=f"Key (lead_id, phone_e164)=(abc, {PHONE}) already exists.",
)
CHECK_WHOLE_ROW = FakeAPIError(
    'new row for relation "broker" violates check constraint "broker_role_check"',
    "23514",
    details=f"Failing row contains (abc, Jane Doe, {EMAIL}, {PHONE}, $2b$12$hash).",
)
NOT_NULL_WHOLE_ROW = FakeAPIError(
    'null value in column "group_id" violates not-null constraint',
    "23502",
    details=f"Failing row contains (abc, null, Jane Doe, {EMAIL}).",
)
FOREIGN_KEY = FakeAPIError(
    'violates foreign key constraint "broker_group_id_fkey"',
    "23503",
    details="Key (group_id)=(0000) is not present in table.",
)
GROUP_FULL = FakeAPIError("This group already has 10 brokers", "P0001")


# ── the leak ─────────────────────────────────────────────────────────────
class TestNothingSensitiveIsLogged:
    @pytest.mark.parametrize(
        "err,secret",
        [
            (UNIQUE_EMAIL, EMAIL),
            (UNIQUE_PHONE, PHONE),
            (CHECK_WHOLE_ROW, "Jane Doe"),
            (CHECK_WHOLE_ROW, "$2b$12$hash"),
            (NOT_NULL_WHOLE_ROW, EMAIL),
            (FOREIGN_KEY, "Key (group_id)"),
        ],
    )
    def test_the_failing_values_never_reach_the_log(self, err, secret, caplog):
        with caplog.at_level(logging.ERROR, logger="myday.db"):
            db_error(err)
        assert caplog.text, "nothing logged — the assertion would pass vacuously"
        assert secret not in caplog.text

    @pytest.mark.parametrize(
        "err", [UNIQUE_EMAIL, CHECK_WHOLE_ROW, NOT_NULL_WHOLE_ROW, FOREIGN_KEY]
    )
    def test_details_is_never_logged_in_any_form(self, err, caplog):
        with caplog.at_level(logging.ERROR, logger="myday.db"):
            db_error(err)
        assert err.details not in caplog.text
        assert "Failing row contains" not in caplog.text

    def test_no_traceback_is_attached(self, caplog):
        """logger.exception would append the traceback, whose last line is the
        exception's own repr — the full dict, details and all. Sanitising the
        message while exception() reprinted the raw error underneath it would
        have been a fix that changed nothing."""
        try:
            raise UNIQUE_EMAIL
        except Exception as err:
            with caplog.at_level(logging.ERROR, logger="myday.db"):
                db_error(err)
        assert "Traceback" not in caplog.text
        assert EMAIL not in caplog.text

    # ── but it must still be debuggable ──────────────────────────────────
    def test_the_constraint_name_is_kept(self, caplog):
        """The whole point of logging this at all. A 500 that cannot say which
        constraint failed has traded a leak for blindness."""
        with caplog.at_level(logging.ERROR, logger="myday.db"):
            db_error(UNIQUE_EMAIL)
        assert "broker_email_key" in caplog.text
        assert "23505" in caplog.text


# ── routing must not regress ─────────────────────────────────────────────
class TestRoutingStillWorks:
    FK = HTTPException(409, "still in use")
    UNIQUE = HTTPException(409, "already exists")
    FULL = HTTPException(409, "group is full")

    def test_foreign_key_routes(self):
        assert db_error(FOREIGN_KEY, on_foreign_key=self.FK) is self.FK

    def test_unique_routes(self):
        assert db_error(UNIQUE_EMAIL, on_unique=self.UNIQUE) is self.UNIQUE

    def test_group_full_routes(self):
        assert db_error(GROUP_FULL, on_group_full=self.FULL) is self.FULL

    def test_an_unhandled_code_falls_through_to_500(self):
        assert db_error(CHECK_WHOLE_ROW).status_code == 500

    def test_a_handler_for_a_different_code_is_not_used(self):
        """A unique violation must not be answered with the foreign-key
        response just because a handler was passed."""
        assert db_error(UNIQUE_EMAIL, on_foreign_key=self.FK).status_code == 500

    def test_the_500_never_carries_internal_detail(self):
        assert db_error(CHECK_WHOLE_ROW).detail == "Something went wrong"

    def test_a_plain_exception_still_routes_by_substring(self):
        """The fallback path. Something that is not an APIError has no .code,
        and without the substring test a mapped 409 would silently become a
        500 — visible to users, unlike the leak this file is mostly about."""
        assert db_error(Exception("... 23505 ..."), on_unique=self.UNIQUE) is self.UNIQUE

    def test_a_plain_exception_with_no_match_is_a_500(self, caplog):
        with caplog.at_level(logging.ERROR, logger="myday.db"):
            result = db_error(Exception("something else entirely"))
        assert result.status_code == 500
        assert "something else entirely" in caplog.text
