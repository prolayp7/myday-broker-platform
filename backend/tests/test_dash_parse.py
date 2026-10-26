"""Tests for reading a DASH report.

WHY THIS FILE EXISTS. A parser that misses a field fails LOUDLY — the form is
blank and the broker types it. A parser that misses a RECORD fails silently:
the form shows one car, looks filled in, and nobody notices the second one is
gone. That is exactly what happened while this was being written — the first
version returned one vehicle from a two-vehicle policy and the output looked
entirely healthy.

So the assertions here are about COUNTS and EXACT VALUES against a real
report, not about the parser not crashing.

The fixture is a genuine 8-page DASH report read by Azure, with every
identifying value replaced by an invented one of the same shape. The two
awkward shapes are preserved deliberately, because they are what the parser
has to survive:

    SAMPLE,TESTCASE A            no space after the comma
    1 EXAMPLE ST, , SAMPLETOWN   an empty comma-separated part
"""

from pathlib import Path

import pytest

from services import dash_parse

FIXTURE = Path(__file__).parent / "fixtures" / "dash_sample.md"


@pytest.fixture(scope="module")
def report() -> str:
    return FIXTURE.read_text(encoding="utf-8")


class TestTheDriver:
    def test_the_name_keeps_dash_own_spacing(self, report):
        """THE REASON THIS RUNS ON AZURE AT ALL. DASH prints "SAMPLE,TESTCASE A"
        with no space after the comma; the deleted Gemini extractor returned
        "SAMPLE, TESTCASE A" and its own docstring recorded that two rounds of
        explicit prompting could not fix it. Normalising here would throw away
        the accuracy that motivated the change."""
        assert dash_parse.parse_driver(report)["name"] == "SAMPLE,TESTCASE A"

    def test_the_licence_splits_from_its_province(self, report):
        driver = dash_parse.parse_driver(report)
        assert driver["license_number"] == "S1234-56789-01234"
        assert driver["license_province"] == "Ontario"

    def test_gender_and_marital_status(self, report):
        """Both are printed on one line with the next label following, so a
        match that took the rest of the line would swallow it."""
        driver = dash_parse.parse_driver(report)
        assert driver["gender"] == "Male"
        assert driver["marital_status"] == "Married"

    def test_the_address_survives_its_empty_part(self, report):
        """DASH prints "1 EXAMPLE ST, , SAMPLETOWN ON M1A1A1" — the empty
        segment is where a unit number would go. A naive split on commas makes
        the blank the city and shifts everything after it."""
        driver = dash_parse.parse_driver(report)
        assert driver["street_address"] == "1 EXAMPLE ST"
        assert driver["city"] == "SAMPLETOWN"
        assert driver["province"] == "ON"
        assert driver["postal_code"] == "M1A1A1"

    def test_date_of_birth_is_never_filled(self, report):
        """DASH prints only the YEAR, and driver.date_of_birth is a date.
        Inventing a month and day would put a wrong value in a field the
        broker is REQUIRED to complete before Quote Done — worse than leaving
        it blank, because a wrong date looks answered."""
        assert "date_of_birth" not in dash_parse.parse_driver(report)

    def test_at_fault_claims_are_read_from_the_header(self, report):
        """THE NUMBER THE PREFERRED CHECK TURNS ON.

        Read straight off DASH's own count rather than totted up from the Claims
        table, which carries a percentage per claim and *THIRD PARTY* rows
        belonging to other people.

        An int, not a string: driver.at_fault_claims_6y is an int column (0114).
        """
        assert dash_parse.parse_driver(report)["at_fault_claims_6y"] == 2

    def test_at_fault_survives_sharing_a_line_with_its_neighbour(self, report):
        """THE PARSING HAZARD, PINNED.

        Every real report prints this on one physical line:

            Number of Policies at this Address: Not available Number of At-Fault Claims in Last 6 Years: 2

        _field stops a value at the next [A-Z][A-Za-z/ ]{2,30}: — letters, slash
        and space only — and this label has a hyphen and digits, so it cannot act
        as a stop-label. Reading it directly is therefore fine; reading its
        neighbour is not. This asserts the value is the at-fault number alone and
        has not absorbed any of the text beside it.
        """
        value = dash_parse.parse_driver(report)["at_fault_claims_6y"]
        assert isinstance(value, int)
        assert value == 2

    def test_at_fault_is_absent_rather_than_zero_when_unreadable(self):
        """UNKNOWN IS NOT CLEAN — the distinction the whole classifier rests on.

        A report with no such line, or one printing "Not available", must leave
        the key out entirely. A silent 0 would mean "no at-fault claims" and send
        an unvetted lead to the premium dataset looking spotless.
        """
        assert "at_fault_claims_6y" not in dash_parse.parse_driver(
            "SAMPLE,TESTCASE A\nGender: Male\n"
        )
        assert "at_fault_claims_6y" not in dash_parse.parse_driver(
            "SAMPLE,TESTCASE A\n"
            "Number of At-Fault Claims in Last 6 Years: Not available\n"
        )

    def test_only_the_header_is_read(self, report):
        """The same labels appear again inside policy blocks, for OTHER
        operators. Reading past the first section would fill the applicant's
        form with a co-insured's details — this report has a second person on
        every policy."""
        driver = dash_parse.parse_driver(report)
        assert "OTHERPERSON" not in driver["name"]
        assert driver["license_number"] != "O9876-54321-09876"


class TestTheVehicles:
    def test_both_cars_on_the_current_policy_are_found(self, report):
        """THE BUG THIS FILE EXISTS FOR. The first version anchored on the
        "Vehicle #N:" marker and returned ONE car from this two-car policy,
        because DASH prints the label and the car in separate table cells and
        sometimes puts "Vehicle Code:" between them. Nothing raised; the parse
        just quietly lost a vehicle."""
        vehicles = dash_parse.parse_vehicles(report)
        assert len(vehicles) == 2, f"expected both cars, got {vehicles}"

        vins = {v["vin"] for v in vehicles}
        assert vins == {"1AAAA1A1AAA000001", "2BBBB2B2BBB000002"}

    def test_the_make_and_model_are_exact(self, report):
        vehicles = {v["vin"]: v for v in dash_parse.parse_vehicles(report)}
        assert vehicles["1AAAA1A1AAA000001"]["year_make_model"] == "2024 HONDA - CIVIC TOURING 4DR"
        assert vehicles["2BBBB2B2BBB000002"]["year_make_model"] == "2021 ACURA - TLX A-SPEC 4DR AWD"

    def test_each_car_gets_its_own_distances(self, report):
        """Read from the car's OWN segment, not the policy's. The two cars on
        this policy have different figures, so a parser reading the block as a
        whole would give both the first car's."""
        vehicles = {v["vin"]: v for v in dash_parse.parse_vehicles(report)}
        assert vehicles["1AAAA1A1AAA000001"]["one_day_distance_km"] == 15
        assert vehicles["1AAAA1A1AAA000001"]["annual_km"] == 14000
        assert vehicles["2BBBB2B2BBB000002"]["one_day_distance_km"] == 0
        assert vehicles["2BBBB2B2BBB000002"]["annual_km"] == 8000

    def test_older_policies_are_not_included(self, report):
        """This report has 11 policy blocks going back to 2016, and a 2011
        Honda on the older ones. Only the current policy's cars are the cars
        being insured."""
        vins = {v["vin"] for v in dash_parse.parse_vehicles(report)}
        assert "3CCCC3C3CCC000003" not in vins, "a car from a 2016 policy was included"

    def test_coverage_is_not_read(self, report):
        """Decided explicitly: DASH prints the PREVIOUS insurer's coverage on a
        policy that may be years old, and the quote's coverage is the broker's
        decision."""
        for vehicle in dash_parse.parse_vehicles(report):
            assert "coverage_type" not in vehicle


class TestTheRulesThatAreEasyToGetWrong:
    def test_private_passenger_is_matched_on_the_text_not_the_code(self):
        """DASH prints "01 / Private Passenger Vehicle", "02 / ...", "03 / ..."
        for Pleasure, Commute and Business — every one a private car. Matching
        the NUMBER would silently drop two thirds of them."""
        for code in ("01", "02", "03"):
            markdown = (
                f"Policy #: X Effective Date: 2026-01-01\n"
                f"2024 HONDA - CIVIC 4DR - 1AAAA1A1AAA00000{code[-1]}\n"
                f"Type of Use / Vehicle Class: {code} / Private Passenger Vehicle\n"
            )
            assert dash_parse.parse_vehicles(markdown), f"class {code} was dropped"

    def test_a_commercial_vehicle_is_excluded(self):
        markdown = (
            "Policy #: X Effective Date: 2026-01-01\n"
            "2024 FORD - F650 - 1AAAA1A1AAA000009\n"
            "Type of Use / Vehicle Class: 06 / Commercial Vehicle\n"
        )
        assert dash_parse.parse_vehicles(markdown) == []

    def test_the_same_car_on_two_policies_appears_once(self):
        """A report lists the same physical car on a policy and its renewal."""
        markdown = (
            "Policy #: A Effective Date: 2026-01-01\n"
            "2024 HONDA - CIVIC 4DR - 1AAAA1A1AAA000001\n"
            "Type of Use / Vehicle Class: 02 / Private Passenger Vehicle\n"
            "2024 HONDA - CIVIC 4DR - 1AAAA1A1AAA000001\n"
            "Type of Use / Vehicle Class: 02 / Private Passenger Vehicle\n"
        )
        assert len(dash_parse.parse_vehicles(markdown)) == 1

    def test_a_year_of_birth_is_not_a_date_of_birth(self):
        """Every real report prints the year alone, and the column is a date.

        Filling "1997-01-01" would put a wrong value in a field the broker is
        REQUIRED to complete before Quote Done — and a wrong date that looks
        filled in is worse than a blank one they have to type.
        """
        assert "date_of_birth" not in dash_parse.parse_driver(
            "SAMPLE,TESTCASE A\nDate of Birth: 1997\n"
        )

    def test_a_date_that_is_only_date_SHAPED_is_refused(self):
        """The case that retired the regex.

        "1997-99-99" has four digits, a dash, two digits, a dash, two digits —
        so a pattern match accepts it and Postgres rejects it later, failing
        the whole update and taking the address and licence with it.
        date.fromisoformat asks the only question worth asking.
        """
        assert "date_of_birth" not in dash_parse.parse_driver(
            "SAMPLE,TESTCASE A\nDate of Birth: 1997-99-99\n"
        )

    def test_a_unit_number_is_kept_on_the_street(self):
        """DASH puts a unit in the middle comma segment, usually empty.

        It used to be discarded silently. driver.unit_number has no control on
        New Policy, so writing it there would store something invisible —
        appending keeps it in a field the broker can read and edit.
        """
        driver = dash_parse.parse_driver(
            "SAMPLE,TESTCASE A\n"
            "Address: 5 FESTIVAL DR, Unit 405, NORTH YORK ON M2R3T9\n"
        )
        assert driver["street_address"] == "5 FESTIVAL DR, Unit 405"
        assert driver["city"] == "NORTH YORK"

    def test_a_full_date_of_birth_is_taken(self):
        """Checked against the VALUE, not assumed from the format — a report
        that does print a whole date is worth having."""
        driver = dash_parse.parse_driver(
            "SAMPLE,TESTCASE A\nDate of Birth: 1997-04-12\n"
        )
        assert driver["date_of_birth"] == "1997-04-12"

    def test_not_available_is_never_filled(self):
        """DASH prints it where it HAS no data. Storing the literal string
        would put "Not available" in a broker's form as though it were a
        value."""
        markdown = "Gender: Not available\nMarital Status: Married\n"
        driver = dash_parse.parse_driver(markdown)
        assert "gender" not in driver
        assert driver["marital_status"] == "Married"

    def test_an_empty_report_yields_nothing_rather_than_raising(self):
        """A scanned or unreadable PDF returns little or no markdown. The
        broker should get an empty form to type into, not a 500."""
        assert dash_parse.parse_driver("") == {}
        assert dash_parse.parse_vehicles("") == []
        assert dash_parse.parse_operators("") == []


class TestOperators:
    """The other people on the most recent policy."""

    def test_the_other_operator_is_read(self, report):
        """Name, licence and province, from the real report."""
        operators = dash_parse.parse_operators(report)

        assert operators == [
            {
                "name": "OTHERPERSON,SECOND B",
                "license_number": "O9876-54321-09876",
                "license_province": "Ontario",
            }
        ]

    def test_the_subject_is_not_returned_as_an_additional_driver(self, report):
        """THE TRAP THIS GUARDS.

        The report's subject is listed as an operator on his OWN policy —
        SAMPLE,TESTCASE A appears alongside OTHERPERSON,SECOND B. Taking every
        operator would create a second copy of the applicant as an
        "additional driver", filled from his own report.
        """
        subject = dash_parse.parse_driver(report)["license_number"]
        numbers = [o["license_number"] for o in dash_parse.parse_operators(report)]

        assert subject not in numbers
        assert "SAMPLE,TESTCASE A" not in [o["name"] for o in
                                           dash_parse.parse_operators(report)]

    def test_the_name_is_read_past_an_interleaved_vehicle_role(self):
        """Azure reads DASH's two table columns in reading order, so a role
        lands between the name and the licence. Taking the rest of the line
        would make the name "FRANCIS,MILLAT F Vehicle #1: Principal Operator".
        """
        markdown = (
            # The subject's header, then the policies. Both halves are needed:
            # parse_operators excludes the subject by licence, and the subject
            # is read from everything above the first "## " — so a fragment
            # with no heading would make the OPERATOR look like the subject and
            # exclude him from his own result.
            "SUBJECT,PERSON A\nDLN: S1111-11111-11111 Ontario\n"
            "## Policies\n"
            "Policy #: A Effective Date: 2026-01-01\n"
            "Operator: OTHER,PERSON B Vehicle #1: Principal Operator\n"
            "DLN: O9876-54321-09876 Ontario\n"
        )
        assert dash_parse.parse_operators(markdown) == [
            {
                "name": "OTHER,PERSON B",
                "license_number": "O9876-54321-09876",
                "license_province": "Ontario",
            }
        ]

    def test_only_the_most_recent_policy(self):
        """A driver on a 2016 policy may have moved out. The current household
        is what the quote covers."""
        markdown = (
            "SUBJECT,PERSON A\nDLN: S1111-11111-11111 Ontario\n"
            "## Policies\n"
            "Policy #: A Effective Date: 2026-01-01\n"
            "Operator: CURRENT,PERSON A\nDLN: C1111-11111-11111 Ontario\n"
            "Policy #: B Effective Date: 2016-01-01\n"
            "Operator: FORMER,PERSON B\nDLN: F2222-22222-22222 Ontario\n"
        )
        assert [o["name"] for o in dash_parse.parse_operators(markdown)] == [
            "CURRENT,PERSON A"
        ]

    def test_an_operator_without_a_licence_is_still_returned(self):
        """A name the broker does not have to type is worth having.

        An earlier version dropped these, reasoning that a person with no
        licence could not be deduplicated across uploads. Nothing is
        deduplicated any more — the report is read and dropped — so the rule
        was protecting against a problem that no longer exists.
        """
        markdown = (
            "SUBJ,A\nDLN: S1111-11111-11111 Ontario\n"
            "## Policies\n"
            "Policy #: A Effective Date: 2026-01-01\n"
            "Operator: NAMEONLY,PERSON\n"
            "Relationship to Policyholder: NA\n"
        )
        assert dash_parse.parse_operators(markdown) == [{"name": "NAMEONLY,PERSON"}]

    def test_no_subject_licence_means_no_operators_at_all(self):
        """FAIL CLOSED, and this is the case that made it necessary.

        The subject is excluded by his licence number — so a report whose
        header licence cannot be read has nothing to exclude him BY, and he is
        listed as an operator on his own policy. Importing anyway would put the
        applicant on screen twice, from his own report.

        Returning nothing costs the broker some typing. The alternative costs
        them a duplicate they have to notice and delete, and the applicant's
        own fields and vehicles are filled either way.
        """
        markdown = (
            "SUBJECT,PERSON A\nGender: Male\n"
            "## Policies\n"
            "Policy #: A Effective Date: 2026-01-01\n"
            "Operator: SUBJECT,PERSON A\nDLN: S1111-11111-11111 Ontario\n"
            "Operator: OTHER,PERSON B\nDLN: O9876-54321-09876 Ontario\n"
        )
        assert dash_parse.parse_driver(markdown).get("license_number") is None
        assert dash_parse.parse_operators(markdown) == []

# ── The Policies summary ─────────────────────────────────────────────────


class TestPolicyDates:
    """The three dates a broker copies into a rating screen.

    DASH prints the policy history newest first, so all three are corner
    values of that one table — the newest row's end and start, and the oldest
    row's start.
    """

    def test_the_three_dates_from_the_real_report(self, report):
        """The fixture's Policies table:

            #1  2024-02-16 to 2026-02-16   Allstate   Active
            #2  2018-02-16 to 2019-02-17   AVIVA      Cancelled
            #3  2016-02-16 to 2018-02-16   AVIVA      Expired
        """
        assert dash_parse.parse_policy_dates(report) == {
            "renewal_date": "2026-02-16",
            "current_insurer_since": "2024-02-16",
            "insured_since": "2016-02-16",
        }

    def test_a_gap_is_not_interpreted(self, report):
        """DELIBERATE. The fixture has a five-year hole between #2 and #1,
        marked *POSSIBLE GAP*, and DASH's own header then says "Years of
        Continuous Insurance: 1" rather than the ten the oldest start implies.

        insured_since reports the date as printed. Whether the gap breaks
        continuity is a judgement the broker makes with the report open —
        encoding a guess here would put a number in a quote that nobody could
        trace back to anything on the page.
        """
        assert "*POSSIBLE GAP*" in report, "the fixture must still contain a gap"
        assert dash_parse.parse_policy_dates(report)["insured_since"] == "2016-02-16"

    def test_one_policy_answers_all_three(self):
        """A driver with a single policy has been insured since it started, so
        the one row is both the newest and the oldest."""
        markdown = (
            "# DRIVER REPORT\n"
            "\n## Policies\n"
            "<tr><td>#1</td><td>2024-02-16 to 2026-02-16</td></tr>\n"
            "\n## Claims\n"
        )
        assert dash_parse.parse_policy_dates(markdown) == {
            "renewal_date": "2026-02-16",
            "current_insurer_since": "2024-02-16",
            "insured_since": "2024-02-16",
        }

    def test_no_policies_section_is_not_a_failure(self):
        """A report without one must still parse — the driver's own details
        are the reason the broker uploaded it."""
        assert dash_parse.parse_policy_dates("# DRIVER REPORT\nGender: Male") == {}

    def test_an_empty_policies_section(self):
        markdown = "x\n## Policies\nNo policies found.\n## Claims\n"
        assert dash_parse.parse_policy_dates(markdown) == {}

    def test_a_malformed_date_drops_only_itself(self):
        """PARSED, NOT PATTERN-MATCHED. "2026-99-99" has the shape of a date,
        so a regex would pass it and Postgres would reject the whole UPDATE —
        taking the address and licence down with one bad policy date.
        """
        markdown = (
            "x\n## Policies\n"
            "<tr><td>2026-99-99 to 2026-02-16</td></tr>\n"
            "\n## Claims\n"
        )
        out = dash_parse.parse_policy_dates(markdown)

        assert out == {"renewal_date": "2026-02-16"}, (
            "the good half of the row must survive the bad half"
        )
