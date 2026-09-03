"""
The `google_places` source — the workhorse for real Australian businesses.

Uses the Places API (New) Text Search endpoint, which is what powers "mortgage
brokers in Newcastle" on Google Maps. It gives us name, address, website,
phone, rating and review count in one call, which is most of what scoring
needs; enrich.py then visits the website for an email address.

Set GOOGLE_PLACES_API_KEY to turn this on. Billing note: Text Search is
charged per request, not per result, so raising the page size is free-ish —
the limit below is about not hammering the quota, not cost per row.
"""
from __future__ import annotations

from typing import Any

import httpx

from config import GOOGLE_PLACES_API_KEY
from sources.base import Field
from util import domain_of, clean_phone, normalise_url, parse_address

KEY = "google_places"
LABEL = "Google Places"
DESCRIPTION = (
    "Search Google's business listings the way a customer would — "
    '"mortgage brokers in Newcastle" — and pull in name, address, website, '
    "phone, rating and review count."
)
FIELDS = [
    Field("industry", "Business type", "mortgage brokers", required=True),
    Field("location", "Location", "Newcastle NSW", required=True),
    Field("limit", "How many", "60", kind="number", default="60",
          help="Google returns 20 per page and caps a text search at 60 results."),
]

# Text Search (New) returns 20 results per page and stops after three pages.
PAGE_SIZE = 20
MAX_RESULTS = 60

ENDPOINT = "https://places.googleapis.com/v1/places:searchText"
FIELD_MASK = ",".join([
    "places.id",
    "places.displayName",
    "places.formattedAddress",
    "places.addressComponents",
    "places.websiteUri",
    "places.nationalPhoneNumber",
    "places.internationalPhoneNumber",
    "places.rating",
    "places.userRatingCount",
    "places.primaryTypeDisplayName",
    "places.businessStatus",
    "nextPageToken",
])


def available() -> tuple[bool, str]:
    if not GOOGLE_PLACES_API_KEY:
        return False, "Set GOOGLE_PLACES_API_KEY to enable Google Places."
    return True, ""


def _error_detail(response: httpx.Response) -> str:
    """Google's error message, falling back to raw text for non-JSON bodies."""
    try:
        return response.json().get("error", {}).get("message", "")[:300] or response.text[:200]
    except ValueError:
        return response.text[:200]


def _component(place: dict[str, Any], type_name: str) -> str:
    for component in place.get("addressComponents") or []:
        if type_name in (component.get("types") or []):
            return component.get("shortText") or component.get("longText") or ""
    return ""


def _display_name(value: Any) -> str:
    """Places returns localised text as {"text": ..., "languageCode": ...}."""
    if isinstance(value, dict):
        return value.get("text", "")
    return value or ""


def _to_record(place: dict[str, Any]) -> dict[str, Any]:
    address = place.get("formattedAddress", "")
    parsed = parse_address(address)
    website = normalise_url(place.get("websiteUri", ""))
    return {
        "name": _display_name(place.get("displayName")).strip(),
        "website": website,
        "domain": domain_of(website),
        "phone": clean_phone(place.get("nationalPhoneNumber")
                             or place.get("internationalPhoneNumber")),
        "address": address,
        "suburb": _component(place, "locality") or parsed["suburb"],
        "state": _component(place, "administrative_area_level_1") or parsed["state"],
        "postcode": _component(place, "postal_code") or parsed["postcode"],
        "category": _display_name(place.get("primaryTypeDisplayName")),
        "rating": place.get("rating"),
        "review_count": place.get("userRatingCount"),
        "source_ref": place.get("id", ""),
    }


def search(query: dict[str, Any]) -> list[dict[str, Any]]:
    ok, reason = available()
    if not ok:
        raise RuntimeError(reason)

    industry = (query.get("industry") or "").strip()
    location = (query.get("location") or "").strip()
    text_query = " in ".join(part for part in (industry, location) if part)
    if not text_query:
        raise ValueError("industry or location is required")
    try:
        limit = max(1, min(MAX_RESULTS, int(query.get("limit") or 60)))
    except (TypeError, ValueError):
        limit = 60

    headers = {
        "Content-Type": "application/json",
        "X-Goog-Api-Key": GOOGLE_PLACES_API_KEY,
        "X-Goog-FieldMask": FIELD_MASK,
    }
    results: list[dict[str, Any]] = []
    page_token = ""
    with httpx.Client(timeout=httpx.Timeout(20.0)) as client:
        while len(results) < limit:
            # Every field must stay identical across a paged sequence: Google
            # rejects a pageToken whose request differs from the one that
            # produced it, so pageSize is fixed and the trim happens at the end.
            body: dict[str, Any] = {
                "textQuery": text_query,
                "regionCode": "AU",
                "pageSize": PAGE_SIZE,
            }
            if page_token:
                body["pageToken"] = page_token
            response = client.post(ENDPOINT, headers=headers, json=body)
            if response.status_code >= 400:
                raise RuntimeError(
                    f"Google Places error {response.status_code}: {_error_detail(response)}"
                )
            payload = response.json()
            places = payload.get("places") or []
            for place in places:
                if place.get("businessStatus") in ("CLOSED_PERMANENTLY",):
                    continue
                record = _to_record(place)
                if record["name"]:
                    results.append(record)
            page_token = payload.get("nextPageToken", "")
            if not page_token or not places:
                break
    return results[:limit]
