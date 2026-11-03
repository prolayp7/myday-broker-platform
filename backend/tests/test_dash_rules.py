"""The exact calculations on a DASH report.

Every case here is either a real report from the sample set or one of the edge
cases the external review named (overlapping policies, Feb 29, the exact
12-month boundary, a missing at-fault figure).
"""

from decimal import Decimal

from services import dash_rules as r


class TestLicence:
    def test_year_only_is_completed_from_the_licence(self):
        assert r.date_of_birth("1987", "B0023-64708-70101", "Ontario") == "1987-01-01"
        assert r.date_of_birth("1978", "L5544-23277-80711", "Ontario") == "1978-07-11"

    def test_a_printed_full_date_wins(self):
        assert r.date_of_birth("1975-03-07", "P9212-27807-50307", "Ontario") == "1975-03-07"

    def test_female_month_has_fifty_added(self):
        born, gender = r.licence_facts("S2068-40208-35221", "Ontario", "1983")
        assert born.isoformat() == "1983-02-21"
        assert gender == "Female"

    def test_male(self):
        assert r.licence_facts("L5544-23277-80711", "Ontario", "1978")[1] == "Male"

    def test_year_mismatch_trusts_nothing(self):
        assert r.licence_facts("L5544-23277-80711", "Ontario", "1985") == (None, None)
        assert r.date_of_birth("1985", "L5544-23277-80711", "Ontario") is None

    def test_not_ontario_trusts_nothing(self):
        assert r.licence_facts("L5544-23277-80711", "Alberta", "1978") == (None, None)

    def test_malformed_licence(self):
        assert r.licence_facts("D440330018309", "Ontario", "1978") == (None, None)

    def test_normalize(self):
        assert r.normalize_dln(" b0023-64708 70101 ") == "B00236470870101"


class TestRenewal:
    def test_pre_renewed_goes_back_a_year(self):
        assert r.renewal_date("2026-05-12", "2027-05-31") == "2026-05-31"

    def test_normal(self):
        assert r.renewal_date("2026-04-28", "2026-06-29") == "2026-06-29"

    def test_exactly_twelve_months_is_kept(self):
        assert r.renewal_date("2026-05-12", "2027-05-12") == "2027-05-12"

    def test_already_ended_is_blank(self):
        assert r.renewal_date("2026-05-10", "2026-02-16") is None

    def test_feb_29(self):
        assert r.renewal_date("2027-01-10", "2028-02-29") == "2027-02-28"
        assert r.add_years(r._as_date("2028-02-29"), -1).isoformat() == "2027-02-28"


def _t(start, end, status="", vins=()):
    return {"start": start, "end": end, "status": status, "vins": list(vins)}


class TestGaps:
    def test_joy_jijo_two_non_payment_gaps(self):
        terms = [
            _t("2021-09-28", "2023-07-18", "Cancelled - non-payment", ["V1"]),
            _t("2023-09-07", "2025-02-04", "Cancelled - non-payment", ["V1"]),
            _t("2025-02-10", "2025-04-30", "Cancelled - other reason", ["V1"]),
            _t("2025-04-30", "2026-10-31", "Active", ["V1"]),
        ]
        gaps = r.find_gaps(terms)
        assert [(g["from"], g["to"], g["days"], g["reason"]) for g in gaps] == [
            ("2023-07-18", "2023-09-07", 51, "Non-payment"),
            ("2025-02-04", "2025-02-10", 6, "Non-payment"),
        ]

    def test_one_day_is_a_gap(self):
        terms = [_t("2023-01-21", "2025-01-21"), _t("2025-01-22", "2027-01-22")]
        assert r.find_gaps(terms)[0]["days"] == 1

    def test_same_day_is_continuous(self):
        terms = [_t("2022-10-18", "2023-10-18"), _t("2023-10-18", "2024-10-18")]
        assert r.find_gaps(terms) == []

    def test_overlap_does_not_fake_a_gap(self):
        terms = [
            _t("2024-01-01", "2024-12-31"),
            _t("2024-03-01", "2024-04-30"),
            _t("2024-06-01", "2024-12-31"),
        ]
        assert r.find_gaps(terms) == []

    def test_new_vehicle_after_gap(self):
        terms = [_t("2020-01-01", "2021-01-01", vins=["A"]), _t("2021-03-01", "2022-03-01", vins=["B"])]
        assert r.find_gaps(terms)[0]["reason"] == "Possible new vehicle"

    def test_same_vehicle_is_other(self):
        terms = [_t("2020-01-01", "2021-01-01", vins=["A"]), _t("2021-03-01", "2022-03-01", vins=["a"])]
        assert r.find_gaps(terms)[0]["reason"] == "Other"

    def test_older_than_six_years_is_kept(self):
        terms = [_t("2014-01-01", "2016-04-30"), _t("2016-05-01", "2026-05-01")]
        assert r.find_gaps(terms)[0]["from"] == "2016-04-30"


class TestClaims:
    def test_baby_renny_claim_one(self):
        lines = [
            {"code": "20", "loss": "24,307.00", "expense": "190.00"},
            {"code": "41", "loss": "1,450.00", "expense": "0"},
            {"code": "80", "loss": "0", "expense": "0"},
            {"code": "86", "loss": "300.00", "expense": "233.00"},
            {"code": "81", "loss": "0", "expense": "0"},
        ]
        totals, unknown = r.claim_amounts(lines)
        assert totals == {
            "tp_bi": Decimal("0"), "tp_pd": Decimal("0"),
            "ab": Decimal("1983.00"), "coll": Decimal("24497.00"),
        }
        assert unknown == []
        assert r.claim_totals_match(lines, "$26,057.00", "$423.00")

    def test_dcpd_goes_to_tp_pd_and_kol_prefix_is_accepted(self):
        totals, _ = r.claim_amounts([{"code": "KOL16", "loss": "23236", "expense": "0"}])
        assert totals["tp_pd"] == Decimal("23236")

    def test_unknown_code_is_kept(self):
        _, unknown = r.claim_amounts([{"code": "99", "loss": "10", "expense": "0"}])
        assert unknown == ["99"]

    def test_totals_mismatch_is_caught(self):
        assert not r.claim_totals_match([{"code": "20", "loss": "100", "expense": "0"}], "200", "0")

    def test_every_code_has_one_box(self):
        assert len(r.KOL_BOX) == sum(len(c) for c in r._BOX_CODES.values())


class TestAddressProblems:
    def test_a_whole_line_in_the_street_is_flagged(self):
        problems = r.address_problems("165 SPRING VIEW DRIVE BRAMPTON ON L6X4K5", "BRAMPTON", "ON", "L6X4K5")
        assert problems == ["the street still holds the town, province or postal code"]

    def test_flagged_even_when_the_postal_field_is_blank(self):
        assert r.address_problems("165 SPRING VIEW DRIVE BRAMPTON ON L6X4K5", "BRAMPTON", "ON", None)

    def test_a_spaced_postal_code_in_the_street_is_flagged(self):
        assert r.address_problems("165 SPRING VIEW DRIVE BRAMPTON ON L6X 4K5", "BRAMPTON", "ON", "L6X 4K5")

    def test_the_town_alone_at_the_end_of_the_street_is_flagged(self):
        assert r.address_problems("235 Clarke St Oshawa", "Oshawa", "ON", "L1H5G2")

    def test_city_on_is_a_province_code(self):
        problems = r.address_problems("235 Clarke St Oshawa", "ON", "ON", "L1H5G2")
        assert 'the city is "ON", a province code' in problems

    def test_a_number_in_the_city(self):
        assert r.address_problems("2583 KING ST E", "406 HAMILTON", "ON", "L8K1Y4") == [
            'the city "406 HAMILTON" has a number in it'
        ]

    def test_a_province_that_is_not_a_code(self):
        assert r.address_problems("9 MILLINGTON COVE", "AJAX", "OT", None) == [
            'the province "OT" is not a 2-letter province code'
        ]
        assert r.address_problems("9 MILLINGTON COVE", "AJAX", "Ontario", None) == [
            'the province "Ontario" is not a 2-letter province code'
        ]

    def test_a_correct_address_raises_nothing(self):
        assert r.address_problems("165 SPRING VIEW DRIVE", "BRAMPTON", "ON", "L6X4K5") == []
        assert r.address_problems("50 Town Centre CRT SUITE 1910", "Scarborough", "on", "M1P 0A9") == []
        assert r.address_problems(None, None, None, None) == []

    def test_the_town_inside_a_word_or_mid_street_is_not_flagged(self):
        assert r.address_problems("12 Hamiltonian Way", "Hamilton", "ON", None) == []
        assert r.address_problems("10 Brampton St", "Brampton", "ON", None) == []
