"""Business logic for lead_form. Owns every HTTPException decision.

This is the actual lead-routing mechanism: platform_form_id -> group_id.
Campaign no longer carries routing (see services/campaign.py)."""

import logging
from uuid import UUID

import httpx
from fastapi import HTTPException

import meta_graph
from db_errors import db_error
from repositories import broker_group as group_repo
from repositories import lead_form as repo
from schemas.lead_form import CreateLeadFormRequest, ValidateFormRequest

logger = logging.getLogger("myday.lead_form")


def list_lead_forms():
    return repo.list_all().data


def validate_form(request: ValidateFormRequest):
    """Confirm platform_form_id is a real, accessible, ACTIVE Meta lead form.
    Returns Meta's own form record (id, name, status, leads_count). Exposed
    standalone (POST /lead-forms/validate) for an admin to check a form_id,
    and reused by create_lead_form so no dead/fake form can ever be mapped."""
    try:
        form = meta_graph.validate_form(request.platform_form_id)
    except meta_graph.MetaFormValidationError as err:
        raise HTTPException(422, f"Meta rejected this form: {err.reason}")
    except (httpx.HTTPError, KeyError) as err:
        logger.exception("Could not reach Meta to validate form_id %s", request.platform_form_id)
        raise HTTPException(502, "Could not reach Meta to validate this form — try again") from err

    return form


def create_lead_form(request: CreateLeadFormRequest):
    # The Recycle Bin holds deactivated brokers — routing live leads there
    # would send real customers to people who no longer work leads.
    if group_repo.is_recycle_bin(str(request.group_id)):
        raise HTTPException(400, "Leads can't be routed to the Recycle Bin")

    # Confirm with Meta that this is a real, ACTIVE lead form BEFORE mapping it
    # — otherwise a wrong/dead/fabricated form_id gets stored and silently
    # receives zero leads forever.
    form = validate_form(ValidateFormRequest(platform_form_id=request.platform_form_id))

    try:
        result = repo.insert({
            "platform_form_id": request.platform_form_id,
            "group_id": str(request.group_id),
            # Meta's own form name, never admin-typed — the stored label can't
            # drift from what the form is actually called on Meta.
            "name": form["name"],
        })
    except Exception as err:
        raise db_error(
            err,
            on_foreign_key=HTTPException(400, "That broker group doesn't exist"),
            on_unique=HTTPException(409, "That form is already mapped to a group"),
        )

    return result.data


def delete_lead_form(lead_form_id: UUID):
    """Unmap a form from its group.

    REFUSES THE GROUP'S LAST FORM. A group with no form still exists and still
    has brokers, but nothing routes to it — leads simply stop arriving, with
    no error anywhere to say why. That is the same rule create_broker_group
    already enforces at the other end ("no form, no group"); enforcing it only
    at creation would leave the gap open at deletion.

    Checked here rather than in the UI alone: a rule that lives in one screen
    is not a rule, and any other caller could strip a group bare.
    """
    existing = repo.get_by_id(str(lead_form_id))
    if not existing.data:
        raise HTTPException(status_code=404, detail="Lead form mapping not found")

    if repo.count_for_group(existing.data[0]["group_id"]) <= 1:
        raise HTTPException(
            status_code=409,
            detail="This is the group's only form — map another before removing it",
        )

    result = repo.delete(str(lead_form_id))

    if not result.data:
        raise HTTPException(status_code=404, detail="Lead form mapping not found")

    return result.data
