"""The premium request schema — what the API will and will not accept.

REPLACES tests/test_premium_math.py, which tested premium_math.py: a module
that duplicated the SQL totals in Python "so the money rules could be tested
without a live Supabase". It had no production caller, no concept of the
monthly/annual unit, and a docstring that contradicted itself. Postgres
computes the totals; a second copy of the arithmetic was one more thing to
drift, and 0106 removed the last reason to keep it.

What survives is the part that guards a real failure: extra="forbid".
"""

import os

import pytest

os.environ.setdefault(
    "JWT_SECRET_KEY",
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
)
os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")

from decimal import Decimal  # noqa: E402

from pydantic import ValidationError  # noqa: E402

from schemas.premium_history import (  # noqa: E402
    PremiumResponse,
    SavePremiumRequest,
)


class TestBothHalvesOfAPairAreAccepted:
    """0106 gave every product a monthly column and an annual one. The broker
    types into whichever their unit names, and the database derives the other,
    so BOTH names have to be sendable."""

    def test_a_monthly_figure_is_accepted(self):
        request = SavePremiumRequest(current_auto_premium_monthly=Decimal("320"))
        assert request.current_auto_premium_monthly == Decimal("320")

    def test_an_annual_figure_is_accepted_unchanged(self):
        """$6,950 stays $6,950 — nothing is converted on the way in."""
        request = SavePremiumRequest(new_auto_premium_annual=Decimal("6950"))
        assert request.new_auto_premium_annual == Decimal("6950")

    def test_every_product_has_both_names(self):
        fields = SavePremiumRequest.model_fields
        for side in ("current", "new"):
            for product in ("auto", "homeowner", "tenant"):
                for unit in ("annual", "monthly"):
                    assert f"{side}_{product}_premium_{unit}" in fields

    def test_there_is_no_condo_field(self):
        """Condo shares the homeowner pair — it earns the homeowner rate, and
        a separate column would be a distinction nothing uses."""
        assert not [f for f in SavePremiumRequest.model_fields if "condo" in f]


class TestTheGuardThatMakesAMissLoud:
    """extra="forbid" is the reason a wrong column name is a 422 and not a
    200-that-saves-nothing.

    THIS HAS HAPPENED. The schema silently ignored unknown fields until 0099,
    and a browser tab left open across that deploy POSTed a field the model no
    longer knew, was told 200, and saved nothing — the broker watched a
    successful save lose their figure. Any rename of these columns can
    reproduce it, which is exactly why the guard is tested rather than assumed.
    """

    def test_an_unknown_field_is_refused(self):
        with pytest.raises(ValidationError):
            SavePremiumRequest(current_auto_premum_annual=Decimal("580"))

    def test_a_generated_column_cannot_be_sent(self):
        """The totals and savings are computed by Postgres. Accepting one
        would let a client send a total that disagrees with its own parts."""
        with pytest.raises(ValidationError):
            SavePremiumRequest(total_new_premium_annual=Decimal("5760"))

    def test_a_negative_premium_is_refused(self):
        with pytest.raises(ValidationError):
            SavePremiumRequest(current_auto_premium_monthly=Decimal("-1"))


class TestPatchSemantics:
    """A broker fills this in over several conversations, so an omitted field
    must be left alone while an explicit null clears it. The service dumps
    with exclude_unset, which only works because every field is optional."""

    def test_an_omitted_field_is_not_in_the_dump(self):
        request = SavePremiumRequest(current_auto_premium_monthly=Decimal("320"))
        dumped = request.model_dump(exclude_unset=True)
        assert dumped == {"current_auto_premium_monthly": Decimal("320")}

    def test_an_explicit_null_survives_the_dump(self):
        """Clearing a figure has to reach the database as a null, not vanish."""
        request = SavePremiumRequest(current_auto_premium_monthly=None)
        assert "current_auto_premium_monthly" in request.model_dump(exclude_unset=True)


class TestAnUnansweredUnitSurvivesTheResponse:
    """THE REGRESSION THAT BROKE BOTH SYMPTOMS AT ONCE.

    Migration 0107 made premium_unit nullable, so a lead nobody has answered
    holds null. PremiumResponse still declared it as
    `Literal["year","month"] = "year"` — non-nullable, with a default. Two
    things followed, and they looked like separate bugs:

      * every save of an unanswered row failed validation and came back a 500,
        so the broker was told "Could not save that premium"; and
      * any row that did validate had "year" substituted for the missing
        answer, so all six toggles lit up Year — the exact default 0107 had
        just removed from the database.

    A response model that supplies a value the database does not hold is
    inventing data. Here it invented an answer to the one question the whole
    feature exists to ask.
    """

    def _row(self, **over):
        import uuid
        from datetime import datetime

        row = {
            "id": uuid.uuid4(),
            "lead_id": uuid.uuid4(),
            "created_at": datetime.now(),
            "updated_at": datetime.now(),
        }
        row.update(over)
        return row

    def test_a_null_unit_validates(self):
        response = PremiumResponse(**self._row(current_premium_unit=None))
        assert response.current_premium_unit is None

    def test_a_missing_unit_is_not_filled_in_with_year(self):
        """The default is what lit every toggle. Absent must stay absent."""
        response = PremiumResponse(**self._row())
        assert response.current_premium_unit is None
        assert response.new_premium_unit is None

    def test_a_real_unit_still_survives(self):
        response = PremiumResponse(**self._row(current_premium_unit="month"))
        assert response.current_premium_unit == "month"
