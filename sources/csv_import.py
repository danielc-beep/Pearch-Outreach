"""
The `csv` source — paste or upload a list you already have.

Header names are matched loosely (case-insensitive, punctuation ignored) so
an export from a CRM, a Google Sheet, or a bought list all import without
anyone having to rename columns first.
"""
from __future__ import annotations

import csv
import io
import re
from typing import Any

from sources.base import Field

KEY = "csv"
LABEL = "CSV / paste a list"
DESCRIPTION = (
    "Import businesses from a CSV you already have. Columns are matched by name — "
    "business name, website, email, phone, address, suburb, state, postcode, industry."
)
FIELDS = [
    Field("csv", "CSV content", "name,website,email\nAcme Pty Ltd,acme.com.au,info@acme.com.au",
          required=True, kind="textarea",
          help="Paste rows including the header line."),
]

# Every header spelling we accept, mapped to our column name.
ALIASES = {
    "name": ("name", "business", "businessname", "company", "companyname", "tradingname", "title"),
    "legal_name": ("legalname", "entityname", "registeredname"),
    "abn": ("abn",),
    "website": ("website", "url", "web", "domain", "site", "websiteurl"),
    "email": ("email", "emailaddress", "contactemail", "e-mail"),
    "phone": ("phone", "telephone", "tel", "mobile", "contactnumber", "phonenumber"),
    "address": ("address", "streetaddress", "address1", "fulladdress"),
    "suburb": ("suburb", "city", "town", "locality"),
    "state": ("state", "region_state"),
    "postcode": ("postcode", "postalcode", "zip", "zipcode"),
    "industry": ("industry", "vertical", "category", "sector", "type"),
    "size_band": ("size", "employees", "headcount", "staff"),
    "linkedin": ("linkedin", "linkedinurl"),
    "facebook": ("facebook", "facebookurl"),
    "instagram": ("instagram", "instagramurl"),
    "notes": ("notes", "comment", "comments"),
}
_LOOKUP = {alias: field for field, aliases in ALIASES.items() for alias in aliases}


def available() -> tuple[bool, str]:
    return True, ""


def _key(header: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (header or "").lower())


def search(query: dict[str, Any]) -> list[dict[str, Any]]:
    raw = (query.get("csv") or "").strip()
    if not raw:
        return []
    reader = csv.DictReader(io.StringIO(raw))
    results: list[dict[str, Any]] = []
    for row in reader:
        record: dict[str, Any] = {}
        for header, value in row.items():
            field = _LOOKUP.get(_key(header or ""))
            if field and (value or "").strip():
                record[field] = value.strip()
        if record.get("name"):
            record.setdefault("source_ref", record.get("website") or record["name"])
            results.append(record)
    return results
