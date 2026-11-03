"""The DASH self-check, from made-up text. No Claude, no database."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import dash

TEXT = """JANE DOE
DLN: D1234‑56789‑10719 Ontario
Report Date: 2026-10-01
Claim #1 Date of Loss 2023-08-10 At-Fault : 0%
Claim Number 991 Total Loss $1,000.00 Total Expense $50.00
"""


def reading(**changes):
    base = {
        "name": "JANE DOE", "licence_no": "D1234-56789-10719", "date_of_birth": "1991",
        "gender": "", "marital_status": "", "report_date": "2026-10-01", "claims_6y": 1,
        "claims": [{
            "date_of_loss": "2023-08-10", "at_fault_percent": 0, "whose": "report_driver",
            "driver_licence": "D1234-56789-10719", "driver_name": "JANE DOE",
            "insurer": "Aviva", "claim_number": "991", "total_loss": "$1,000.00",
            "total_expense": "$50.00",
            "kol_lines": [{"code": "20", "loss": "$1,000.00", "expense": "$50.00"}],
        }],
    }
    base.update(changes)
    return base


def test_good_reading_passes_with_an_odd_hyphen():
    assert dash.self_check(reading(), TEXT) == ([], [])


def test_wrong_licence_fails():
    problems, _ = dash.self_check(reading(licence_no="D1234-56789-10718"), TEXT)
    assert problems == ["licence number is not in the document"]


def test_amounts_must_add_up():
    claim = reading()["claims"][0]
    claim["kol_lines"][0]["loss"] = "$900.00"
    problems, _ = dash.self_check(reading(claims=[claim]), TEXT)
    assert problems == ["claim 1 amounts do not add up to its totals"]


def test_printed_count_is_only_a_warning():
    problems, warnings = dash.self_check(reading(claims_6y=2), TEXT)
    assert problems == [] and len(warnings) == 1


def test_rows_add_up_the_boxes():
    report, claims = dash.to_rows(reading())
    assert report["licence_no"] == "D12345678910719" and report["dob"] is None
    assert claims[0]["coll"] == 1050.0


def test_licence_drops_the_province_printed_after_it():
    assert dash.licence("R1234-56789-01234 Ontario") == "R12345678901234"
    assert dash.to_rows({
        "name": "A", "licence_no": "R1234-56789-01234 Ontario", "report_date": "2026-01-01",
        "claims": [],
    })[0]["licence_no"] == "R12345678901234"


def test_licence_keeps_a_number_from_elsewhere():
    assert dash.licence("AB-123") == "AB123"
