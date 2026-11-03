"""Run the DASH reader over the sample reports with each model, into a CSV.

READ-ONLY on the database — nothing is written to any driver. It spends money:
Azure once per report (text cached, so a re-run reads the cache), and one
Claude call per report per model.

    venv/Scripts/python.exe scripts/dash_compare.py "C:/Auto Insurance" out_dir \\
        --models claude-opus-5-5 claude-sonnet-5-5 [--limit 5]

Writes out_dir/compare.csv: one row per report and model with the fields the
form fills, counts of gaps/claims/vehicles/drivers, tokens, and the old regex
parser's reading for comparison. Score it against the hand-checked answer key.
"""
import argparse
import csv
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from dotenv import load_dotenv

# override: a shell-level ANTHROPIC_API_KEY must not shadow the backend's own.
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"), override=True)

import logging  # noqa: E402

from services import dash_apply, dash_azure, dash_claude  # noqa: E402

# Prints dash_claude's "N in / M out tokens" line per call — the real cost.
logging.basicConfig(level=logging.INFO, format="  %(message)s")
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("azure").setLevel(logging.WARNING)

_FIELDS = (
    "name", "first_name", "middle_name", "last_name", "license_number", "date_of_birth",
    "gender", "marital_status", "street_address", "city", "province", "postal_code",
    "renewal_date", "insured_since", "current_insurer_since", "at_fault_claims_6y",
)

ap = argparse.ArgumentParser()
ap.add_argument("reports_dir")
ap.add_argument("out_dir")
ap.add_argument("--models", nargs="+", default=["claude-opus-5-5", "claude-sonnet-5-5"])
ap.add_argument("--limit", type=int, default=0)
ap.add_argument("--only", nargs="*", default=[], help="report-name fragments to pick")
args = ap.parse_args()

out = Path(args.out_dir)
(out / "text").mkdir(parents=True, exist_ok=True)
pdfs = sorted(p for p in Path(args.reports_dir).rglob("*.pdf") if p.name.lower().startswith("dash"))
if args.only:
    picked = {}
    for fragment in args.only:
        match = next((p for p in pdfs if fragment.lower() in p.name.lower()), None)
        if match:
            picked.setdefault(match.name, match)
    pdfs = list(picked.values())
if args.limit:
    pdfs = pdfs[: args.limit]

rows = []
for i, pdf in enumerate(pdfs, 1):
    cached = out / "text" / f"{pdf.stem[:60]}.md"
    if not cached.exists():
        cached.write_text(dash_azure.read_pdf(pdf.read_bytes()), encoding="utf-8")
    markdown = cached.read_text(encoding="utf-8")

    readings = {"regex": dash_apply.from_fallback(markdown)}
    for model in args.models:
        dash_claude._MODEL = model
        try:
            readings[model] = dash_apply.from_claude(dash_claude._ask(markdown, None))
        except dash_claude.DashClaudeError as err:
            readings[model] = {"error": str(err)}

    for reader, report in readings.items():
        row = {"report": pdf.name, "reader": reader, "error": report.get("error", "")}
        driver = report.get("driver") or {}
        row.update({f: driver.get(f, "") for f in _FIELDS})
        for key in ("gaps", "claims", "vehicles", "operators"):
            value = report.get(key)
            row[key] = "" if value is None else len(value)
        row["gaps_detail"] = json.dumps(report.get("gaps") or [])
        row["notes"] = " | ".join(report.get("notes") or [])
        rows.append(row)
    print(f"{i}/{len(pdfs)} {pdf.name}")

with open(out / "compare.csv", "w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=list(rows[0]))
    writer.writeheader()
    writer.writerows(rows)
print(f"\nWrote {out / 'compare.csv'} ({len(rows)} rows)")
