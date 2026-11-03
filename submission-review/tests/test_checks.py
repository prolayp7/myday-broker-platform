"""Each rule with no live services: a clean file passes, one change fails."""

import copy
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from checks import ERROR, WARNING, run_checks  # noqa: E402

LICENCE = "D12345678950312"


def clean_file() -> dict:
    return copy.deepcopy({
        "application": {
            "applicant_name": "Jane Doe & JOHN DOE", "street": "1 MAIN ST",
            "city": "Toronto", "province": "Ontario", "postal_code": "M1P0A9",
            "effective_date": "2026-09-23", "effective_time": "00:01",
            "expiry_date": "2027-09-23", "expiry_time": "00:01",
            "registered_owner": "yes", "household_autos": 1,
            "prior_insurer": "Pembridge", "prior_policy_no": "100000001",
            "prior_expiry": "2026-09-23", "q_suspended_6y": "no", "q_cancelled_3y": "no",
            "q_misrep_3y": "no", "q_fraud": "no", "total_premium": 1000,
        },
        "drivers": [{
            "driver_no": 1, "name": "Doe, Jane", "licence_no": LICENCE,
            "dob": "1985-03-12", "gender": "M", "marital": "Mar",
            "first_lic_class": "G", "first_lic_year": 2016,
        }],
        "vehicles": [{
            "auto_no": 1, "model_year": 2023, "make_model": "KIA", "vin": "1HGBH41JXMN109186",
            "owned": "yes", "leased": "blank", "annual_km": 5000, "use_pleasure": "yes",
            "business_pct": 0, "use_farm": "blank", "use_commercial": "blank",
            "car_pool": "no", "fuel": "Gasoline", "unrepaired_damage": "no", "modified": "no",
        }],
        "driver_use": [{"driver_no": 1, "auto_no": 1, "pct": 100}],
        "claims": [],
        "convictions": [],
        "coverages": [{"auto_no": 1, "code": "20_loss_of_use", "limit_amount": 2000}],
        "mvrs": {LICENCE: {"document_id": "m", "report": {
            "name": "DOE,JANE", "street": "1 MAIN ST", "city": "TORONTO",
            "province": "ON", "postal_code": "M1P0A9", "dob": "1985-03-12",
        }, "entries": []}},
        "dashes": {LICENCE: {"document_id": "d", "report": {
            "gender": "Male", "marital": "Married",
        }, "claims": []}},
    })


def rules(file: dict) -> list[str]:
    return [f["rule_no"] for f in run_checks(file).findings]


def claim(day: str, number: str = "1", **extra) -> dict:
    return {"date_of_loss": day, "insurer": "Aviva", "claim_number": number,
            "driver_licence": LICENCE, **extra}


def test_clean_file_has_no_findings():
    assert rules(clean_file()) == []


def test_policy_term():
    file = clean_file()
    file["application"]["expiry_date"] = "2027-09-22"
    assert rules(file) == ["1"]


def test_times():
    file = clean_file()
    file["application"]["effective_time"] = "00:00"
    assert rules(file) == ["2"]


def test_business_use():
    file = clean_file()
    file["vehicles"][0]["business_pct"] = 31
    assert rules(file) == ["3"]


def test_vehicle_ticks():
    for key, value, rule in (
        ("car_pool", "yes", "4"), ("fuel", "Diesel", "5"),
        ("unrepaired_damage", "yes", "6"), ("modified", "yes", "7"),
    ):
        file = clean_file()
        file["vehicles"][0][key] = value
        assert rules(file) == [rule]


def test_blank_goes_to_filled_not_passed():
    file = clean_file()
    file["vehicles"][0]["car_pool"] = "blank"
    assert rules(file) == ["filled"]


def test_unclear_goes_to_a_person():
    file = clean_file()
    file["vehicles"][0]["modified"] = "unclear"
    result = run_checks(file)
    assert result.unclear == ["vehicle.1.modified"]


def test_total_automobiles_is_an_error():
    file = clean_file()
    file["application"]["household_autos"] = 0
    result = run_checks(file)
    assert [(f["rule_no"], f["severity"]) for f in result.findings] == [("9", ERROR)]


def test_questions():
    file = clean_file()
    file["application"]["q_fraud"] = "yes"
    assert "11" in rules(file)


def test_loss_of_use_needs_an_amount():
    file = clean_file()
    file["coverages"][0]["limit_amount"] = None
    assert rules(file) == ["12"]


def test_mortgage_needs_lienholder():
    file = clean_file()
    file["coverages"].append({"auto_no": 1, "code": "23a_mortgage", "premium": 0})
    assert rules(file) == ["13"]
    file["vehicles"][0].update(lienholder_name="Bank", lienholder_address="1 Main")
    assert rules(file) == []


def test_address_against_mvr():
    file = clean_file()
    file["application"]["postal_code"] = "M1P0A8"
    assert rules(file) == ["14"]


def test_joint_applicant_passes():
    assert "14" not in rules(clean_file())


def test_driver_against_mvr():
    file = clean_file()
    file["drivers"][0]["dob"] = "1988-05-19"
    assert rules(file) == ["15"]


def test_marital_words_are_mapped():
    file = clean_file()
    file["dashes"][LICENCE]["report"]["marital"] = "Single"
    assert rules(file) == ["16"]


def test_not_available_is_skipped_and_noted():
    file = clean_file()
    file["dashes"][LICENCE]["report"]["gender"] = "Not available"
    result = run_checks(file)
    assert not result.findings and len(result.notes) == 1


def test_missing_claim_is_an_error():
    file = clean_file()
    file["dashes"][LICENCE]["claims"] = [claim("2023-08-10")]
    assert [(f["rule_no"], f["severity"]) for f in run_checks(file).findings] == [("17", ERROR)]


def test_claim_anniversary_is_inside_and_a_day_before_is_not():
    file = clean_file()
    file["dashes"][LICENCE]["claims"] = [claim("2020-09-23")]
    assert rules(file) == ["17"]
    file["dashes"][LICENCE]["claims"] = [claim("2020-09-22")]
    assert rules(file) == []


def test_two_claims_on_one_date_need_two_rows():
    file = clean_file()
    file["dashes"][LICENCE]["claims"] = [claim("2023-08-10", "1"), claim("2023-08-10", "2")]
    file["claims"] = [{"driver_no": 1, "loss_date": "2023-08-10"}]
    assert rules(file) == ["17"]


def test_same_claim_on_two_reports_counts_once():
    file = clean_file()
    file["dashes"]["SPOUSE"] = {"document_id": "e", "report": {}, "claims": [claim("2023-08-10")]}
    file["dashes"][LICENCE]["claims"] = [claim("2023-08-10")]
    file["claims"] = [{"driver_no": 1, "loss_date": "2023-08-10"}]
    assert rules(file) == []


def test_no_claim_number_on_two_reports_goes_to_a_person():
    file = clean_file()
    file["dashes"]["SPOUSE"] = {"document_id": "e", "report": {}, "claims": [claim("2023-08-10", "")]}
    file["dashes"][LICENCE]["claims"] = [claim("2023-08-10", "")]
    assert run_checks(file).unclear


def test_claim_under_another_driver_is_a_warning():
    file = clean_file()
    file["dashes"][LICENCE]["claims"] = [claim("2023-08-10")]
    file["claims"] = [{"driver_no": 2, "loss_date": "2023-08-10"}]
    assert [(f["rule_no"], f["severity"]) for f in run_checks(file).findings] == [("17", WARNING)]


def test_conviction_listed_and_missing():
    file = clean_file()
    file["mvrs"][LICENCE]["entries"] = [
        {"entry_type": "conviction", "entry_date": "2024-05-06"},
        {"entry_type": "reinstatement", "entry_date": "2024-06-06"},
    ]
    assert rules(file) == ["18"]
    file["convictions"] = [{"driver_no": 1, "convicted_date": "2024-05-06"}]
    assert rules(file) == []


def test_old_conviction_is_outside_the_limit():
    file = clean_file()
    file["mvrs"][LICENCE]["entries"] = [{"entry_type": "conviction", "entry_date": "2023-09-22"}]
    assert rules(file) == []
