"""
Enrichment: turn a bare business record into one we can actually email.

Given a website, fetch the homepage and the most likely contact page, then
pull out an email address, a phone number, social profiles, a description,
and an industry guess. No API key needed — it is just HTTP and regex.

Everything here is best-effort: a site that 403s or has no email simply
returns fewer fields. It never raises at the callers' level.
"""
from __future__ import annotations

import re
from typing import Any

import httpx

from config import ABR_GUID
from util import (domain_of, find_emails, find_phone, normalise_url,
                  strip_tags, truncate)

USER_AGENT = (
    "Mozilla/5.0 (compatible; PearchOutreachBot/1.0; +https://pearch.com.au) "
    "prospect-research"
)
TIMEOUT = httpx.Timeout(10.0, connect=6.0)

# Pages most likely to carry a real email address, best first.
CONTACT_PATHS = ("/contact", "/contact-us", "/contactus", "/about", "/about-us", "/get-in-touch")

SOCIAL_PATTERNS = {
    "linkedin": re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in)/[A-Za-z0-9_\-%.]+", re.I),
    "facebook": re.compile(r"https?://(?:www\.)?facebook\.com/[A-Za-z0-9_\-.]+", re.I),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9_\-.]+", re.I),
}

_META_DESC_RE = re.compile(
    r'<meta[^>]+(?:name|property)=["\'](?:description|og:description)["\'][^>]+content=["\']([^"\']+)',
    re.I,
)
_TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title>", re.I | re.S)

# Keyword → industry label. First match wins, so order matters: put the
# specific terms above the generic ones.
INDUSTRY_KEYWORDS: list[tuple[tuple[str, ...], str]] = [
    (("mortgage broker", "home loan", "lending specialist"), "Home loans"),
    (("real estate", "property management", "realty"), "Real estate"),
    (("conveyanc", "solicitor", "lawyer", "legal advice"), "Legal"),
    (("accountant", "accounting", "bookkeep", "tax return"), "Accounting"),
    (("financial plan", "financial advis", "wealth management"), "Financial planning"),
    (("dentist", "dental"), "Dental"),
    (("physio", "chiropract", "podiatr", "medical centre", "gp clinic"), "Medical"),
    (("aged care", "retirement living", "home care package"), "Aged care"),
    (("plumb", "electric", "builder", "carpentr", "roofing", "landscap"), "Trades"),
    (("construction", "civil works", "earthmoving"), "Construction"),
    (("car dealer", "automotive", "mechanic", "smash repair"), "Automotive"),
    (("restaurant", "cafe", "brewery", "catering", "venue hire"), "Hospitality"),
    (("tourism", "accommodation", "holiday park", "caravan park"), "Tourism"),
    (("school", "college", "tutoring", "childcare", "early learning"), "Education"),
    (("winery", "farm", "agricultur", "livestock", "irrigation"), "Agriculture"),
    (("mining services", "drilling", "quarry"), "Mining services"),
    (("insurance broker", "insurance"), "Insurance"),
    (("gym", "fitness", "personal training"), "Fitness"),
]


def guess_industry(*texts: str | None) -> str:
    blob = " ".join(t for t in texts if t).lower()
    for keywords, label in INDUSTRY_KEYWORDS:
        if any(k in blob for k in keywords):
            return label
    return ""


def _fetch(client: httpx.Client, url: str) -> str:
    try:
        r = client.get(url)
        if r.status_code >= 400 or "html" not in r.headers.get("content-type", "html"):
            return ""
        return r.text
    except httpx.HTTPError:
        return ""


def enrich_from_website(website: str) -> dict[str, Any]:
    """
    Scrape a business website for contact details.

    Returns only the fields it actually found, so the result can be merged
    straight over an existing record without blanking anything.
    """
    url = normalise_url(website)
    if not url:
        return {}

    found: dict[str, Any] = {}
    pages: list[str] = []
    with httpx.Client(
        timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    ) as client:
        home = _fetch(client, url)
        if not home:
            return {"enrich_error": "could not fetch site"}
        pages.append(home)

        # Prefer a contact page that the homepage actually links to.
        linked = re.findall(r'href=["\']([^"\']*contact[^"\']*)["\']', home, re.I)[:2]
        candidates = [normalise_url(l) if l.startswith("http") else url.rstrip("/") + "/" + l.lstrip("/")
                      for l in linked]
        candidates += [url.rstrip("/") + p for p in CONTACT_PATHS[:2]]
        for candidate in dict.fromkeys(c for c in candidates if c)[:3]:
            page = _fetch(client, candidate)
            if page:
                pages.append(page)
                if find_emails(page):
                    break

    blob = "\n".join(pages)

    emails = find_emails(blob)
    if emails:
        # Prefer an address on the business's own domain over a gmail.
        site_domain = domain_of(url)
        on_domain = [e for e in emails if e.endswith("@" + site_domain)]
        found["email"] = (on_domain or emails)[0]
        found["all_emails"] = emails[:5]

    phone = find_phone(strip_tags(blob))
    if phone:
        found["phone"] = phone

    for key, pattern in SOCIAL_PATTERNS.items():
        match = pattern.search(blob)
        if match:
            found[key] = match.group(0)

    desc_match = _META_DESC_RE.search(pages[0])
    title_match = _TITLE_RE.search(pages[0])
    title = strip_tags(title_match.group(1)) if title_match else ""
    if desc_match:
        found["description"] = truncate(strip_tags(desc_match.group(1)))
    elif title:
        found["description"] = truncate(title)

    industry = guess_industry(title, found.get("description"), strip_tags(blob)[:4000])
    if industry:
        found["industry"] = industry

    found["website"] = url
    found["domain"] = domain_of(url)
    return found


def lookup_abn(name_or_abn: str) -> dict[str, Any]:
    """
    Australian Business Register lookup. Optional: returns {} unless ABR_GUID
    is set. Gives us the legal entity name and ABN for a trading name.
    """
    query = (name_or_abn or "").strip()
    if not query or not ABR_GUID:
        return {}
    digits = re.sub(r"\D", "", query)
    try:
        with httpx.Client(timeout=TIMEOUT) as client:
            if len(digits) == 11:
                r = client.get(
                    "https://abr.business.gov.au/json/AbnDetails.aspx",
                    params={"abn": digits, "guid": ABR_GUID},
                )
            else:
                r = client.get(
                    "https://abr.business.gov.au/json/MatchingNames.aspx",
                    params={"name": query, "maxResults": 1, "guid": ABR_GUID},
                )
            payload = r.text.strip()
    except httpx.HTTPError:
        return {}

    # The ABR returns JSONP: callback({...}).
    match = re.search(r"\((.*)\)\s*$", payload, re.S)
    if not match:
        return {}
    import json
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return {}

    if "Names" in data:
        names = data.get("Names") or []
        if not names:
            return {}
        first = names[0]
        return {
            "abn": first.get("Abn", ""),
            "legal_name": first.get("Name", ""),
            "state": first.get("State", ""),
            "postcode": first.get("Postcode", ""),
        }
    return {
        "abn": data.get("Abn", ""),
        "legal_name": data.get("EntityName", ""),
        "state": data.get("AddressState", ""),
        "postcode": data.get("AddressPostcode", ""),
    }
