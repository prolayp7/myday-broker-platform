"""Tests for reading the register line out of an MVR.

The fixture copies the SHAPE of Azure's markdown for a real CGI RapidWeb
printout (read 2026-10-20) with every value invented: labels in one cell and
values in the next, a space before some colons and not others, and the receipt
number printed both in the summary table and at the end of the abstract.
"""

from datetime import date
from pathlib import Path

from services import mvr_parse

SAMPLE = (Path(__file__).parent / "fixtures" / "mvr_sample.md").read_text(encoding="utf-8")


def test_reads_the_four_register_facts():
    assert mvr_parse.parse(SAMPLE) == [{
        "licence_number": "T12345678901234",
        "driver_name": "TESTER, PAT Q",
        "requested_on": date(2026, 10, 8),
        "aris_order_number": "600000111",
    }]


def test_dates_are_day_first():
    """08/10/2026 is the 8th of October. Month-first would file it in August."""
    [line] = mvr_parse.parse(SAMPLE)
    assert line["requested_on"].month == 10


def test_trailing_comma_on_a_single_name_is_dropped():
    text = SAMPLE.replace("TESTER, PAT Q", "JOHN SMITH,")
    assert mvr_parse.parse(text)[0]["driver_name"] == "JOHN SMITH"


def test_two_abstracts_give_two_lines():
    second = SAMPLE[SAMPLE.index("# ONTARIO Driving Record"):]
    second = (
        second.replace("T1234-56789-01234", "U9876-54321-09876")
        .replace("TESTER, PAT Q", "TESTER, SAM")
        .replace("600000111", "600000222")
    )
    lines = mvr_parse.parse(SAMPLE + "\n\n" + second)
    assert [l["aris_order_number"] for l in lines] == ["600000111", "600000222"]
    assert [l["licence_number"] for l in lines] == ["T12345678901234", "U98765432109876"]


def test_receipt_falls_back_to_the_summary_table():
    """If the abstract's own trailing receipt is missing, the summary row for
    the same licence still identifies it."""
    head, tail = SAMPLE.rsplit("Receipt Number : 600000111", 1)
    assert mvr_parse.parse(head + tail)[0]["aris_order_number"] == "600000111"


def test_missing_requested_on_is_none_not_a_guess():
    text = SAMPLE.replace("Requested On :", "Asked On :")
    assert mvr_parse.parse(text)[0]["requested_on"] is None


def test_a_document_that_is_not_an_mvr_gives_nothing():
    assert mvr_parse.parse("# DASH Report\n\nDLN: X1234 Ontario\n") == []
    assert mvr_parse.parse("") == []


def test_an_abstract_with_no_licence_is_skipped():
    text = SAMPLE.replace("Licence Number :", "Licence :")
    assert mvr_parse.parse(text) == []
