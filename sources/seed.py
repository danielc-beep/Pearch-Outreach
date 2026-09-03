"""
The `sample` source — deterministic, obviously-fake Australian businesses.

It exists so the app is fully usable the moment it starts: you can prospect,
score, filter, draft and review without a single API key. Every record it
produces uses an `.example.com.au` domain and is tagged as sample data, so
nobody can mistake one for a real prospect or accidentally email it.
"""
from __future__ import annotations

import hashlib
import random
from typing import Any

from config import ENABLE_SAMPLE_SOURCE, POSTCODE_REGIONS
from sources.base import Field

KEY = "sample"
LABEL = "Sample data"
DESCRIPTION = (
    "Generates realistic-looking but fictional Australian businesses so you can "
    "try the whole workflow without any API keys. Domains end in .example.com.au."
)
FIELDS = [
    Field("industry", "Industry", "mortgage broker", required=True),
    Field("location", "Location", "Newcastle NSW", required=True),
    Field("limit", "How many", "20", kind="number", default="20"),
]

_PREFIXES = ["Hunter", "Coastal", "Summit", "Ironbark", "Bellbird", "Redgum",
             "Harbourview", "Stockton", "Merewether", "Kurrajong", "Wattle",
             "Southern Cross", "Blue Wren", "Old Mill", "Riverbank"]
_SUFFIXES = ["Group", "& Co", "Partners", "Collective", "Services",
             "Australia", "Advisory", "Co-op", "Works", "Studio"]
_STREETS = ["Hunter St", "Beaumont St", "Darby St", "King St", "Church St",
            "Union St", "Watt St", "Maitland Rd", "Pacific Hwy", "High St"]
_SIZE_BANDS = ["1-4", "5-19", "20-49", "50-199"]


def available() -> tuple[bool, str]:
    if not ENABLE_SAMPLE_SOURCE:
        return False, ("Off by default so fictional businesses cannot reach a live "
                       "database. Set PEARCH_ENABLE_SAMPLE_SOURCE=1 to demo the workflow.")
    return True, ""


def _seeded_random(query: dict[str, Any]) -> random.Random:
    """Same query in, same businesses out — so re-runs dedupe cleanly."""
    key = f"{query.get('industry','')}|{query.get('location','')}".lower()
    return random.Random(int(hashlib.sha256(key.encode()).hexdigest()[:12], 16))


def _resolve_location(location: str) -> tuple[str, str, str, str]:
    """Map a free-text location onto a suburb/state/postcode/region."""
    text = (location or "").strip()
    words = [w for w in text.replace(",", " ").split() if w]
    state = next((w.upper() for w in words if w.upper() in
                  {"NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"}), "NSW")
    suburb = " ".join(w for w in words if w.upper() != state).title() or "Newcastle"
    for low, high, st, region in POSTCODE_REGIONS:
        if st == state:
            if region.lower() in text.lower() or suburb.lower() in region.lower():
                return suburb, state, str(low + 2), region
    for low, high, st, region in POSTCODE_REGIONS:
        if st == state:
            return suburb, state, str(low + 2), region
    return suburb, "NSW", "2300", "Newcastle"


def search(query: dict[str, Any]) -> list[dict[str, Any]]:
    industry = (query.get("industry") or "business").strip()
    location = (query.get("location") or "Newcastle NSW").strip()
    try:
        limit = max(1, min(100, int(query.get("limit") or 20)))
    except (TypeError, ValueError):
        limit = 20

    rng = _seeded_random(query)
    suburb, state, postcode, region = _resolve_location(location)
    trade = industry.title()

    results: list[dict[str, Any]] = []
    used: set[str] = set()
    while len(results) < limit and len(used) < len(_PREFIXES) * len(_SUFFIXES):
        name = f"{rng.choice(_PREFIXES)} {trade} {rng.choice(_SUFFIXES)}"
        if name in used:
            continue
        used.add(name)
        slug = name.lower().replace(" & ", "-").replace(" ", "-").replace("&", "and")
        domain = f"{slug}.example.com.au"
        has_email = rng.random() < 0.7
        results.append({
            "name": name,
            "website": f"https://{domain}",
            "domain": domain,
            "email": f"info@{domain}" if has_email else "",
            "phone": f"02 {rng.randint(4000, 4999)} {rng.randint(1000, 9999)}",
            "address": f"{rng.randint(1, 220)} {rng.choice(_STREETS)}, {suburb} {state} {postcode}",
            "suburb": suburb,
            "state": state,
            "postcode": postcode,
            "region": region,
            "industry": trade,
            "category": trade,
            "size_band": rng.choice(_SIZE_BANDS),
            "rating": round(rng.uniform(3.2, 5.0), 1),
            "review_count": rng.randint(0, 180),
            "description": f"Sample record — a fictional {industry.lower()} in {suburb}, {state}.",
            "notes": "Sample data. Not a real business — do not contact.",
            "source_ref": domain,
        })
    return results
