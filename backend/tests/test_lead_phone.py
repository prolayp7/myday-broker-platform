"""A lead's phone numbers, merged and deduplicated by the backend.

WHY THE BACKEND MERGES. A customer's numbers live in three tables — the lead,
its drivers, lead_phone — and no screen holds all three: ContactHistoryPage
receives only the lead's, mobile only has the lead's, neither has the drivers'.
A frontend asked to combine them could not, and two frontends each combining
separately would eventually disagree about what numbers a customer has.

THE DEDUPLICATION IS NOT COSMETIC. driver.phone is SEEDED from lead.phone when
the applicant row is created (migrations/0058), so the same number in two rows
is the normal case on almost every lead — and a delete control beside one of
two identical lines reads as a bug.
"""

import os
import uuid
from pathlib import Path

import pytest
from dotenv import load_dotenv
from fastapi import HTTPException

pytestmark = pytest.mark.dbtest

load_dotenv(Path(__file__).resolve().parent.parent / ".env")

if not os.environ.get("SUPABASE_URL") or not os.environ.get("SUPABASE_SERVICE_ROLE_KEY"):
    pytest.skip(
        "needs SUPABASE_URL + SUPABASE_SERVICE_ROLE_KEY — these tests run "
        "against a real database",
        allow_module_level=True,
    )

from db import supabase  # noqa: E402
from services import lead_phone as service  # noqa: E402
from tests.test_lead_assignment import Sandbox  # noqa: E402
from tests.test_lead_assignment import sandbox  # noqa: E402,F401

SECOND_NUMBER = "+16475550123"


class Scene:
    def __init__(self, box: Sandbox):
        self.box = box
        self.group = box.group()
        self.broker = box.broker(self.group["id"], "Owner", daily_cap=10)
        self.lead = box.lead(self.group["id"], "Customer")
        assert box.assign(self.lead["id"]) == self.broker["id"]

    def add_secondary(self, phone_e164=SECOND_NUMBER):
        return supabase.table("lead_phone").insert({
            "lead_id": self.lead["id"],
            "phone_e164": phone_e164,
            "source": "added",
            "added_by": self.broker["id"],
        }).execute().data[0]

    def cleanup(self):
        for lead_id in self.box.lead_ids:
            supabase.table("lead_phone").delete().eq("lead_id", lead_id).execute()


@pytest.fixture
def scene(sandbox):
    s = Scene(sandbox)
    try:
        yield s
    finally:
        s.cleanup()


class TestTheUnifiedList:
    def test_the_leads_own_number_appears_once_not_twice(self, scene):
        """THE CASE THAT IS ALWAYS TRUE. sandbox.applicant() creates a driver
        carrying the same number as the lead, exactly as the real applicant
        flow does — so without deduplication every lead shows its number
        twice."""
        scene.box.applicant(scene.lead["id"])

        phones = service.list_for_lead(scene.lead["id"])

        primaries = [p for p in phones if p["phone_e164"] == "+15550001111"]
        assert len(primaries) == 1, f"the primary number appeared {len(primaries)} times"
        assert primaries[0]["source"] == "lead", (
            "the lead's own number was attributed to the driver instead"
        )
        assert primaries[0]["deletable"] is False

    def test_a_secondary_number_is_listed_and_deletable(self, scene):
        scene.add_secondary()

        phones = service.list_for_lead(scene.lead["id"])
        secondary = [p for p in phones if p["phone_e164"] == SECOND_NUMBER]

        assert len(secondary) == 1
        assert secondary[0]["source"] == "secondary"
        assert secondary[0]["deletable"] is True
        assert secondary[0]["phone_id"]

    def test_a_number_in_TWO_tables_appears_once_and_is_NOT_deletable(self, scene):
        """PRECEDENCE: lead -> driver -> secondary.

        Deleting the lead_phone row for a number the lead itself holds would
        not stop it matching — the lead still has it. Offering the control
        would promise something it cannot do.
        """
        scene.add_secondary(phone_e164="+15550001111")  # the lead's own number

        phones = service.list_for_lead(scene.lead["id"])
        rows = [p for p in phones if p["phone_e164"] == "+15550001111"]

        assert len(rows) == 1, "the same number was listed twice"
        assert rows[0]["source"] == "lead"
        assert rows[0]["deletable"] is False, (
            "offered to delete a number that would keep matching anyway"
        )

    def test_a_missing_lead_is_a_404(self, scene):
        with pytest.raises(HTTPException) as err:
            service.list_for_lead(str(uuid.uuid4()))
        assert err.value.status_code == 404


class TestDeletion:
    def test_deleting_a_secondary_number_removes_it(self, scene):
        row = scene.add_secondary()

        service.delete(scene.lead["id"], row["id"])

        assert SECOND_NUMBER not in [
            p["phone_e164"] for p in service.list_for_lead(scene.lead["id"])
        ]

    def test_a_phone_id_from_ANOTHER_lead_is_refused(self, scene):
        """SCOPED BY BOTH IDS. The phone id alone finds the row, so filtering
        on it only would let any lead's URL delete any other lead's number —
        the lead in the path would be decoration rather than a check."""
        other = scene.box.lead(scene.group["id"], "Someone else")
        theirs = supabase.table("lead_phone").insert({
            "lead_id": other["id"],
            "phone_e164": "+16475550777",
            "source": "added",
            "added_by": scene.broker["id"],
        }).execute().data[0]

        with pytest.raises(HTTPException) as err:
            service.delete(scene.lead["id"], theirs["id"])
        assert err.value.status_code == 404

        still_there = supabase.table("lead_phone").select("id").eq(
            "id", theirs["id"]).execute().data
        assert still_there, "deleted another lead's number through the wrong URL"
