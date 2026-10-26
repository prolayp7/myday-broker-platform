"""Refuse to run database tests against production.

WHY THIS EXISTS. Five test files in this directory write real rows — brokers,
groups, communities, leads — to whatever SUPABASE_URL resolves to. They load
backend/.env themselves, and backend/.env holds PRODUCTION credentials, so a
bare `pytest` wrote test data straight into the live database. It happened on
2026-09-20: two interrupted runs left 11 communities, 10 groups, 23 brokers and
22 leads behind, because the cleanup is a fixture teardown and a killed process
never reaches its teardown.

The old guard checked only that credentials EXISTED, never which database they
pointed at. That is the gap this closes.

HOW IT WORKS. Any test module marked `dbtest` is skipped unless SUPABASE_URL
names a project on the allowlist below. Nothing is deleted, nothing is
reconfigured — the tests simply do not run, and say why.

TO RUN THEM, point at staging explicitly:

    SUPABASE_URL=https://igrqkthanulzfjlyrcdm.supabase.co \\
    SUPABASE_SERVICE_ROLE_KEY=<staging key> \\
    venv/Scripts/python.exe -m pytest tests/

or use ./run-staging.sh's env file as the source of those two values.

ADDING A PROJECT. Put its ref below only if losing every row in it would not
matter. A ref that serves real brokers does not belong here, however convenient
it would be.
"""

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

# Same load the db-backed modules do, so this sees what they will see.
load_dotenv(Path(__file__).resolve().parent.parent / ".env")

# Supabase project refs these tests may write to. The ref is the subdomain in
# SUPABASE_URL: https://<ref>.supabase.co
_DISPOSABLE_PROJECT_REFS = {
    "igrqkthanulzfjlyrcdm",  # PolicyScanner Staging
}


def _project_ref(url: str) -> str:
    """The <ref> out of https://<ref>.supabase.co, or '' if it is not that shape."""
    without_scheme = url.split("://", 1)[-1]
    host = without_scheme.split("/", 1)[0]
    return host.split(".", 1)[0] if "." in host else ""


def pytest_collection_modifyitems(config, items):
    """Skip every dbtest-marked item unless the target database is disposable.

    A skip rather than an error: the rest of the suite — the mocked tests that
    make up most of it — must still run on a machine configured for production,
    which is every developer machine.
    """
    url = os.environ.get("SUPABASE_URL", "")
    ref = _project_ref(url)

    if ref in _DISPOSABLE_PROJECT_REFS:
        return

    where = f"project '{ref}'" if ref else "an unrecognised SUPABASE_URL"
    skip = pytest.mark.skip(
        reason=(
            f"database tests write real rows and {where} is not on the "
            "disposable allowlist (tests/conftest.py). Point SUPABASE_URL at "
            "staging to run them."
        )
    )
    for item in items:
        if "dbtest" in item.keywords:
            item.add_marker(skip)


def pytest_configure(config):
    # Registered here so `-m dbtest` works and an unknown-marker warning does
    # not train people to ignore warnings.
    config.addinivalue_line(
        "markers", "dbtest: writes real rows; only runs against a disposable database"
    )
