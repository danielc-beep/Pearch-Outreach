"""
Google Places source, tested against a mocked API.

No key and no network: httpx.MockTransport plays the part of Google, so the
request we actually send — field mask, paging, region — is asserted rather
than assumed. These are the paths that only otherwise run in production.
"""
from __future__ import annotations

import json

import httpx
import pytest

import sources.google_places as gp


def _place(name: str, place_id: str, **extra):
    return {
        "id": place_id,
        "displayName": {"text": name, "languageCode": "en"},
        "formattedAddress": "12 Hunter St, Newcastle NSW 2300, Australia",
        "addressComponents": [
            {"types": ["locality"], "shortText": "Newcastle", "longText": "Newcastle"},
            {"types": ["administrative_area_level_1"], "shortText": "NSW", "longText": "New South Wales"},
            {"types": ["postal_code"], "shortText": "2300", "longText": "2300"},
        ],
        "websiteUri": f"https://{place_id}.com.au",
        "nationalPhoneNumber": "(02) 4979 5000",
        "rating": 4.6,
        "userRatingCount": 42,
        "primaryTypeDisplayName": {"text": "Mortgage broker", "languageCode": "en"},
        **extra,
    }


def _client_with(pages: list[dict], captured: list[dict]) -> None:
    """Patch httpx.Client so google_places talks to our fake Google."""
    def handler(request: httpx.Request) -> httpx.Response:
        captured.append({
            "body": json.loads(request.content),
            "headers": dict(request.headers),
        })
        return httpx.Response(200, json=pages[len(captured) - 1])

    transport = httpx.MockTransport(handler)
    original = httpx.Client

    def factory(*args, **kwargs):
        kwargs["transport"] = transport
        return original(*args, **kwargs)

    gp.httpx.Client = factory
    return original


@pytest.fixture
def fake_google(monkeypatch):
    monkeypatch.setattr(gp, "GOOGLE_PLACES_API_KEY", "test-key")
    original = httpx.Client
    yield
    gp.httpx.Client = original


def test_unavailable_without_a_key(monkeypatch):
    monkeypatch.setattr(gp, "GOOGLE_PLACES_API_KEY", "")
    ok, reason = gp.available()
    assert ok is False and "GOOGLE_PLACES_API_KEY" in reason
    with pytest.raises(RuntimeError):
        gp.search({"industry": "x", "location": "y"})


def test_maps_a_place_onto_our_columns(fake_google):
    captured: list[dict] = []
    _client_with([{"places": [_place("Hunter Home Loans", "hunterhomeloans")]}], captured)

    rows = gp.search({"industry": "mortgage brokers", "location": "Newcastle NSW", "limit": 20})

    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "Hunter Home Loans"
    assert row["domain"] == "hunterhomeloans.com.au"
    assert row["phone"] == "02 4979 5000"          # normalised from "(02) 4979 5000"
    assert row["suburb"] == "Newcastle"
    assert row["state"] == "NSW"
    assert row["postcode"] == "2300"
    assert row["category"] == "Mortgage broker"     # unwrapped from the localised dict
    assert row["rating"] == 4.6 and row["review_count"] == 42
    assert row["source_ref"] == "hunterhomeloans"


def test_the_request_carries_the_field_mask_and_region(fake_google):
    captured: list[dict] = []
    _client_with([{"places": [_place("A", "a")]}], captured)
    gp.search({"industry": "dentists", "location": "Bendigo VIC", "limit": 5})

    sent = captured[0]
    assert sent["headers"]["x-goog-api-key"] == "test-key"
    assert "places.websiteUri" in sent["headers"]["x-goog-fieldmask"]
    assert sent["body"]["textQuery"] == "dentists in Bendigo VIC"
    assert sent["body"]["regionCode"] == "AU"


def test_paging_keeps_every_parameter_identical(fake_google):
    """Google rejects a pageToken whose request differs from the original."""
    captured: list[dict] = []
    _client_with([
        {"places": [_place(f"A{i}", f"a{i}") for i in range(20)], "nextPageToken": "tok-2"},
        {"places": [_place(f"B{i}", f"b{i}") for i in range(20)], "nextPageToken": "tok-3"},
        {"places": [_place(f"C{i}", f"c{i}") for i in range(20)]},
    ], captured)

    rows = gp.search({"industry": "cafes", "location": "Hobart TAS", "limit": 60})

    assert len(rows) == 60
    assert len(captured) == 3
    # Every page must ask for the same thing, only adding the token.
    assert [c["body"]["pageSize"] for c in captured] == [20, 20, 20]
    assert [c["body"]["textQuery"] for c in captured] == ["cafes in Hobart TAS"] * 3
    assert "pageToken" not in captured[0]["body"]
    assert captured[1]["body"]["pageToken"] == "tok-2"
    assert captured[2]["body"]["pageToken"] == "tok-3"


def test_a_partial_page_is_trimmed_to_the_limit(fake_google):
    captured: list[dict] = []
    _client_with([
        {"places": [_place(f"A{i}", f"a{i}") for i in range(20)], "nextPageToken": "tok-2"},
        {"places": [_place(f"B{i}", f"b{i}") for i in range(20)]},
    ], captured)

    rows = gp.search({"industry": "vets", "location": "Ballarat VIC", "limit": 25})
    assert len(rows) == 25          # asked for 25, fetched 40, trimmed


def test_permanently_closed_businesses_are_skipped(fake_google):
    captured: list[dict] = []
    _client_with([{"places": [
        _place("Open Co", "open"),
        _place("Gone Co", "gone", businessStatus="CLOSED_PERMANENTLY"),
    ]}], captured)

    rows = gp.search({"industry": "x", "location": "y", "limit": 20})
    assert [r["name"] for r in rows] == ["Open Co"]


def test_an_api_error_surfaces_googles_message(fake_google):
    def handler(request):
        return httpx.Response(403, json={"error": {"message": "Places API (New) has not been used"}})
    transport = httpx.MockTransport(handler)
    original = httpx.Client
    gp.httpx.Client = lambda *a, **kw: original(*a, **{**kw, "transport": transport})

    with pytest.raises(RuntimeError, match="Places API \\(New\\) has not been used"):
        gp.search({"industry": "x", "location": "y"})


def test_a_non_json_error_body_does_not_crash(fake_google):
    def handler(request):
        return httpx.Response(502, text="<html>Bad Gateway</html>")
    transport = httpx.MockTransport(handler)
    original = httpx.Client
    gp.httpx.Client = lambda *a, **kw: original(*a, **{**kw, "transport": transport})

    with pytest.raises(RuntimeError, match="502"):
        gp.search({"industry": "x", "location": "y"})
