"""One-time clean-up of applicant addresses that were split wrongly.

THREE STEPS, and only the first one talks to Claude:

    venv/Scripts/python.exe scripts/fix_addresses.py review --env staging review.json
    venv/Scripts/python.exe scripts/fix_addresses.py apply  --env staging review.json
    venv/Scripts/python.exe scripts/fix_addresses.py undo   --env staging review.undo.json

review  READ-ONLY. Lists every applicant whose saved address fails
        dash_rules.address_problems. Where a DASH PDF is stored, Claude splits
        that PDF's "Address:" line by the rules a reading uses; with no PDF the
        row is listed with no proposal, for a broker to fix by hand. Nothing is
        guessed from the saved fields. Writes the file for a person to check,
        who may delete rows or edit proposals.
apply   Writes exactly what the checked file proposes, never asking Claude
        again: an approved answer must not be swapped for a fresh one. Skips any
        row whose address changed since the review (a broker got there first),
        and refuses a file made against the other database. Saves the values it
        replaces to <file>.undo.json before writing anything.
undo    Puts those values back, skipping any row changed since.

backend/.env is PRODUCTION; --env picks the file, and the database's own ref is
recorded in the review file so the two cannot be confused.
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

_BACKEND = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_BACKEND))

_FIELDS = ("street_address", "city", "province", "postal_code")


def _connect(env: str) -> str:
    """Loads the chosen environment BEFORE db is imported, and returns its ref."""
    from dotenv import load_dotenv

    load_dotenv(_BACKEND / (".env.staging" if env == "staging" else ".env"), override=True)
    return _ref(os.environ["SUPABASE_URL"])


def _ref(url: str) -> str:
    return url.split("//", 1)[1].split(".", 1)[0]


def _current(row: dict) -> dict:
    return {f: row.get(f) for f in _FIELDS}


# pypdfium2 puts the second column on the same line, so the line is cut at the
# next label: "Address: 1 EXAMPLE ST ... M1A1A1 Number of Claims in Last 6 Years: 1"
_ADDRESS_LINE = re.compile(r"Address:\s*(.+?)(?=\s+Number of |\s*\r?\n|$)")


def _address_line(pdf: bytes) -> str | None:
    from services import dash_text

    match = _ADDRESS_LINE.search(dash_text.read_pdf(pdf))
    return match.group(1).strip() if match else None


def review(args) -> None:
    from db import supabase
    from services import dash_claude, dash_rules
    import supabase_storage

    rows, start = [], 0
    while True:
        page = (
            supabase.table("driver")
            .select("id, lead_id, street_address, city, province, postal_code, dash_report_path")
            .eq("is_principal", True)
            .range(start, start + 999)
            .execute()
            .data
        )
        rows += page
        if len(page) < 1000:
            break
        start += 1000

    listed = []
    for row in rows:
        problems = dash_rules.address_problems(*(row.get(f) for f in _FIELDS))
        if not problems:
            continue
        entry = {"driver_id": row["id"], "lead_id": row["lead_id"], "problems": problems,
                 "current": _current(row), "proposed": None, "source": "broker to check"}
        if row.get("dash_report_path"):
            try:
                pdf = supabase.storage.from_(supabase_storage.DASH_BUCKET).download(row["dash_report_path"])
                line = _address_line(pdf)
                if line:
                    split = dash_claude.split_address(line)
                    entry["proposed"] = {
                        "street_address": split["street"] or None,
                        "city": split["city"] or None,
                        "province": split["province"] or None,
                        "postal_code": split["postal_code"] or None,
                    }
                    entry["source"] = f"DASH line: {line}"
                    if split.get("unsure"):
                        entry["source"] += f" (Claude unsure: {split.get('note') or 'no reason'})"
            except Exception as err:
                entry["source"] = f"broker to check (DASH not read: {type(err).__name__})"
        listed.append(entry)
        print(f"{row['id']}  {problems}\n   now: {entry['current']}\n   new: {entry['proposed']}")

    Path(args.file).write_text(json.dumps({"ref": args.ref, "rows": listed}, indent=1), "utf-8")
    print(f"\n{len(listed)} listed, {sum(1 for e in listed if e['proposed'])} with a proposal -> {args.file}")


def _write(args, rows: list[dict], expect: str, write: str, undo_path: Path | None) -> None:
    """For each row whose saved address still equals `expect`, writes `write`."""
    from db import supabase

    done, skipped, undo = 0, 0, []
    for entry in rows:
        if not entry.get(write):
            continue
        saved = supabase.table("driver").select(", ".join(_FIELDS)).eq("id", entry["driver_id"]).execute().data
        if not saved or _current(saved[0]) != entry[expect]:
            skipped += 1
            print(f"skipped {entry['driver_id']}: changed since the file was made")
            continue
        undo.append({"driver_id": entry["driver_id"], "current": entry[write], "proposed": entry[expect]})
        if undo_path:
            # Written before each change, so a crash part-way still leaves a full undo.
            undo_path.write_text(json.dumps({"ref": args.ref, "rows": undo}, indent=1), "utf-8")
        supabase.table("driver").update(entry[write]).eq("id", entry["driver_id"]).execute()
        done += 1
    print(f"{done} written, {skipped} skipped")


def apply(args) -> None:
    data = _load(args)
    undo_path = Path(args.file).with_suffix(".undo.json")
    if undo_path.exists():
        sys.exit(f"{undo_path} already exists — this file was applied before")
    _write(args, data["rows"], "current", "proposed", undo_path)
    print(f"undo file: {undo_path}")


def undo(args) -> None:
    # An undo row's "current" is what apply wrote and "proposed" the original.
    _write(args, _load(args)["rows"], "current", "proposed", None)


def _load(args) -> dict:
    data = json.loads(Path(args.file).read_text("utf-8"))
    if data.get("ref") != args.ref:
        sys.exit(f"this file was made against {data.get('ref')}, not {args.ref} — refusing")
    return data


def main(argv=None) -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("step", choices=("review", "apply", "undo"))
    parser.add_argument("--env", choices=("staging", "production"), required=True)
    parser.add_argument("file")
    args = parser.parse_args(argv)
    args.ref = _connect(args.env)
    {"review": review, "apply": apply, "undo": undo}[args.step](args)


if __name__ == "__main__":
    main()
