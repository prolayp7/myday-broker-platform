"""One-off: re-stamp August 2026's deliveries at the real $3 price.

WHY THESE ROWS ARE WRONG. 0088 seeded lead_cost_settings with 20 — a
placeholder so the calculation could answer on its first day, not a price
anyone agreed to. Nothing could change it, because the endpoint existed and no
screen called it, so the trigger stamped $20 onto every delivery. The real rate
was always $3.

WHY REWRITING IS RIGHT HERE, when cost_at_delivery exists precisely so a price
change cannot reach back into a month already billed. That freeze protects a
REAL price applied to real leads. This is the opposite: $20 was never charged
to anyone, no statement has gone out, the month is still open, and leaving it
would bill brokers $1,122 for a migration's placeholder.

FOUR GUARDS, because this is money on a bill:

  1. refuses unless the configured price is exactly $3
  2. dry run by default; --write to apply
  3. filters by MONTH as well as price, so the promise "nothing outside August
     is touched" is enforced by the query rather than assumed
  4. asserts the number updated equals the number the dry run found

Idempotent: a second run finds nothing, because $3 rows are not candidates.
"""

import argparse
import os
import sys
from decimal import Decimal

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))

from dotenv import load_dotenv

load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env"))

from db import supabase  # noqa: E402
from services.lead_cost import month_bounds  # noqa: E402

# The month whose deliveries carry the placeholder. Hard-coded rather than
# derived: this is a one-off correction of a known, measured set of rows, and
# a script that decides for itself which months to rewrite is a different and
# far more dangerous thing.
MONTH = "2026-08"
WRONG = Decimal("20")
RIGHT = Decimal("3")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true", help="apply (default: dry run)")
    args = ap.parse_args()

    # GUARD 1. If the configured price is not what we are correcting toward,
    # the assumption behind this whole script is already wrong.
    settings = supabase.table("lead_cost_settings").select("cost_per_lead").eq(
        "id", 1
    ).execute().data
    current = Decimal(str(settings[0]["cost_per_lead"])) if settings else None
    if current != RIGHT:
        print(f"ABORT: configured price is {current}, expected {RIGHT}.")
        print("Set the price to its real value before correcting the deliveries.")
        return 1

    # GUARD 3. Month AND price. month_bounds converts Ontario month edges to
    # UTC instants — delivered_at is a timestamptz, and a lead delivered
    # 31 Aug 23:00 Ontario is 1 Sep in UTC.
    start, end = month_bounds(MONTH)
    candidates = (
        supabase.table("lead_delivery")
        .select("id, delivered_at, cost_at_delivery")
        .eq("cost_at_delivery", str(WRONG))
        .gte("delivered_at", start)
        .lt("delivered_at", end)
        .order("delivered_at")
        .execute()
        .data
    )

    print(f"{'WRITING' if args.write else 'DRY RUN'} — {MONTH} ({start} .. {end})")
    print(f"candidates at ${WRONG}: {len(candidates)}\n")
    for row in candidates:
        print(f"  {row['id']}  {row['delivered_at'][:19]}  ${row['cost_at_delivery']}")

    if not candidates:
        print("\nNothing to do.")
        return 0

    if not args.write:
        print(f"\n{len(candidates)} rows would become ${RIGHT}. Re-run with --write.")
        return 0

    ids = [row["id"] for row in candidates]
    updated = (
        supabase.table("lead_delivery")
        .update({"cost_at_delivery": str(RIGHT)})
        .in_("id", ids)
        .execute()
        .data
    )

    # GUARD 4. A mismatch means the set moved between reading and writing —
    # worth failing loudly rather than reporting a number nobody checked.
    if len(updated) != len(candidates):
        print(f"\nMISMATCH: updated {len(updated)}, expected {len(candidates)}.")
        return 1

    print(f"\n{len(updated)} rows updated to ${RIGHT}.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
