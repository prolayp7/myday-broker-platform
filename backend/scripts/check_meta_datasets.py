"""Read-only: does this environment send to the Meta dataset it should?

Prints the id in META_CAPI_DATASET_ID (services/meta_signal.py::dataset_id) and
the name META reports for that id, and compares both against what this
environment is supposed to hold. Exits 1 on any gap, so the answer is yes/no
rather than a line someone has to read.

WHY META'S NAME AND NOT JUST THE ID. An id copied from the wrong dataset looks
exactly as valid as the right one. Asking Meta what it is called is the only
check that catches it.

WHICH TOKEN. META_DATASET_QUALITY_TOKEN first, then META_CAPI_ACCESS_TOKEN. The
token is sent as a header by meta_reporting._get and is never printed.

RUN IT INSIDE THE SERVICE, so it sees exactly the deployed variables:

    railway ssh --service <backend> --environment staging
    python scripts/check_meta_datasets.py --staging

Not `railway run` from a laptop: backend/.env would fill in any variable the
environment is missing (load_dotenv never overrides, but it does add), and a
gap would pass as configured.
"""
import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import meta_reporting  # noqa: E402
from services.meta_signal import dataset_id  # noqa: E402

# What META_CAPI_DATASET_ID must hold. The names are Meta's, as shown in Events
# Manager; renaming a dataset in Meta means updating this table.
PRODUCTION = ("3253348891512104", "Auto Insurance")
STAGING = ("1703124824536735", "PolicyScanner – Staging Test")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    env = parser.add_mutually_exclusive_group(required=True)
    env.add_argument("--production", action="store_true")
    env.add_argument("--staging", action="store_true")
    args = parser.parse_args()
    want_id, want_name = PRODUCTION if args.production else STAGING

    token = os.environ.get("META_DATASET_QUALITY_TOKEN") or os.environ.get(
        "META_CAPI_ACCESS_TOKEN"
    )
    if not token:
        print("FAIL  neither META_DATASET_QUALITY_TOKEN nor META_CAPI_ACCESS_TOKEN is set")
        return 1

    got_id = dataset_id()
    if got_id is None:
        print("FAIL  META_CAPI_DATASET_ID is not set")
        return 1

    dataset = meta_reporting._get(got_id, {"fields": "id,name"}, token)
    got_name = (dataset or {}).get("name")
    if got_id != want_id:
        problem = f"id is {got_id}, expected {want_id}"
    elif got_name is None:
        problem = "Meta did not return a name (no access, or the id does not exist)"
    elif got_name != want_name:
        problem = f"Meta calls it {got_name!r}, expected {want_name!r}"
    else:
        problem = None

    print(f"{'OK  ' if problem is None else 'FAIL'}  {got_id}  {got_name or '?'}"
          f"{'' if problem is None else '  — ' + problem}")
    print("Dataset verified." if problem is None else "NOT VERIFIED.")
    return 0 if problem is None else 1


if __name__ == "__main__":
    sys.exit(main())
