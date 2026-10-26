"""Business logic for broker groups. Owns every HTTPException decision."""

from uuid import UUID

from fastapi import HTTPException

from db_errors import db_error
from repositories import broker_group as repo
from repositories import lead_form as lead_form_repo
from schemas.broker_group import CreateBrokerGroupRequest, RenameBrokerGroupRequest
from schemas.lead_form import ValidateFormRequest
from services import lead_form as lead_form_service


def list_broker_groups():
    rows = repo.list_all().data
    result = []
    for row in rows:
        brokers = row.pop("broker", []) or []
        lead_forms = row.pop("lead_form", []) or []
        # The FIRST mapped form's Meta-confirmed name/id, so the list can show
        # which form routes here rather than just whether one does — plus a
        # count, because a group may have several (lead_form.group_id is not
        # unique; see migrations/0006). Showing one and silently dropping the
        # rest is what this count exists to prevent.
        # None when unmapped — only legacy pre-"no form, no group" rows.
        form = lead_forms[0] if lead_forms else None
        result.append({
            **row,
            # Plain row count — every broker in a real group counts toward its
            # 10-cap (deactivating moves a broker OUT to the Recycle Bin, so a
            # real group never holds an inactive broker). Matches the
            # cap-enforcement count in count_brokers exactly.
            "broker_count": len(brokers),
            "has_form_mapped": form is not None,
            "form_name": form["name"] if form else None,
            "form_platform_id": form["platform_form_id"] if form else None,
            "form_count": len(lead_forms),
        })
    return result


def _reject_duplicate_name(community_id: str, name: str, *, exclude_group_id: str | None = None):
    """A broker group's name must be unique within its community, so two
    identical rows can't sit side by side (indistinguishable in the UI when
    moving/onboarding a broker). Case-insensitive. On rename, the group's own
    row is excluded so renaming to its current name isn't a false conflict."""
    matches = repo.find_by_name_in_community(community_id, name).data
    for row in matches:
        if row["id"] != exclude_group_id:
            raise HTTPException(409, "A group with that name already exists in this community")


def create_broker_group(request: CreateBrokerGroupRequest):
    # A broker group only exists once it can actually receive leads — no
    # group without a Meta-confirmed real form mapped to it (decided
    # 2026-07-12: "no form mapped, no broker group", deliberately no
    # intermediate/pending state). Validate BEFORE creating anything, so a
    # bad form_id leaves nothing behind. Raises 422/502 itself on failure.
    form = lead_form_service.validate_form(
        ValidateFormRequest(platform_form_id=request.platform_form_id)
    )

    _reject_duplicate_name(str(request.community_id), request.name)
    try:
        result = repo.insert(str(request.community_id), request.name)
    except Exception as err:
        # FK = community_id doesn't exist. Unique = a concurrent create used
        # the same name in this community between the pre-check and this
        # insert (the DB index is the race backstop; migration 0015).
        raise db_error(
            err,
            on_foreign_key=HTTPException(400, "That community doesn't exist"),
            on_unique=HTTPException(409, "A group with that name already exists in this community"),
        )

    group = result.data[0]

    try:
        lead_form_repo.insert({
            "platform_form_id": request.platform_form_id,
            "group_id": group["id"],
            # Meta's own form name, fetched during validation above — the admin
            # never types it, so the stored label can't drift from the real form.
            "name": form["name"],
        })
    except Exception as err:
        # The group was already created — but "no form, no group" means it
        # must not survive a failed mapping (e.g. this form_id is already
        # mapped elsewhere). Roll back rather than leave an orphan group.
        repo.delete(group["id"])
        raise db_error(
            err,
            on_unique=HTTPException(409, "That form is already mapped to a group"),
        )

    return result.data


def rename_broker_group(group_id: UUID, request: RenameBrokerGroupRequest):
    current = repo.get_community_id(str(group_id)).data
    if not current:
        raise HTTPException(status_code=404, detail="Broker group not found")
    _reject_duplicate_name(current[0]["community_id"], request.name, exclude_group_id=str(group_id))

    try:
        result = repo.rename(str(group_id), request.name)
    except Exception as err:
        # Unique = a concurrent rename/create took the same name in this
        # community between the pre-check and here (DB backstop, migration 0015).
        raise db_error(
            err,
            on_unique=HTTPException(409, "A group with that name already exists in this community"),
        )

    if not result.data:
        raise HTTPException(status_code=404, detail="Broker group not found")

    return result.data


def delete_broker_group(group_id: UUID):
    # The one global Recycle Bin is system infrastructure — deleting it
    # (possible whenever it happens to be empty) would break Deactivate
    # everywhere, since there'd be nowhere to move brokers to.
    if repo.is_recycle_bin(str(group_id)):
        raise HTTPException(400, "The Recycle Bin can't be deleted")

    # The group's own form mappings go with it. They hold an FK to the group,
    # so the delete below would fail while any remain — and they cannot be
    # removed beforehand, because delete_lead_form refuses a group's last form
    # (a group with no routing receives nothing, silently). Deleting the group
    # is the one case where losing every mapping is the intent.
    lead_form_repo.delete_for_group(str(group_id))

    try:
        result = repo.delete(str(group_id))
    except Exception as err:
        # FK violation here means a broker/campaign/lead still references this group.
        raise db_error(err, on_foreign_key=HTTPException(409, "Can't delete — still in use"))

    # A delete on a missing id succeeds with an empty list, not an error — surface it as 404.
    if not result.data:
        raise HTTPException(status_code=404, detail="Broker group not found")

    return result.data
