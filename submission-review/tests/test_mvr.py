"""The self-check, from made-up text. No Azure, no Claude, no database."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import mvr

TEXT = """ONTARIO Driving Record
***Number of Convictions: 2 ***
Licence Number: S1234-56789-10719 Expiry Date: 19/07/2028
Name: DOE,JANE Birth Date: 19/07/1991
DATE CONVICTIONS, DISCHARGES AND OTHER ACTIONS
16/05/2025
SPEEDING
OFFENCE DATE 2024/02/06
05/06/2025 REINST - SUSP. EXPIRED OR RESCINDED
SUSPENSION NO. 5059286
02/06/2025 SUSPENDED UNTIL JUNE 5, 2025,
SUSPENSION NO. 5059286
"""


def reading(**changes):
    base = {
        "licence_no": "S1234-56789-10719", "name": "DOE,JANE",
        "birth_date": "19/07/1991", "gender": "F", "street": "1 MAIN ST",
        "city": "TORONTO", "province": "ON", "postal_code": "M1P 0A9",
        "printed_convictions": 2,
        "entries": [
            {"date": "16/05/2025", "entry_type": "conviction", "description": "SPEEDING",
             "offence_date": "2024/02/06", "suspension_no": ""},
            {"date": "05/06/2025", "entry_type": "reinstatement",
             "description": "REINST - SUSP. EXPIRED OR RESCINDED",
             "offence_date": "", "suspension_no": "5059286"},
            {"date": "02/06/2025", "entry_type": "suspension",
             "description": "SUSPENDED UNTIL JUNE 5, 2025,",
             "offence_date": "", "suspension_no": "5059286"},
        ],
    }
    return {**base, **changes}


def test_a_correct_reading_passes():
    assert mvr.self_check(reading(), TEXT) == []


def test_a_reinstatement_is_listed_but_not_counted():
    assert mvr.self_check(reading(printed_convictions=3), TEXT.replace(
        "Convictions: 2", "Convictions: 3")) != []


def test_a_dropped_entry_fails():
    parsed = reading()
    parsed["entries"] = parsed["entries"][1:]
    assert any("printed count" in p for p in mvr.self_check(parsed, TEXT))


def test_a_date_not_on_the_document_fails():
    parsed = reading()
    parsed["entries"][0]["date"] = "17/05/2025"
    assert any("entry 1 date" in p for p in mvr.self_check(parsed, TEXT))


def test_a_reformatted_date_fails():
    parsed = reading()
    parsed["entries"][0]["date"] = "2025-05-16"
    assert any("entry 1 date" in p for p in mvr.self_check(parsed, TEXT))


def test_a_birth_date_that_disagrees_with_the_licence_fails():
    text = TEXT.replace("19/07/1991", "18/07/1991")
    assert any("licence number" in p
               for p in mvr.self_check(reading(birth_date="18/07/1991"), text))


def test_a_womans_licence_month_has_50_added():
    text = TEXT.replace("10719", "15719")
    assert not any("agree" in p for p in mvr.self_check(
        reading(licence_no="S1234-56789-15719"), text))


def test_a_licence_not_on_the_document_fails():
    assert any("not in the document" in p for p in mvr.self_check(
        reading(licence_no="S1234-56789-10720"), TEXT))


def test_two_abstracts_in_one_pdf_fail():
    assert any("one abstract" in p
               for p in mvr.self_check(reading(), TEXT + "ONTARIO Driving Record"))


def test_dates_read_day_first():
    report, entries = mvr.to_rows(reading())
    assert report["dob"] == "1991-07-19"
    assert report["licence_no"] == "S12345678910719"
    assert report["postal_code"] == "M1P0A9"
    assert entries[0]["entry_date"] == "2025-05-16"
    assert entries[0]["offence_date"] == "2024-02-06"
    assert entries[1]["offence_date"] is None
