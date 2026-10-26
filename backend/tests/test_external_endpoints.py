"""Tests for the third-party endpoint configuration.

WHY THIS FILE EXISTS. Third-party URLs used to be written into the code; they
now come from the environment so a host change is a config edit rather than a
redeploy. Everything here is about that resolution — the defaults, the
overrides, and the two ways it can silently break:

  * a trailing slash producing "//users", which fails as a 404 that reads
    like an outage rather than a config typo
  * one of a PAIR being redirected while the other is not — the shape of the
    incident recorded in meta_capi.py, where the graph base pointed at a dev
    stub and live conversion events kept going to real Meta
"""

import importlib

import pytest

_PLACES_VARS = ("GOOGLE_PLACES_BASE",)


def _reload(monkeypatch, module_name, names, **env):
    """Import a module fresh with the named variables controlled.

    Every name is cleared first, then only what the test asks for is set —
    otherwise the test measures whatever the developer's own .env happens to
    contain. tests/test_meta_capi.py learned that the hard way: its first
    version passed alone and failed in the suite.
    """
    for name in names:
        monkeypatch.delenv(name, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    return importlib.reload(importlib.import_module(module_name))
class TestGooglePlacesEndpoints:
    def _places(self, monkeypatch, **env):
        return _reload(monkeypatch, "services.address", _PLACES_VARS, **env)

    def test_defaults_are_the_real_google_urls(self, monkeypatch):
        address = self._places(monkeypatch)
        assert (
            address._AUTOCOMPLETE_URL
            == "https://places.googleapis.com/v1/places:autocomplete"
        )
        assert address._DETAILS_URL == "https://places.googleapis.com/v1/places/{place_id}"

    def test_one_base_moves_both_urls(self, monkeypatch):
        """They share a base and differ only in path, so a second variable
        would be a second thing to keep in step for no benefit."""
        address = self._places(monkeypatch, GOOGLE_PLACES_BASE="http://localhost:8888")
        assert address._AUTOCOMPLETE_URL == "http://localhost:8888/places:autocomplete"
        assert address._DETAILS_URL == "http://localhost:8888/places/{place_id}"

    def test_details_url_stays_a_format_template(self, monkeypatch):
        """THE HAZARD IN BUILDING THIS URL WITH AN F-STRING. The caller does
        _DETAILS_URL.format(place_id=...), so a single brace would resolve the
        placeholder at import and leave that call substituting nothing —
        every lookup would request the literal path and 404."""
        address = self._places(monkeypatch)
        assert "{place_id}" in address._DETAILS_URL
        assert (
            address._DETAILS_URL.format(place_id="ChIJ123")
            == "https://places.googleapis.com/v1/places/ChIJ123"
        )

    def test_the_template_survives_an_override(self, monkeypatch):
        address = self._places(monkeypatch, GOOGLE_PLACES_BASE="http://localhost:8888")
        assert (
            address._DETAILS_URL.format(place_id="X") == "http://localhost:8888/places/X"
        )

    def test_a_trailing_slash_does_not_double(self, monkeypatch):
        address = self._places(monkeypatch, GOOGLE_PLACES_BASE="http://localhost:8888/")
        assert address._AUTOCOMPLETE_URL == "http://localhost:8888/places:autocomplete"
