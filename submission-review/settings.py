"""Loads the review's settings. On this PC, files win over the shell, which holds
a stale Anthropic key, and staging's database always wins over backend/.env's.
On the server REVIEW_ENV=production and the variables are already set."""

import os
from datetime import datetime
from pathlib import Path

from dotenv import dotenv_values

_HERE = Path(__file__).resolve().parent
_BACKEND = _HERE.parent / "backend"


def load() -> None:
    if os.environ.get("REVIEW_ENV") == "production":
        return
    production = dotenv_values(_BACKEND / ".env")
    for key in ("ANTHROPIC_API_KEY", "AZURE_DOC_INTELLIGENCE_ENDPOINT", "AZURE_DOC_INTELLIGENCE_KEY"):
        os.environ[key] = production[key]
    staging = dotenv_values(_BACKEND / ".env.staging")
    for key in ("SUPABASE_URL", "SUPABASE_SERVICE_ROLE_KEY"):
        os.environ[key] = staging[key]
    for key, value in dotenv_values(_HERE / ".env").items():
        os.environ[key] = value


def mailbox_since() -> datetime:
    """Mail older than this is never touched. No default, on purpose: the
    mailbox holds other mail that must not be read as submissions."""
    return datetime.fromisoformat(os.environ["REVIEW_MAILBOX_SINCE"])
