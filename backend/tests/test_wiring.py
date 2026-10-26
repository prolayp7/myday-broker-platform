"""Every function a route or service calls on the layer below must exist.

WHY THIS EXISTS. Commit 8500532 (retiring Zoom) deleted generate_password,
reset_credentials and confirm_totp_enrollment from the broker vertical while
leaving the routes and services that call them untouched. Python does not
notice: `service.generate_password` is an attribute lookup at CALL time, so
the app imports cleanly, every test passes, and the button returns 500 in
production. It did, from 2026-09-16 — "Generate password" and first-time 2FA
enrolment were both dead for four days before anyone pressed them.

This test walks the 4-layer convention — routes import `services.x as
service`, services import `repositories.x as repo` — and asserts that every
`service.name(` and `repo.name(` in the source resolves to a real definition.
It is static, runs in milliseconds, needs no database, and turns a runtime
AttributeError in production into a red test on the developer's machine.

It only understands the `as service` / `as repo` alias convention, which is
the codebase's own rule (README "4-layer"). A file that imports the layer
below under another name is skipped, not failed — the point is to catch
deletions, not to police style.
"""

import re
from pathlib import Path

import pytest

BACKEND = Path(__file__).resolve().parent.parent

# (caller directory, callee directory, alias the caller binds the callee to)
_LAYERS = [
    ("routes", "services", "service"),
    ("services", "repositories", "repo"),
]

_DEF = re.compile(r"^(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(", re.M)


def _definitions(path: Path) -> set[str]:
    return set(_DEF.findall(path.read_text(encoding="utf-8")))


def _cases():
    for caller_dir, callee_dir, alias in _LAYERS:
        for caller in sorted((BACKEND / caller_dir).glob("*.py")):
            src = caller.read_text(encoding="utf-8")
            m = re.search(rf"^from {callee_dir} import (\w+) as {alias}\b", src, re.M)
            if not m:
                continue
            callee = BACKEND / callee_dir / f"{m.group(1)}.py"
            calls = set(re.findall(rf"\b{alias}\.([A-Za-z_]\w*)\s*\(", src))
            for name in sorted(calls):
                yield pytest.param(
                    caller, callee, name,
                    id=f"{caller_dir}/{caller.name} -> {callee_dir}/{callee.name}::{name}",
                )


@pytest.mark.parametrize("caller, callee, name", list(_cases()))
def test_every_cross_layer_call_resolves(caller, callee, name):
    assert callee.exists(), f"{caller.name} imports {callee.name}, which does not exist"
    assert name in _definitions(callee), (
        f"{caller.relative_to(BACKEND)} calls {callee.stem}.{name}() but "
        f"{callee.relative_to(BACKEND)} defines no such function. "
        "Either restore it or remove the caller — a route pointing at a "
        "deleted function is a 500 in production, not an import error."
    )
