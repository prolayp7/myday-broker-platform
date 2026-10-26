"""Staging smoke test for broker self-pause, over real HTTP.

The scenario end to end through the API rather than the service layer — a real
login, a real bearer token, the real route. The unit and integration suites
call Python functions; this is the only check that the whole chain is actually
wired together, and it is what caught a stale server serving pre-feature code.

  1. pause broker A
  2. a lead arrives; confirm broker B receives it
  3. re-read A's profile; confirm the switch is STILL off after a refresh
  4. resume A with a lead already waiting; confirm it is assigned without
     creating another one
  5. confirm A cannot pause B by naming them in the body

Builds its own disposable community, group, brokers and leads, and removes
them in a finally that runs even when an assertion fails. Verified afterwards
to leave nothing behind.

RUN IT:

    cd backend
    ./venv/Scripts/python.exe -m uvicorn main:app --port 8055     # in one shell
    SMOKE_BASE_URL=http://127.0.0.1:8055 \
      PYTHONPATH=. ./venv/Scripts/python.exe scripts/smoke_self_pause.py

PYTHONPATH is needed because this sits in scripts/ and imports db/security from
the parent. Point SMOKE_BASE_URL at whichever host you want to exercise —
it defaults to the local dev port.

CHECK THE SERVER IS THE ONE YOU THINK. A backend started before this feature
existed will 404 on /my-availability and every check below step 1 will fail
for that reason alone. The script says so explicitly rather than letting the
failures look like logic errors.
"""

import os
import sys
import uuid

import httpx
import pyotp
from dotenv import load_dotenv

load_dotenv()

from db import supabase  # noqa: E402
from security import hash_password  # noqa: E402

BASE = os.environ.get("SMOKE_BASE_URL", "http://127.0.0.1:8055")
PREFIX = "ZZSMOKE"
PASSWORD = "SmokeTest!2026x"

created = {"community": [], "group": [], "broker": [], "lead": []}
ok = True


def check(label, condition, detail=""):
    global ok
    print(f"  {'PASS' if condition else 'FAIL'}  {label}{(' — ' + detail) if detail else ''}")
    if not condition:
        ok = False


def login(email):
    """Full two-step login, including first-time TOTP enrolment."""
    r = httpx.post(f"{BASE}/auth/login", json={"email": email, "password": PASSWORD}, timeout=30)
    r.raise_for_status()
    body = r.json()
    secret = pyotp.parse_uri(body["totp_uri"]).secret
    v = httpx.post(
        f"{BASE}/auth/login/verify",
        json={"pending_token": body["pending_token"], "code": pyotp.TOTP(secret).now()},
        timeout=30,
    )
    v.raise_for_status()
    return v.json()["access_token"]


try:
    # ── Is this server even running the feature? ─────────────────────────
    # Checked FIRST, because the failure it catches is invisible otherwise:
    # a backend started before this route existed answers 404, every step
    # below fails, and the output looks like broken logic rather than a
    # process that needs restarting. This exact thing happened.
    try:
        spec = httpx.get(f"{BASE}/openapi.json", timeout=10).json()
    except Exception as err:  # noqa: BLE001
        sys.exit(f"No server answering at {BASE} ({err}).")
    paths = spec.get("paths", {})
    for route in ("/my-availability", "/brokers/{broker_id}/resume-leads"):
        if route not in paths:
            sys.exit(
                f"The server at {BASE} does not have {route}. It is running "
                "code from before this feature — restart it."
            )
    print(f"Server at {BASE} has both self-pause routes. Starting.")

    # ── Build the sandbox ────────────────────────────────────────────────
    com = supabase.table("community").insert(
        {"name": f"{PREFIX} {uuid.uuid4().hex[:8]}"}
    ).execute().data[0]
    created["community"].append(com["id"])

    grp = supabase.table("broker_group").insert(
        {"community_id": com["id"], "name": f"{PREFIX} G {uuid.uuid4().hex[:6]}"}
    ).execute().data[0]
    created["group"].append(grp["id"])

    brokers = {}
    for label in ("A", "B"):
        row = supabase.table("broker").insert({
            "group_id": grp["id"],
            "name": f"{PREFIX} {label}",
            "email": f"{PREFIX.lower()}-{uuid.uuid4().hex[:10]}@example.com",
            "phone": "+15550000000",
            "password_hash": hash_password(PASSWORD),
            "role": "broker",
            "lines_of_authority": [],
            "daily_cap": 5,
            "max_cap": 5,
        }).execute().data[0]
        created["broker"].append(row["id"])
        brokers[label] = row

    def new_lead(tag):
        row = supabase.table("lead").insert({
            "group_id": grp["id"],
            "source": "manual",
            "name": f"{PREFIX} {tag}",
            "phone": "+15550001111",
            "email": f"{PREFIX.lower()}-{uuid.uuid4().hex[:10]}@example.com",
        }).execute().data[0]
        created["lead"].append(row["id"])
        return row

    def assign(lead_id):
        return supabase.rpc("assign_lead", {"p_lead_id": lead_id}).execute().data

    def holder(lead_id):
        got = supabase.table("lead").select("assigned_broker_id").eq(
            "id", lead_id
        ).execute().data[0]["assigned_broker_id"]
        for label, row in brokers.items():
            if row["id"] == got:
                return label
        return None

    token_a = login(brokers["A"]["email"])
    auth = {"Authorization": f"Bearer {token_a}"}

    print("\n1. PAUSE BROKER A (through PATCH /my-availability)")
    r = httpx.patch(f"{BASE}/my-availability", json={"paused": True}, headers=auth, timeout=30)
    check("request succeeds", r.status_code == 200, f"HTTP {r.status_code} {r.text[:120]}")
    check("body is {paused, backlog_drained}",
          r.json() == {"paused": True, "backlog_drained": True}, str(r.json()))

    print("\n2. A LEAD ARRIVES — IT MUST GO TO B")
    lead1 = new_lead("L1")
    assign(lead1["id"])
    check("lead went to B, not the paused A", holder(lead1["id"]) == "B",
          f"held by {holder(lead1['id'])}")

    print("\n3. RE-READ THE PROFILE — THE SWITCH MUST STILL READ PAUSED")
    p = httpx.get(f"{BASE}/my-profile", headers=auth, timeout=30)
    check("/my-profile returns 200", p.status_code == 200, f"HTTP {p.status_code}")
    check("self_paused is present in the response", "self_paused" in p.json(),
          "the deny-by-default model would strip it")
    check("self_paused is still true after a refresh", p.json().get("self_paused") is True,
          str(p.json().get("self_paused")))

    print("\n4. A LEAD WAITS WHILE BOTH ARE UNAVAILABLE")
    # Fill B so the waiting lead can go nowhere while A is paused.
    supabase.table("broker").update({"daily_cap": 1}).eq("id", brokers["B"]["id"]).execute()
    lead2 = new_lead("L2")
    assign(lead2["id"])
    check("lead is left waiting (B at cap, A paused)", holder(lead2["id"]) is None,
          f"held by {holder(lead2['id'])}")

    print("\n5. RESUME A — THE WAITING LEAD MUST BE ASSIGNED, NO NEW LEAD CREATED")
    r = httpx.patch(f"{BASE}/my-availability", json={"paused": False}, headers=auth, timeout=30)
    check("resume succeeds", r.status_code == 200, f"HTTP {r.status_code}")
    check("backlog_drained reported true", r.json().get("backlog_drained") is True, str(r.json()))
    check("the WAITING lead is now A's", holder(lead2["id"]) == "A",
          f"held by {holder(lead2['id'])}")

    p = httpx.get(f"{BASE}/my-profile", headers=auth, timeout=30)
    check("profile now reads not-paused", p.json().get("self_paused") is False)

    print("\n6. THE REQUEST CANNOT NAME ANOTHER BROKER")
    r = httpx.patch(
        f"{BASE}/my-availability",
        json={"paused": True, "broker_id": brokers["B"]["id"]},
        headers=auth, timeout=30,
    )
    check("a broker_id in the body is refused", r.status_code == 422, f"HTTP {r.status_code}")
    still = supabase.table("broker").select("self_paused").eq(
        "id", brokers["B"]["id"]
    ).execute().data[0]["self_paused"]
    check("B was not paused by A's request", still is False)

finally:
    print("\nCleaning up…")
    for lead_id in created["lead"]:
        for t in ("lead_delivery", "lead_assignment", "lead_stage_history",
                  "meta_signal", "reminder", "premium_history"):
            supabase.table(t).delete().eq("lead_id", lead_id).execute()
        supabase.table("driver").delete().eq("lead_id", lead_id).execute()
        supabase.table("audit_log").delete().eq("entity_id", lead_id).execute()
        supabase.table("lead").delete().eq("id", lead_id).execute()
    for gid in created["group"]:
        supabase.table("broker_group_rotation").delete().eq("group_id", gid).execute()
    for bid in created["broker"]:
        supabase.table("audit_log").delete().eq("actor_broker_id", bid).execute()
        supabase.table("audit_log").delete().eq("entity_id", bid).execute()
        supabase.table("broker").delete().eq("id", bid).execute()
    for gid in created["group"]:
        supabase.table("broker_group").delete().eq("id", gid).execute()
    for cid in created["community"]:
        supabase.table("community").delete().eq("id", cid).execute()
    print("done.")

print("\n" + ("ALL CHECKS PASSED" if ok else "SOME CHECKS FAILED"))
sys.exit(0 if ok else 1)
