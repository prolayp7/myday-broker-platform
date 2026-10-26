"""Reset one broker's password directly, for an admin locked out of an
environment.

WHY THIS EXISTS: POST /brokers/{id}/generate-password is the normal path, but
it needs an admin session — useless when the person locked out IS the admin.
That is a genuine bootstrap hole, and the alternative is hand-editing hashes in
the Supabase console, which is worse.

Does exactly what services/broker.py::generate_password does, minus the audit
row (there is no acting admin session to attribute it to):
  - sets a new bcrypt password hash
  - clears TOTP, so 2FA is re-enrolled on next login
  - clears the failed-attempt lockout
  - forces a password change at next login

Reads SUPABASE_* and the bcrypt cost from backend/.env, the same as every other
script here. Prints the broker's email and role before writing, so a mistyped
address cannot silently reset someone else.

    python scripts/reset_broker_password.py <email>
    python scripts/reset_broker_password.py <email> --password 'Chosen@123'

With no --password, one is generated. Run it from backend/ with the venv
active.
"""

import argparse
import secrets
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import bcrypt  # noqa: E402

from db import supabase  # noqa: E402


def _generated() -> str:
    """Readable enough to type once, random enough not to be guessed. Matches
    the shape services/broker.py issues, so a broker being talked through this
    over the phone hears the same kind of thing either way."""
    words = ["amber", "birch", "cedar", "coral", "delta", "ember", "falcon",
             "granite", "harbor", "indigo", "juniper", "lagoon", "maple",
             "nectar", "oak", "pebble", "quartz", "river", "summit", "timber"]
    return f"{secrets.choice(words)}-{secrets.choice(words)}{secrets.randbelow(90) + 10}"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("email")
    parser.add_argument("--password", default=None, help="Defaults to a generated one.")
    args = parser.parse_args()

    email = args.email.strip().lower()

    # Confirm the row BEFORE writing. A typo here would otherwise reset a
    # colleague's login and tell nobody.
    found = (
        supabase.table("broker")
        .select("id, name, email, role, is_active, is_archived")
        .eq("email", email)
        .execute()
    )
    if not found.data:
        print(f"No broker with email {email!r}.")
        return 1

    broker = found.data[0]
    print(f"  {broker['name']}  <{broker['email']}>")
    print(f"  role={broker['role']}  active={broker['is_active']}  archived={broker['is_archived']}")

    if broker["is_archived"]:
        # Archive is a permanent one-way lock everywhere else in this codebase
        # (services/broker.py::_reject_if_archived); a back-door script must
        # not be the one place it does not hold.
        print("\nThis broker is archived and cannot be changed.")
        return 1

    password = args.password or _generated()
    password_hash = bcrypt.hashpw(password.encode(), bcrypt.gensalt()).decode()

    supabase.table("broker").update(
        {
            "password_hash": password_hash,
            # Cleared together on purpose: someone who lost their password has
            # usually lost the phone holding their 2FA too, and a reset that
            # left them stuck at the TOTP step would not be a reset.
            "totp_secret": None,
            "totp_enrolled_at": None,
            "failed_login_count": 0,
            "locked_until": None,
            "must_change_password": True,
        }
    ).eq("id", broker["id"]).execute()

    print(f"\n  password: {password}")
    print("\nTemporary — you will be asked to change it at login, and 2FA re-enrolls.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
