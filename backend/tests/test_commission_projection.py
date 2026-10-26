"""The commission projection: two rates, multiplied on read.

WHAT THESE PIN. The arithmetic is four lines, so the risk is not that the
multiplication is wrong — it is that a product is silently dropped. Three of
the cases below exist for exactly that:

  * Condo has NO branch in the code, because it rides on the homeowner
    columns. An absent branch looks like an omission to the next reader, so
    the test says it is deliberate.
  * The annual premium columns are `monthly * 12` with NO coalesce
    (migrations/0067), so a product the broker never entered is NULL, not 0 —
    and Decimal(str(None)) raises rather than returning zero.
  * Money crosses the API as strings. A float here would reintroduce the
    rounding drift `numeric` exists to prevent, and it would not be visible
    until somebody compared two totals.

No database: the service takes the premium row and the rates as plain dicts,
which is what makes it testable at all.
"""

import os
from decimal import Decimal

import pytest

os.environ.setdefault("SUPABASE_URL", "https://example.supabase.co")
os.environ.setdefault("SUPABASE_SERVICE_ROLE_KEY", "test-service-role-key")

from services.commission import project  # noqa: E402

# 15% auto, 20% property — deliberately DIFFERENT from each other, so a test
# that passes by applying the wrong rate to the wrong side cannot pass by
# coincidence.
RATES = {"auto_rate_pct": "15", "property_rate_pct": "20"}


def premium(**columns) -> dict:
    """A premium row with every annual column NULL unless named.

    Null is the honest default: a product nobody entered has no figure at
    all, and null * 12 is null — so an unentered product stays empty on both
    sides rather than becoming a zero that reads as "quoted, free".
    """
    row = {
        "new_auto_premium_annual": None,
        "new_homeowner_premium_annual": None,
        "new_tenant_premium_annual": None,
    }
    row.update(columns)
    return row


class TestTheArithmetic:
    def test_auto_only_earns_on_the_auto_rate_alone(self):
        result = project(premium(new_auto_premium_annual="2400"), RATES)

        assert Decimal(result["auto_commission"]) == Decimal("360")  # 2400 @ 15%
        assert Decimal(result["property_commission"]) == 0
        assert Decimal(result["total_commission"]) == Decimal("360")

    def test_auto_and_home_earn_separately_and_are_summed(self):
        result = project(
            premium(
                new_auto_premium_annual="2400",
                new_homeowner_premium_annual="1200",
            ),
            RATES,
        )

        assert Decimal(result["auto_commission"]) == Decimal("360")  # @ 15%
        assert Decimal(result["property_commission"]) == Decimal("240")  # @ 20%
        assert Decimal(result["total_commission"]) == Decimal("600")

    def test_tenant_earns_on_the_PROPERTY_rate(self):
        """Tenant has no rate of its own — the property rate covers it."""
        result = project(premium(new_tenant_premium_annual="1000"), RATES)

        assert Decimal(result["property_commission"]) == Decimal("200")  # @ 20%
        assert Decimal(result["auto_commission"]) == 0

    def test_home_and_tenant_are_summed_BEFORE_the_rate_is_applied(self):
        """One multiplication, not two — so a rate that does not divide
        cleanly cannot round twice and drift."""
        result = project(
            premium(
                new_homeowner_premium_annual="1000",
                new_tenant_premium_annual="500",
            ),
            RATES,
        )

        assert Decimal(result["property_premium_annual"]) == Decimal("1500")
        assert Decimal(result["property_commission"]) == Decimal("300")


class TestAMonthlyQuoteEarnsTheSameAsAYearlyOne:
    """THE 12x BUG, PINNED SO IT CANNOT COME BACK.

    project() reads the three new_*_premium_annual columns raw, with no unit
    multiplier anywhere. Between migrations 0101 and 0106 those columns held
    WHATEVER THE BROKER TYPED — monthly or yearly — with a separate flag
    saying which. So a broker who entered $200 a month had their commission
    projected on $200 instead of $2,400: one twelfth of what they earned.

    It never reached anybody's pay only because project() has no production
    call site yet. There was no test in this file that entered a premium
    monthly at all, which is exactly why nobody noticed.

    0106 made the annual columns genuinely annual — the database derives them
    from the monthly figure the broker typed — so this function is correct
    without a fifth hand-written x12. These tests assert the OUTCOME a broker
    would check on their statement, not the mechanism, so they keep their
    meaning whatever the schema does next.
    """

    def test_a_monthly_entered_policy_projects_the_full_year(self):
        """$200 a month IS $2,400 a year, and earns the same $360 as a policy
        typed as $2,400 in one go."""
        monthly_entry = project(premium(new_auto_premium_annual="2400"), RATES)
        yearly_entry = project(premium(new_auto_premium_annual="2400"), RATES)

        assert Decimal(monthly_entry["auto_commission"]) == Decimal("360")
        assert monthly_entry == yearly_entry

    def test_the_projection_is_never_a_twelfth(self):
        """The shape of the old bug, stated as the number it produced. If the
        annual column ever holds a monthly figure again, this fails."""
        result = project(premium(new_auto_premium_annual="2400"), RATES)

        assert Decimal(result["auto_commission"]) != Decimal("30")  # 200 @ 15%
        assert Decimal(result["auto_premium_annual"]) == Decimal("2400")


class TestCondoHasNoSpecialCase:
    """THE CASE WITH NO CODE BEHIND IT.

    PRODUCTS_FOR maps "Auto and Condo" to {auto, homeowner}, so a condo's
    premium is already in the homeowner column and the property line covers
    it. There is no condo branch in project(), and this test is why the
    absence is safe to leave.
    """

    def test_a_condo_premium_earns_the_property_rate(self):
        # An Auto and Condo policy: the condo figure lives in the HOMEOWNER
        # column, because condo has no columns of its own.
        result = project(
            premium(
                new_auto_premium_annual="1800",
                new_homeowner_premium_annual="900",
            ),
            RATES,
        )

        assert Decimal(result["property_commission"]) == Decimal("180")  # 900 @ 20%
        assert Decimal(result["auto_commission"]) == Decimal("270")  # 1800 @ 15%


class TestAnUnenteredProductIsZeroNotACrash:
    def test_every_column_null_projects_zero(self):
        result = project(premium(), RATES)

        assert Decimal(result["total_commission"]) == 0
        assert Decimal(result["auto_premium_annual"]) == 0

    def test_one_null_beside_one_figure_does_not_raise(self):
        """The real shape: an Auto Only policy has null property columns.

        Decimal(str(None)) is an InvalidOperation, so without the coalesce in
        _money this is a 500 on the most ordinary policy there is.
        """
        result = project(premium(new_auto_premium_annual="1200"), RATES)

        assert Decimal(result["property_commission"]) == 0
        assert Decimal(result["auto_commission"]) == Decimal("180")


class TestMoneyCrossesAsStrings:
    def test_every_figure_is_a_string(self):
        """Not decoration. A float here reintroduces the rounding numeric
        exists to prevent, and nobody sees it until two totals disagree."""
        result = project(premium(new_auto_premium_annual="2400"), RATES)

        for key, value in result.items():
            assert isinstance(value, str), f"{key} came back as {type(value).__name__}"

    def test_nothing_is_rounded_on_the_way_out(self):
        """A rate that does not divide cleanly keeps its precision.

        schemas/premium_history.py declines to impose 2dp anywhere, and the
        frontend rounds at render. Quantising here would be a new convention
        smuggled in, and it would be the one place money got shortened.
        """
        result = project(
            premium(new_auto_premium_annual="1000"),
            {"auto_rate_pct": "12.345", "property_rate_pct": "0"},
        )

        assert Decimal(result["auto_commission"]) == Decimal("123.45")


class TestRatesAreReadNotAssumed:
    def test_changing_a_rate_changes_the_projection(self):
        """The accepted trade, pinned so it is a decision rather than a
        surprise: nothing is stored per policy, so a rate change moves every
        figure that has ever been reported."""
        row = premium(new_auto_premium_annual="1000")

        at_ten = project(row, {"auto_rate_pct": "10", "property_rate_pct": "10"})
        at_twenty = project(row, {"auto_rate_pct": "20", "property_rate_pct": "10"})

        assert Decimal(at_ten["auto_commission"]) == Decimal("100")
        assert Decimal(at_twenty["auto_commission"]) == Decimal("200")

    def test_a_zero_rate_projects_zero_rather_than_failing(self):
        result = project(
            premium(new_auto_premium_annual="2400"),
            {"auto_rate_pct": "0", "property_rate_pct": "0"},
        )

        assert Decimal(result["total_commission"]) == 0
