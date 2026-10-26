#!/usr/bin/env bash
# Run the backend locally against the STAGING database.
#
# WHY A SCRIPT. db.py loads .env unconditionally, and .env holds PRODUCTION
# credentials. Pointing at staging means overriding them in the environment
# BEFORE the app imports — load_dotenv does not overwrite variables already
# set, which is what makes this work. Typing that by hand invites the one
# mistake that matters: a local backend quietly writing to production.
#
#   ./run-staging.sh
#
# Everything not in .env.staging falls through to .env: the Meta app secret,
# the JWT key, the WhatsApp transport. Only the differences are overridden.
set -euo pipefail

cd "$(dirname "$0")"

if [ ! -f .env.staging ]; then
  echo "No .env.staging — copy the staging SUPABASE_URL / service-role key into it first." >&2
  exit 1
fi

# Export every assignment in the file, ignoring comments and blanks.
set -a
# shellcheck disable=SC1091
. ./.env.staging
set +a

# A visible confirmation of WHICH database, every single time. The whole point
# of the script is that this is never in doubt.
echo "backend -> ${SUPABASE_URL}"
echo "callback WhatsApp: ${WHATSAPP_CALLBACK_TEMPLATE_NAME:-<unset, queues without sending>}"
echo "renewal  WhatsApp: ${WHATSAPP_RENEWAL_TEMPLATE_NAME:-<unset, queues without sending>}"
echo

exec venv/Scripts/python.exe -m uvicorn main:app --host 127.0.0.1 --port 8000 "$@"
