"""scripts/fix_addresses.py: apply writes only the reviewed file, skips rows a
broker changed since, refuses the other database, and undo puts values back.
Supabase is a dict in memory; Claude is never called by apply or undo."""

import json
import sys
from types import SimpleNamespace

import pytest

from scripts import fix_addresses as fx

OLD = {"street_address": "235 Clarke St Oshawa", "city": "ON", "province": "ON", "postal_code": "L1H5G2"}
NEW = {"street_address": "235 Clarke St", "city": "Oshawa", "province": "ON", "postal_code": "L1H5G2"}


class _Table:
    def __init__(self, rows):
        self.rows, self._patch = rows, None

    def select(self, _cols):
        return self

    def update(self, patch):
        self._patch = patch
        return self

    def eq(self, _col, value):
        self._id = value
        return self

    def execute(self):
        row = self.rows.get(self._id)
        if self._patch is not None:
            row.update(self._patch)
            self._patch = None
        return SimpleNamespace(data=[dict(row)] if row else [])


@pytest.fixture
def db(monkeypatch):
    rows = {"d1": dict(OLD), "d2": dict(OLD)}
    fake = SimpleNamespace(table=lambda _name: _Table(rows))
    monkeypatch.setitem(sys.modules, "db", SimpleNamespace(supabase=fake))
    return rows


def _review(tmp_path, ref="staging-ref"):
    path = tmp_path / "review.json"
    entries = [{"driver_id": d, "current": dict(OLD), "proposed": dict(NEW)} for d in ("d1", "d2")]
    path.write_text(json.dumps({"ref": ref, "rows": entries}))
    return path


def _args(path, ref="staging-ref"):
    return SimpleNamespace(file=str(path), ref=ref)


def test_apply_writes_the_file_and_skips_a_row_changed_since(db, tmp_path):
    db["d2"]["city"] = "Oshawa"  # a broker fixed it after the review
    path = _review(tmp_path)
    fx.apply(_args(path))
    assert db["d1"] == NEW
    assert db["d2"]["street_address"] == "235 Clarke St Oshawa"
    undo = json.loads(path.with_suffix(".undo.json").read_text())
    assert [u["driver_id"] for u in undo["rows"]] == ["d1"]


def test_apply_refuses_a_file_from_the_other_database(db, tmp_path):
    with pytest.raises(SystemExit):
        fx.apply(_args(_review(tmp_path, ref="production-ref")))
    assert db["d1"] == OLD


def test_apply_runs_once(db, tmp_path):
    path = _review(tmp_path)
    fx.apply(_args(path))
    with pytest.raises(SystemExit):
        fx.apply(_args(path))


def test_undo_puts_the_originals_back(db, tmp_path):
    path = _review(tmp_path)
    fx.apply(_args(path))
    fx.undo(_args(path.with_suffix(".undo.json")))
    assert db["d1"] == OLD and db["d2"] == OLD


def test_the_address_line_is_cut_at_the_next_label(monkeypatch):
    from services import dash_text
    monkeypatch.setattr(dash_text, "read_pdf", lambda _pdf: (
        "Address: 165 SPRING VIEW DRIVE BRAMPTON ON L6X4K5 Number of Claims in Last 6 Years: 1\r\n"
        "Number of Policies at this Address"))
    assert fx._address_line(b"") == "165 SPRING VIEW DRIVE BRAMPTON ON L6X4K5"
