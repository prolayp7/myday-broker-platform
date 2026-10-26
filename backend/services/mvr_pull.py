"""Business logic for the MVR register. Owns every HTTPException here.

A broker drops MVR PDFs; each abstract in them becomes one line of the MTO
"Records of Licensed Information" register. The PDF is read and dropped
(migrations/0132 says why).

WHO SEES WHAT:
  broker       their own lines, licence masked; may change and delete them
  admin        every line, licence in full; may change and delete any
  head office  every line, licence in full, read only — through a password,
               not a login (unlock / report below)
"""

import logging
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import HTTPException
from jwt import PyJWTError

import security
from db_errors import db_error
from repositories import audit_log as audit_repo
from repositories import broker as broker_repo
from repositories import mvr_pull as repo
from repositories import mvr_report_settings as settings_repo
from services import dash_azure, mvr_parse

logger = logging.getLogger("myday.mvr")

_ONTARIO = ZoneInfo("America/Toronto")

_ALREADY_LOGGED = HTTPException(status_code=409, detail="This MVR is already logged")


# ── shaping ──────────────────────────────────────────────────────────────


def _mask(licence: str) -> str:
    """First five characters, the rest X — the register's own convention
    ("P7222XXXXX"). Five is the letter plus the surname code, which is what
    lets a person recognise the line without the number being usable."""
    return licence[:5] + "X" * max(len(licence) - 5, 0)


def _one_year_on(d: date) -> date:
    """Same calendar day next year. 29 Feb becomes 28 Feb, not 1 Mar: a
    destruction date must never land AFTER the year is up."""
    try:
        return d.replace(year=d.year + 1)
    except ValueError:
        return d.replace(year=d.year + 1, day=28)


def _shape(row: dict, *, full_licence: bool) -> dict:
    requested_on = date.fromisoformat(str(row["requested_on"]))
    licence = row["licence_number"]
    return {
        **row,
        "licence_number": licence if full_licence else _mask(licence),
        "licence_masked": _mask(licence),
        "requested_on": requested_on,
        # The brokerage's rule, agreed 2026-10-20: the authorized use is
        # complete on the day the MVR is pulled, and destruction is a year on.
        "use_completed_on": requested_on,
        "destruction_on": _one_year_on(requested_on),
    }


def _is_admin(caller: dict) -> bool:
    return caller["role"] == "admin"


def _month_range(month: str) -> tuple[str, str]:
    """'2026-10' -> ('2026-10-01', '2026-11-01'), half-open."""
    year, mon = (int(p) for p in month.split("-"))
    if not 1 <= mon <= 12:
        raise HTTPException(status_code=422, detail="That is not a month")
    start = date(year, mon, 1)
    end = date(year + (mon == 12), 1 if mon == 12 else mon + 1, 1)
    return start.isoformat(), end.isoformat()


def _brokers() -> list[dict]:
    """Everyone who appears in the register, newest name per broker, A-Z."""
    seen: dict[str, str] = {}
    for row in repo.brokers_with_pulls():
        # Rows come newest first, so the first name seen is the latest one —
        # a broker renamed since still appears once, under their current name.
        seen.setdefault(row["broker_id"], row["completed_by"])
    return sorted(
        ({"broker_id": bid, "name": name} for bid, name in seen.items()),
        key=lambda b: b["name"].lower(),
    )


# ── broker and admin ─────────────────────────────────────────────────────


def log_pdf(caller: dict, file_name: str, pdf_bytes: bytes) -> dict:
    """Read one MVR PDF and log every abstract in it.

    Never raises for a bad FILE — a batch of five with one wrong PDF must still
    log the other four — so a file that cannot be read comes back as
    "unreadable" with the reason. It DOES raise when Azure is not configured at
    all, because then no file in the batch can succeed and the broker should
    hear that once, not five times.
    """
    try:
        markdown = dash_azure.read_pdf(pdf_bytes)
    except RuntimeError as err:
        logger.error("MVR reading is not configured: %s", err)
        raise HTTPException(status_code=503, detail="Reading MVRs is not set up yet")
    except TimeoutError:
        logger.warning("Azure timed out reading an MVR for broker %s", caller["id"])
        return _result(file_name, "unreadable", "Reading this MVR took too long — try it again")
    except Exception:
        logger.exception("Azure could not read an MVR for broker %s", caller["id"])
        return _result(file_name, "unreadable", "This file could not be read")

    abstracts = mvr_parse.parse(markdown)
    if not abstracts:
        return _result(file_name, "unreadable", "This doesn't look like an MVR")

    name_rows = broker_repo.get_safe(caller["id"]).data
    completed_by = name_rows[0]["name"] if name_rows else caller["email"]
    today = datetime.now(_ONTARIO).date()

    logged: list[dict] = []
    duplicates = 0
    for abstract in abstracts:
        try:
            rows = repo.insert({
                "broker_id": caller["id"],
                "completed_by": completed_by,
                "driver_name": abstract["driver_name"],
                "licence_number": abstract["licence_number"],
                # The printout always carries "Requested On". If a layout ever
                # drops it, the upload day is the honest fallback — it is the
                # day this broker had the record in hand.
                "requested_on": (abstract["requested_on"] or today).isoformat(),
                "aris_order_number": abstract["aris_order_number"],
            }).data
        except Exception as err:
            http = db_error(err, on_unique=_ALREADY_LOGGED)
            if http is _ALREADY_LOGGED:
                duplicates += 1
                continue
            raise http from err
        logged.append(_shape(rows[0], full_licence=_is_admin(caller)))

    if not logged:
        return _result(file_name, "already_logged", "This MVR is already in the log")
    detail = f"{duplicates} already in the log" if duplicates else None
    return _result(file_name, "logged", detail, logged)


def _result(file_name: str, status: str, detail: str | None = None, pulls: list | None = None) -> dict:
    return {"file_name": file_name, "status": status, "detail": detail, "pulls": pulls or []}


def list_month(caller: dict, month: str, broker_id: str | None = None) -> dict:
    """One month of the register.

    A broker's broker_id is ignored and replaced by their own — there is no
    way to ask for somebody else's lines, which is the same rule /my-lead-cost
    keeps by taking no id at all.
    """
    start, end = _month_range(month)
    admin = _is_admin(caller)
    rows = repo.between(start, end, broker_id if admin else caller["id"])
    return {
        "month": month,
        "pulls": [_shape(r, full_licence=admin) for r in rows],
        "brokers": _brokers() if admin else [],
    }


def _owned(caller: dict, pull_id: str) -> dict:
    """The line, if this caller may touch it. 404 rather than 403 for
    somebody else's line: whether it exists is not theirs to learn either."""
    rows = repo.get(pull_id).data
    if not rows or (not _is_admin(caller) and rows[0]["broker_id"] != caller["id"]):
        raise HTTPException(status_code=404, detail="MVR not found")
    return rows[0]


def update(caller: dict, pull_id: str, changes: dict) -> dict:
    _owned(caller, pull_id)
    values = {k: v for k, v in changes.items() if v is not None}
    if not values:
        raise HTTPException(status_code=422, detail="Nothing to change")
    rows = repo.update(pull_id, values).data
    return _shape(rows[0], full_licence=_is_admin(caller))


def delete(caller: dict, pull_id: str) -> None:
    """Gone from the register for good — a deliberate choice (2026-10-20),
    made after a hidden-but-kept delete was offered and declined.

    The audit_log line records THAT a line was deleted and by whom, with the
    ARIS number and date to identify it. Not the name or licence: the point of
    deleting it is that those are no longer held.
    """
    row = _owned(caller, pull_id)
    repo.delete(pull_id)
    audit_repo.insert(
        actor_broker_id=caller["id"],
        action="mvr_pull_deleted",
        entity_type="mvr_pull",
        entity_id=pull_id,
        detail={
            "aris_order_number": row["aris_order_number"],
            "requested_on": str(row["requested_on"]),
            "owner_broker_id": row["broker_id"],
        },
    )


def set_report_password(admin: dict, password: str) -> None:
    settings_repo.set_password(security.hash_password(password))
    audit_repo.insert(
        actor_broker_id=admin["id"],
        action="mvr_report_password_changed",
        entity_type="mvr_report_settings",
        entity_id="mvr_report",
    )


# ── head office ──────────────────────────────────────────────────────────


def _settings() -> dict:
    rows = settings_repo.get().data
    if not rows:
        # 0132 inserts the row, so this is a deployment fault, not a user one.
        raise HTTPException(status_code=503, detail="The MVR report is not set up yet")
    return rows[0]


_WRONG_PASSWORD = HTTPException(status_code=401, detail="That password is not right")


def unlock(password: str) -> dict:
    settings = _settings()
    if not security.verify_password(password, settings["password_hash"]):
        raise _WRONG_PASSWORD
    return {"token": security.create_mvr_report_token(str(settings["updated_at"]))}


def _check_report_token(token: str | None) -> None:
    """401 unless this is a live report token for the CURRENT password.

    The stage check is what keeps a broker's session token out of here and
    this token out of everything else; the version check is what makes a
    password change take effect immediately rather than in eight hours.
    """
    if not token:
        raise HTTPException(status_code=401, detail="Enter the password to open the report")
    try:
        payload = security.decode_token(token)
    except PyJWTError:
        raise HTTPException(status_code=401, detail="Enter the password to open the report")
    if payload.get("stage") != "mvr_report" or payload.get("pwv") != str(_settings()["updated_at"]):
        raise HTTPException(status_code=401, detail="Enter the password to open the report")


def report(token: str | None, month: str, broker_id: str | None = None) -> dict:
    _check_report_token(token)
    start, end = _month_range(month)
    rows = repo.between(start, end, broker_id)
    return {
        "month": month,
        "pulls": [_shape(r, full_licence=True) for r in rows],
        "brokers": _brokers(),
    }
