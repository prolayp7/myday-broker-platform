"""The DASH read flow: Claude's reading, the fallback, and the wrong-lead check.

Azure, Claude and Supabase are all stubbed — this is about the decisions the
flow makes, which are the ones the external review called out:

  - a fallback reading never deletes saved gaps or claims
  - a report that looks like someone else's saves nothing until confirmed
  - an incomplete Claude answer is a failure, not half a reading
"""

import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from services import dash, dash_apply, dash_claude


def _raw(**over):
    """A Claude reading of BABY RENNY's report, in SCHEMA shape."""
    raw = {
        "name": {"full_name": "BABY RENNY", "first_name": "RENNY", "middle_name": None,
                 "last_name": "BABY", "unsure": False, "note": None},
        "license": {"number": "B0023-64708-70101", "province": "Ontario"},
        "date_of_birth_printed": "1987",
        "address": {"printed": "50 Town Centre CRT SUITE 1910 1910 Scarborough, ON ON M1P0A9",
                    "street": "50 Town Centre CRT SUITE 1910", "city": "Scarborough",
                    "province": "ON", "postal_code": "M1P0A9", "unsure": False,
                    "note": "Removed repeated '1910' and 'ON'"},
        "gender": "Male",
        "marital_status": "Married",
        "at_fault_claims_6y": 0,
        "report_date": "2026-04-28",
        "newest_policy_end": "2026-06-29",
        "insured_since": "2019-06-29",
        "current_insurer_since": "2019-06-29",
        "policy_dates_unsure": False,
        "policy_dates_note": None,
        "possible_gap_marker": False,
        "applicant_terms": [],
        "additional_drivers": [],
        "vehicles": [{"year_make_model": "2022 AUDI - Q5 TECHNIK 45 2.0 TFSI 4DR AWD",
                      "vin": "WA1FAAFYXN2050081", "one_way_km": 0, "annual_km": 15000}],
        "claims": [{
            "date_of_loss": "2025-07-30", "at_fault_percent": 0, "whose": "applicant",
            "driver_license": "B0023-64708-70101", "driver_name": "BABY RENNY",
            "insurer": "Primmum Insurance Company", "claim_number": "43220448",
            "total_loss": "$26,057.00", "total_expense": "$423.00",
            "kol_lines": [
                {"code": "20", "loss": "$24,307.00", "expense": "$190.00"},
                {"code": "41", "loss": "$1,450.00", "expense": "$0.00"},
                {"code": "86", "loss": "$300.00", "expense": "$233.00"},
            ],
        }],
        "lead_match": {"result": "match", "reason": "same person"},
    }
    raw.update(over)
    return raw


class TestFromClaude:
    def test_driver_fields_and_python_maths(self):
        report = dash_apply.from_claude(_raw())
        d = report["driver"]
        assert (d["first_name"], d["last_name"]) == ("RENNY", "BABY")
        assert d["date_of_birth"] == "1987-01-01"  # completed from the licence
        assert d["city"] == "Scarborough"
        assert d["renewal_date"] == "2026-06-29"
        assert report["source"] == "claude"

    def test_claim_amounts_are_worked_out_in_python(self):
        claim = dash_apply.from_claude(_raw())["claims"][0]
        assert (claim["coll"], claim["ab"], claim["tp_bi"]) == ("24497.00", "1983.00", "0")
        assert claim["insurer"] == "Primmum Insurance Company"

    def test_claims_older_than_eight_years_are_kept_without_notes(self):
        old = dict(_raw()["claims"][0], date_of_loss="2017-01-01", total_loss="1.00")
        report = dash_apply.from_claude(_raw(claims=[old]))
        assert [c["date_of_loss"] for c in report["claims"]] == ["2017-01-01"]
        assert not [n for n in report["notes"] if "2017-01-01" in n]

    def test_missing_at_fault_stays_unknown(self):
        claim = dict(_raw()["claims"][0], at_fault_percent=None)
        assert dash_apply.from_claude(_raw(claims=[claim]))["claims"][0]["at_fault_percent"] is None

    def test_totals_mismatch_is_flagged(self):
        claim = dict(_raw()["claims"][0], total_loss="$99,999.00")
        notes = dash_apply.from_claude(_raw(claims=[claim]))["notes"]
        assert any("don't add up" in n for n in notes)

    def test_unknown_claim_takes_the_driver_of_a_same_date_claim(self):
        # Gohar Khadija's report: two claims on 2022-05-20, one names her.
        named = dict(_raw()["claims"][0], date_of_loss="2022-05-20", whose="applicant",
                     driver_license="G6161-43407-45406", driver_name="GOHAR, KHADIJA")
        nameless = dict(named, whose="unknown", driver_license="", driver_name="")
        other_day = dict(nameless, date_of_loss="2023-08-01")
        claims = dash_apply.from_claude(_raw(claims=[named, nameless, other_day]))["claims"]
        assert (claims[1]["whose"], claims[1]["claim_driver_name"]) == ("applicant", "GOHAR, KHADIJA")
        assert claims[2]["whose"] == "unknown", "a different date is left alone"

    def test_gender_backup_from_licence_only_when_dash_is_empty(self):
        raw = _raw(gender=None, license={"number": "S2068-40208-35221", "province": "Ontario"},
                   date_of_birth_printed="1983-02-21")
        assert dash_apply.from_claude(raw)["driver"]["gender"] == "Female"

    def test_no_gap_marker_means_no_gaps(self):
        terms = [{"start": "2020-01-01", "end": "2021-01-01", "status": "", "insurer": "", "vins": []},
                 {"start": "2022-01-01", "end": "2023-01-01", "status": "", "insurer": "", "vins": []}]
        assert dash_apply.from_claude(_raw(applicant_terms=terms))["gaps"] == []
        assert len(dash_apply.from_claude(_raw(applicant_terms=terms, possible_gap_marker=True))["gaps"]) == 1


# ── The flow in services/dash.py ───────────────────────────────────────────


@pytest.fixture
def flow(monkeypatch):
    """Stub the guard, Azure, storage and the write; record what happened."""
    seen = {"stored": 0, "applied": None, "replaced": None}
    state = {"driver": {"id": "d1", "lead_id": "l1", "license_number": None},
             "lead": {"name": "Renny"}, "claude": _raw()}

    monkeypatch.setattr(dash, "_driver_on_my_lead", lambda *_: (state["driver"], state["lead"]))
    monkeypatch.setattr(dash.dash_azure, "read_pdf", lambda _b: "# DASH report text")
    monkeypatch.setattr(dash, "_store_copy", lambda *_: seen.__setitem__("stored", seen["stored"] + 1))

    def read(_md, _lead_name):
        if isinstance(state["claude"], Exception):
            raise state["claude"]
        return state["claude"]

    monkeypatch.setattr(dash.dash_claude, "read_report", read)
    monkeypatch.setattr(dash_apply, "from_fallback", lambda _md: {
        "source": "fallback", "driver": {}, "vehicles": [], "operators": [],
        "gaps": None, "claims": None, "notes": [], "subject_name": None,
        "subject_license_number": "B0023-64708-70101", "lead_match": None,
    })

    def apply(driver_id, report):
        seen["applied"] = report
        if report.get("gaps") is not None and report.get("claims") is not None:
            seen["replaced"] = (report["gaps"], report["claims"])
        return {"source": report["source"]}

    monkeypatch.setattr(dash.dash_apply, "apply_report", apply)
    return seen, state


def test_claude_reading_is_applied_and_history_replaced(flow):
    seen, _ = flow
    assert dash.apply_pdf("d1", "b1", b"%PDF")["source"] == "claude"
    assert seen["stored"] == 1
    assert seen["replaced"] is not None


def test_fallback_never_replaces_history(flow):
    seen, state = flow
    state["claude"] = dash_claude.DashClaudeError("timeout")
    assert dash.apply_pdf("d1", "b1", b"%PDF")["source"] == "fallback"
    assert seen["replaced"] is None, "a fallback reading must leave saved gaps/claims alone"


def test_name_mismatch_saves_nothing_until_confirmed(flow):
    seen, state = flow
    state["lead"] = {"name": "Sajida"}
    state["claude"] = _raw(lead_match={"result": "mismatch", "reason": "different person"})

    with pytest.raises(HTTPException) as caught:
        dash.apply_pdf("d1", "b1", b"%PDF")
    assert caught.value.status_code == 409
    assert caught.value.detail["subject_name"] == "BABY RENNY"
    assert seen["stored"] == 0 and seen["applied"] is None

    dash.apply_pdf("d1", "b1", b"%PDF", confirm=True)
    assert seen["applied"] is not None and seen["stored"] == 1


def test_use_it_anyway_reuses_the_first_reading(flow, monkeypatch):
    seen, state = flow
    dash._PENDING.clear()
    state["claude"] = _raw(lead_match={"result": "mismatch", "reason": "different person"})
    with pytest.raises(HTTPException):
        dash.apply_pdf("d1", "b1", b"%PDF-same")

    reads = []
    monkeypatch.setattr(dash.dash_azure, "read_pdf", lambda _b: reads.append(1) or "# text")
    dash.apply_pdf("d1", "b1", b"%PDF-same", confirm=True)
    assert reads == [], "confirming the same report must not read it again"
    assert seen["applied"]["source"] == "claude"


def test_unsure_also_asks(flow):
    _, state = flow
    state["claude"] = _raw(lead_match={"result": "unsure", "reason": "first name only"})
    with pytest.raises(HTTPException):
        dash.apply_pdf("d1", "b1", b"%PDF")


def test_licence_mismatch_is_caught_even_by_the_fallback(flow):
    _, state = flow
    state["driver"]["license_number"] = "S2068-40208-35221"
    state["claude"] = dash_claude.DashClaudeError("down")
    with pytest.raises(HTTPException) as caught:
        dash.apply_pdf("d1", "b1", b"%PDF")
    assert caught.value.status_code == 409


def test_same_licence_with_different_dashes_is_a_match(flow):
    seen, state = flow
    state["driver"]["license_number"] = "b00236470870101"
    dash.apply_pdf("d1", "b1", b"%PDF")
    assert seen["applied"] is not None


# ── Validating Claude's answer ─────────────────────────────────────────────


def _response(stop_reason="end_turn", text=None):
    content = [SimpleNamespace(type="text", text=text)] if text is not None else []
    return SimpleNamespace(stop_reason=stop_reason, content=content, model="m", usage=None)


@pytest.fixture
def fake_client(monkeypatch):
    replies = []
    client = SimpleNamespace(beta=SimpleNamespace(messages=SimpleNamespace(
        create=lambda **_kw: replies.pop(0)
    )))
    monkeypatch.setattr(dash_claude, "_client", lambda: client)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test")
    return replies


def test_a_complete_answer_is_returned(fake_client):
    fake_client.append(_response(text=json.dumps(_raw())))
    assert dash_claude.read_report("md", "Renny")["license"]["number"] == "B0023-64708-70101"


def test_a_truncated_answer_is_retried_then_fails(fake_client):
    fake_client.extend([_response("max_tokens", "{"), _response("max_tokens", "{")])
    with pytest.raises(dash_claude.DashClaudeError):
        dash_claude.read_report("md", "Renny")


def test_one_retry_recovers(fake_client):
    fake_client.extend([_response("refusal"), _response(text=json.dumps(_raw()))])
    assert dash_claude.read_report("md", "Renny")["gender"] == "Male"


def test_missing_keys_fail(fake_client):
    fake_client.extend([_response(text='{"name": {}}'), _response(text='{"name": {}}')])
    with pytest.raises(dash_claude.DashClaudeError):
        dash_claude.read_report("md", "Renny")


# ── The route to Claude: local text first, Azure only when it is needed ──────


@pytest.fixture
def routes(flow, monkeypatch):
    """Records what reached Claude and how often Azure ran."""
    calls = {"azure": 0, "claude": [], "fallback": []}

    def azure(_bytes):
        calls["azure"] += 1
        return "azure markdown"

    def read(text, _name):
        calls["claude"].append(text)
        return _raw()

    def fallback(markdown):
        calls["fallback"].append(markdown)
        return {"source": "fallback", "driver": {}, "vehicles": [], "operators": [],
                "gaps": [], "claims": [], "notes": [], "subject_name": None,
                "subject_license_number": None, "lead_match": None}

    monkeypatch.setattr(dash.dash_azure, "read_pdf", azure)
    monkeypatch.setattr(dash.dash_claude, "read_report", read)
    monkeypatch.setattr(dash_apply, "from_fallback", fallback)
    return calls


def _claude_fails(_text, _name):
    raise dash.dash_claude.DashClaudeError("no")


def test_a_digital_report_never_touches_azure(routes, monkeypatch):
    monkeypatch.setattr(dash.dash_text, "read_pdf", lambda _b: "local text")
    dash.apply_pdf("d", "b", b"%PDF")
    assert routes["azure"] == 0 and routes["claude"] == ["local text"]


def test_a_scan_goes_to_azure(routes, monkeypatch):
    monkeypatch.setattr(dash.dash_text, "read_pdf", lambda _b: "")
    dash.apply_pdf("d", "b", b"%PDF")
    assert routes["azure"] == 1 and routes["claude"] == ["azure markdown"]


def test_the_fallback_gets_azure_markdown_not_local_text(routes, monkeypatch):
    monkeypatch.setattr(dash.dash_text, "read_pdf", lambda _b: "local text")
    monkeypatch.setattr(dash.dash_claude, "read_report", _claude_fails)
    dash.apply_pdf("d", "b", b"%PDF")
    assert routes["azure"] == 1 and routes["fallback"] == ["azure markdown"]


def test_a_scan_is_not_sent_to_azure_twice_on_a_claude_failure(routes, monkeypatch):
    monkeypatch.setattr(dash.dash_text, "read_pdf", lambda _b: "")
    monkeypatch.setattr(dash.dash_claude, "read_report", _claude_fails)
    dash.apply_pdf("d", "b", b"%PDF")
    assert routes["azure"] == 1 and routes["fallback"] == ["azure markdown"]


def test_something_pdfium_cannot_open_is_left_for_azure():
    from services import dash_text

    assert dash_text.read_pdf(b"not a pdf") == ""


@pytest.mark.parametrize("answer, expected", [
    ("false", False), ("False", False), ("true", True), ("True", True),
    (False, False), (True, True), (None, False),
])
def test_a_true_or_false_written_as_text_means_what_it_says(answer, expected):
    assert dash_claude._true_or_false(answer) is expected


def test_booleans_are_cleaned_everywhere_they_appear():
    raw = _raw()
    raw["name"]["unsure"] = "false"
    raw["address"]["unsure"] = "False"
    raw["policy_dates_unsure"] = "false"
    raw["possible_gap_marker"] = "false"
    dash_claude._clean_booleans(raw)
    assert (raw["name"]["unsure"], raw["address"]["unsure"],
            raw["policy_dates_unsure"], raw["possible_gap_marker"]) == (False, False, False, False)


def _pdf(page_texts):
    """A real PDF with these pages. An empty string is a page with no text.

    Written by hand so the tests need nothing outside requirements.txt.
    """
    objects = {1: b"<< /Type /Catalog /Pages 2 0 R >>",
               3: b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>"}
    kids = []
    for index, text in enumerate(page_texts):
        page_id, content_id = 4 + index * 2, 5 + index * 2
        lines = "".join(
            f"BT /F1 9 Tf 20 {780 - 12 * n} Td ({text[i:i + 90]}) Tj ET\n"
            for n, i in enumerate(range(0, len(text), 90))
        ).encode()
        objects[page_id] = (
            f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents {content_id} 0 R "
            "/Resources << /Font << /F1 3 0 R >> >> >>"
        ).encode()
        objects[content_id] = b"<< /Length %d >>\nstream\n%s\nendstream" % (len(lines), lines)
        kids.append(f"{page_id} 0 R")
    objects[2] = f"<< /Type /Pages /Kids [{' '.join(kids)}] /Count {len(kids)} >>".encode()

    pdf, offsets = bytearray(b"%PDF-1.4\n"), {}
    for number in sorted(objects):
        offsets[number] = len(pdf)
        pdf += b"%d 0 obj\n%s\nendobj\n" % (number, objects[number])
    table = len(pdf)
    pdf += b"xref\n0 %d\n0000000000 65535 f \n" % (len(objects) + 1)
    for number in sorted(objects):
        pdf += b"%010d 00000 n \n" % offsets[number]
    pdf += b"trailer\n<< /Size %d /Root 1 0 R >>\nstartxref\n%d\n%%%%EOF\n" % (len(objects) + 1, table)
    return bytes(pdf)


def test_digital_pages_are_read():
    from services import dash_text

    text = dash_text.read_pdf(_pdf(["Claim one " * 20, "Claim two " * 20]))
    assert "Claim one" in text and "Claim two" in text


def test_one_picture_page_sends_the_whole_report_to_azure():
    from services import dash_text

    assert dash_text.read_pdf(_pdf(["Claim one " * 20, "", "Claim two " * 20])) == ""


def test_reads_at_the_same_moment_do_not_mix_reports():
    from concurrent.futures import ThreadPoolExecutor

    from services import dash_text

    pdfs = [_pdf([f"Report {n} " * 30] * 3) for n in range(8)]
    wanted = [dash_text.read_pdf(pdf) for pdf in pdfs]
    with ThreadPoolExecutor(8) as pool:
        for _ in range(25):
            assert list(pool.map(dash_text.read_pdf, pdfs)) == wanted


class TestAddressCheck:
    def test_a_whole_line_in_the_street_adds_a_note_and_changes_nothing(self):
        line = "165 SPRING VIEW DRIVE BRAMPTON ON L6X4K5"
        raw = _raw()
        raw["address"] = {"printed": line, "street": line, "city": "BRAMPTON", "province": "ON",
                          "postal_code": "L6X4K5", "unsure": False, "note": ""}
        report = dash_apply.from_claude(raw)
        assert report["driver"]["street_address"] == line
        assert report["driver"]["city"] == "BRAMPTON"
        assert any(n.startswith("Address: the street still holds") for n in report["notes"])

    def test_a_clean_address_adds_no_address_note(self):
        raw = _raw()
        raw["address"]["note"] = ""
        assert not [n for n in dash_apply.from_claude(raw)["notes"] if n.startswith("Address")]
