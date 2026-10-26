"""One-off: populate first/middle/last on rows created before 0094.

BLANK-ONLY, like every other write path: a row whose first_name is already set
is skipped, so a broker's correction survives a re-run. Re-runnable — a second
pass finds nothing to do.

Names Claude cannot resolve stay blank rather than guessed. `name` is never
touched; it stays DASH's/the lead form's printed form.
"""
import os, sys, argparse
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"), override=True)
import claude_client
from supabase import create_client

ap = argparse.ArgumentParser()
ap.add_argument("--write", action="store_true", help="actually write (default: dry run)")
args = ap.parse_args()

sb = create_client(os.environ["SUPABASE_URL"], os.environ["SUPABASE_SERVICE_ROLE_KEY"])
rows = sb.table("driver").select("id,name,first_name,middle_name,last_name").execute().data

todo = [r for r in rows
        if (r.get("name") or "").strip() and not (r.get("first_name") or "").strip()]

print(f"{'WRITING' if args.write else 'DRY RUN'} — {len(todo)} of {len(rows)} rows\n")
print(f"  {'name':26} {'first':14} {'middle':8} {'last'}")
print("  " + "-" * 62)

wrote = blank = 0
for r in todo:
    first, middle, last = claude_client.split_name(r["name"])
    flag = ""
    if not first and not last:
        blank += 1
        flag = "  <- UNRESOLVED, left blank"
    print(f"  {r['name']!r:26} {first!r:14} {middle!r:8} {last!r}{flag}")
    if args.write and (first or last):
        sb.table("driver").update({
            "first_name": first or None,
            "middle_name": middle or None,
            "last_name": last or None,
        }).eq("id", r["id"]).execute()
        wrote += 1

print(f"\n  resolved: {len(todo)-blank}   unresolved(blank): {blank}")
if args.write:
    print(f"  rows written: {wrote}")
else:
    print("  nothing written — re-run with --write")
