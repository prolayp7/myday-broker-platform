"""Tests for broker-document validation and object-key construction.

WHY THIS FILE EXISTS. These are the rules standing between an admin's file
picker and a bucket holding licence documents. Their failure modes are quiet:
a photo stored as a PDF renders as a broken image in every header; an object
key without its random suffix means a replacement silently overwrites the copy
it was meant to supersede, so a failed upload destroys the good file.

WHAT IS NOT HERE. Uploading, signing and deletion all need real storage and a
real database, so they are covered by the end-to-end pass described in the plan
rather than mocked into a shape that proves nothing. What is testable without
either is the pure part — what a kind accepts, what the size limit does, and
where a file lands — which is also where the silent failures live.

The service is imported for _validate and _object_path only. Neither touches
the database or the network.
"""

import os
import re

import pytest
from fastapi import HTTPException, UploadFile

# services.broker_document imports services.broker, which reads configuration
# at import time. Set before the import, and deliberately not the real values.
os.environ.setdefault(
    "JWT_SECRET_KEY",
    "0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
)
if "TOTP_ENCRYPTION_KEY" not in os.environ:
    from cryptography.fernet import Fernet

    os.environ["TOTP_ENCRYPTION_KEY"] = Fernet.generate_key().decode()

from services import broker_document as service  # noqa: E402
from schemas.broker_document import (  # noqa: E402
    ALLOWED_TYPES,
    EXTENSION,
    KIND_LABEL,
    MAX_BYTES,
)

KINDS = ("photo", "ribo_license", "eo_certificate")


def _file(content_type: str, filename: str = "x") -> UploadFile:
    """An UploadFile carrying only what _validate reads. The bytes are passed
    separately, exactly as the route does it."""
    upload = UploadFile(filename=filename, file=None)  # type: ignore[arg-type]
    upload.headers = {"content-type": content_type}  # type: ignore[assignment]
    return upload


# ── what each kind accepts ───────────────────────────────────────────────


@pytest.mark.parametrize("kind", KINDS)
def test_every_kind_has_a_label_and_an_allow_list(kind):
    # A kind reaching the service without either would raise KeyError at the
    # point of building an error message, turning a 422 into a 500.
    assert kind in ALLOWED_TYPES
    assert kind in KIND_LABEL


@pytest.mark.parametrize("kind", KINDS)
def test_accepts_what_its_allow_list_permits(kind):
    for content_type in ALLOWED_TYPES[kind]:
        assert service._validate(kind, _file(content_type), b"bytes") == content_type


def test_photo_refuses_a_pdf():
    """THE ONE THAT MATTERS. A PDF is a perfectly good licence document and a
    broken <img> in the header of every screen the broker opens."""
    with pytest.raises(HTTPException) as err:
        service._validate("photo", _file("application/pdf"), b"bytes")
    assert err.value.status_code == 422
    assert "JPEG or PNG" in err.value.detail


@pytest.mark.parametrize("kind", KINDS)
def test_refuses_an_executable(kind):
    with pytest.raises(HTTPException) as err:
        service._validate(kind, _file("application/x-msdownload"), b"MZ")
    assert err.value.status_code == 422


def test_content_type_parameters_are_ignored():
    """Browsers send "image/jpeg; charset=binary" often enough that comparing
    the raw header would reject real uploads."""
    assert (
        service._validate("photo", _file("image/jpeg; charset=binary"), b"bytes")
        == "image/jpeg"
    )


def test_content_type_is_matched_case_insensitively():
    assert service._validate("photo", _file("IMAGE/JPEG"), b"bytes") == "image/jpeg"


def test_missing_content_type_is_refused_not_crashed():
    upload = UploadFile(filename="x", file=None)  # type: ignore[arg-type]
    upload.headers = {}  # type: ignore[assignment]
    with pytest.raises(HTTPException) as err:
        service._validate("photo", upload, b"bytes")
    assert err.value.status_code == 422


# ── size ─────────────────────────────────────────────────────────────────


def test_at_the_limit_is_allowed():
    assert service._validate("photo", _file("image/png"), b"x" * MAX_BYTES)


def test_one_byte_over_the_limit_is_refused():
    with pytest.raises(HTTPException) as err:
        service._validate("photo", _file("image/png"), b"x" * (MAX_BYTES + 1))
    # 413, not 422: the file was the right KIND of thing, just too big.
    assert err.value.status_code == 413
    assert "10MB" in err.value.detail


def test_an_empty_file_is_refused():
    # A zero-byte upload is a failed file-picker, not a document. Without this
    # it would store cleanly and only reveal itself when someone opened it.
    with pytest.raises(HTTPException) as err:
        service._validate("photo", _file("image/png"), b"")
    assert err.value.status_code == 422


def test_size_is_judged_on_the_bytes_not_a_declared_length():
    """file.size is client-supplied. A limit that trusted it would bind honest
    callers only."""
    upload = _file("image/png")
    upload.size = 1  # type: ignore[assignment]
    with pytest.raises(HTTPException) as err:
        service._validate("photo", upload, b"x" * (MAX_BYTES + 1))
    assert err.value.status_code == 413


# ── where the object lands ───────────────────────────────────────────────

BROKER = "11111111-2222-3333-4444-555555555555"


@pytest.mark.parametrize("kind", KINDS)
def test_object_path_shape(kind):
    for content_type in ALLOWED_TYPES[kind]:
        path = service._object_path(BROKER, kind, content_type)
        assert re.fullmatch(
            rf"brokers/{BROKER}/{kind}-[0-9a-f]{{32}}\.{EXTENSION[content_type]}",
            path,
        ), path


def test_object_path_is_broker_prefixed():
    """Retention here is "this broker left, remove their files", which is a
    prefix delete — unlike recordings, which are keyed by date."""
    assert service._object_path(BROKER, "photo", "image/png").startswith(
        f"brokers/{BROKER}/"
    )


def test_two_uploads_never_share_a_path():
    """THE REASON FOR THE RANDOM SUFFIX. A fixed path would have the new object
    overwrite the one it replaces, so an upload that failed halfway would
    already have destroyed the existing good copy."""
    paths = {
        service._object_path(BROKER, "eo_certificate", "application/pdf")
        for _ in range(200)
    }
    assert len(paths) == 200


@pytest.mark.parametrize("content_type", sorted({t for k in KINDS for t in ALLOWED_TYPES[k]}))
def test_every_allowed_type_has_an_extension(content_type):
    # _object_path indexes EXTENSION directly. A type allowed but unmapped
    # would pass validation and then KeyError while building the key.
    assert content_type in EXTENSION
