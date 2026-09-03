"""
The `explorium` source — Explorium's business database (Vibe Prospecting).

Better firmographics than Google Places: employee bands, revenue bands, NAICS
and SIC classification, and a written description of what the business does.
Where Places tells you a business exists on a street, Explorium tells you how
big it is and what it sells.

Set EXPLORIUM_API_KEY to turn this on. Each fetch spends Explorium credits, so
the `limit` field on the form is a real budget control, not a display setting.

    POST https://api.explorium.ai/v2/businesses
    header: api_key: <key>

A caution on field names: Explorium's response keys are mapped below through
CANDIDATES, which tries several spellings per column. That is deliberate — the
mapping was written from the documented contract rather than from a live
response, so `scripts/probe_explorium.py` dumps a real record to confirm it.
Once confirmed, the extra candidates cost nothing and guard against the API
renaming a field under us.
"""
from __future__ import annotations

from typing import Any, Iterable

import httpx

from config import EXPLORIUM_API_KEY
from sources.base import Field
from util import domain_of, normalise_url, truncate

KEY = "explorium"
LABEL = "Explorium"
DESCRIPTION = (
    "Search Explorium's business database — the same data behind Vibe Prospecting. "
    "Richer than Google Places: employee and revenue bands, industry codes, and a "
    "description of what each business actually does. Spends Explorium credits."
)

# Explorium matches industry on its own category vocabulary, and a term that
# isn't in it returns nothing rather than an error. These are real values, so
# the picker can't produce an empty result through a typo.
CATEGORIES = [
    "loan brokers",
    "credit intermediation",
    "real estate agents and brokers",
    "real estate",
    "commercial real estate",
    "financial services",
    "insurance agencies and brokerages",
    "accounting",
    "law practice",
    "legal services",
    "hospitals and health care",
    "dentists",
    "medical practices",
    "construction",
    "building construction",
    "civil engineering",
    "automotive",
    "restaurants",
    "hospitality",
    "travel arrangements",
    "education administration programs",
    "retail",
    "farming",
    "mining",
]

AU_STATES = {
    "": "",
    "All of Australia": "",
    "NSW": "AU-NSW", "VIC": "AU-VIC", "QLD": "AU-QLD", "SA": "AU-SA",
    "WA": "AU-WA", "TAS": "AU-TAS", "NT": "AU-NT", "ACT": "AU-ACT",
}

SIZES = ["1-10", "11-50", "51-200", "201-500", "501-1000", "1001-5000"]

FIELDS = [
    Field("category", "Industry", required=True, kind="select", options=CATEGORIES,
          default="loan brokers",
          help="Explorium's own category vocabulary — pick from the list, "
               "since a term outside it returns no results."),
    Field("state", "State", kind="select", options=list(AU_STATES), default="NSW",
          help="Explorium filters on state, not suburb. Narrow to a town afterwards "
               "using the database filters."),
    Field("size", "Company size", kind="select", options=["", *SIZES], default="11-50",
          help="Employee band. Leave blank for any size."),
    Field("limit", "How many", "10", kind="number", default="10",
          help="One credit per business. A starter Explorium plan is 100 credits "
               "in total, so treat this as spending money, not a page size."),
]

# The AgentSource console documents v2; the older public reference says v1.
# ENDPOINTS is tried in order, so a 404 on the first falls through rather than
# failing the whole search — scripts/probe_explorium.py reports which answered.
ENDPOINTS = (
    "https://api.explorium.ai/v2/businesses",
    "https://api.explorium.ai/v1/businesses",
)
ENDPOINT = ENDPOINTS[0]
PAGE_SIZE = 100
MAX_RESULTS = 200

# Explorium's response keys, best-guess first. See the module docstring.
CANDIDATES: dict[str, tuple[str, ...]] = {
    "name":        ("business_name", "name", "company_name"),
    "domain":      ("business_domain", "domain", "company_domain"),
    "website":     ("business_website", "website", "business_url", "url"),
    "description": ("business_business_description", "business_description", "description"),
    "city":        ("business_city_name", "city_name", "city"),
    "region":      ("business_region", "region", "state", "region_name"),
    "country":     ("business_country_name", "country_name", "country"),
    "size_band":   ("business_number_of_employees_range", "number_of_employees_range",
                    "company_size", "employees_range"),
    "revenue":     ("business_yearly_revenue_range", "yearly_revenue_range", "company_revenue"),
    "naics":       ("business_naics_description", "naics_description", "naics"),
    "sic":         ("business_sic_code_description", "sic_code_description", "sic"),
    "ref":         ("business_id", "id", "explorium_id"),
}

# The array of records sits under one of these, depending on the endpoint mode.
RESULT_KEYS = ("data", "results", "businesses", "records", "response")

# State names as Explorium writes them, back to the postcode-free state code
# our own records use.
STATE_NAMES = {
    "new south wales": "NSW", "victoria": "VIC", "queensland": "QLD",
    "south australia": "SA", "western australia": "WA", "tasmania": "TAS",
    "northern territory": "NT", "australian capital territory": "ACT",
}


def available() -> tuple[bool, str]:
    if not EXPLORIUM_API_KEY:
        return False, "Set EXPLORIUM_API_KEY to enable Explorium."
    return True, ""


def _first(record: dict[str, Any], names: Iterable[str]) -> Any:
    """First key in `names` that the record actually carries a value for."""
    for name in names:
        value = record.get(name)
        if value not in (None, "", [], {}):
            return value
    return None


def _text(value: Any) -> str:
    """Explorium returns some fields as {"text": ...} or as a list."""
    if isinstance(value, dict):
        return str(value.get("text") or value.get("name") or "")
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value or "")


def _to_record(raw: dict[str, Any]) -> dict[str, Any]:
    get = lambda key: _text(_first(raw, CANDIDATES[key]))  # noqa: E731

    website = normalise_url(get("website") or get("domain"))
    region_name = get("region")
    revenue = get("revenue")

    notes = f"Explorium: {revenue} revenue band." if revenue else ""

    return {
        "name": get("name").strip(),
        "website": website,
        "domain": domain_of(website) or get("domain").lower(),
        # Explorium filters and reports at state level, so there is no postcode
        # to map onto an ACM region — the region column is left for the user or
        # a later enrichment pass to fill.
        "suburb": get("city").title(),
        "state": STATE_NAMES.get(region_name.lower(), region_name.upper()[:3] if region_name else ""),
        "industry": get("naics") or get("sic"),
        "category": get("sic"),
        "size_band": get("size_band"),
        "description": truncate(get("description")),
        "notes": notes,
        "source_ref": get("ref"),
    }


def _error_detail(response: httpx.Response) -> str:
    try:
        payload = response.json()
    except ValueError:
        return response.text[:200]
    for key in ("message", "error", "detail", "title"):
        if isinstance(payload, dict) and payload.get(key):
            return str(payload[key])[:300]
    return response.text[:200]


def search(query: dict[str, Any]) -> list[dict[str, Any]]:
    ok, reason = available()
    if not ok:
        raise RuntimeError(reason)

    category = (query.get("category") or "").strip()
    state = (query.get("state") or "").strip()
    size = (query.get("size") or "").strip()
    if not category:
        raise ValueError("an industry is required")
    try:
        limit = max(1, min(MAX_RESULTS, int(query.get("limit") or 10)))
    except (TypeError, ValueError):
        limit = 10

    filters: dict[str, Any] = {"linkedin_category": {"values": [category]}}
    # country_code and region_country_code are mutually exclusive — sending both
    # is rejected, so a chosen state replaces the country filter entirely.
    region_code = AU_STATES.get(state, "")
    if region_code:
        filters["region_country_code"] = {"values": [region_code]}
    else:
        filters["country_code"] = {"values": ["AU"]}
    if size:
        filters["company_size"] = {"values": [size]}

    headers = {"Content-Type": "application/json", "api_key": EXPLORIUM_API_KEY}
    results: list[dict[str, Any]] = []
    page = 1
    working_endpoint = ENDPOINTS[0]

    with httpx.Client(timeout=httpx.Timeout(30.0)) as client:
        while len(results) < limit:
            body = {
                "mode": "full",
                "page": page,
                "page_size": min(PAGE_SIZE, limit - len(results)),
                "filters": filters,
            }
            response = None
            for endpoint in (ENDPOINTS if page == 1 else (working_endpoint,)):
                response = client.post(endpoint, headers=headers, json=body)
                if response.status_code != 404:
                    working_endpoint = endpoint
                    break
            if response is None or response.status_code >= 400:
                status = response.status_code if response is not None else 0
                raise RuntimeError(
                    f"Explorium error {status}: {_error_detail(response)}"
                    if response is not None else "Explorium did not respond"
                )
            payload = response.json()
            rows = _first(payload, RESULT_KEYS) if isinstance(payload, dict) else payload
            if not isinstance(rows, list):
                raise RuntimeError(
                    "Explorium returned an unexpected response shape — "
                    "run scripts/probe_explorium.py to see it."
                )
            for raw in rows:
                record = _to_record(raw)
                if record["name"]:
                    results.append(record)
            if len(rows) < body["page_size"]:
                break                      # last page
            page += 1

    return results[:limit]
