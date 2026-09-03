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
from util import (clean_email, deobfuscate, domain_of, find_emails,
                  find_phone, normalise_url, strip_tags, truncate)

# A bot-shaped User-Agent gets a 403 from Cloudflare and most WAFs before a
# single byte of the page is served, which is the difference between finding
# an address and reporting the site has none.
USER_AGENT = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)
ACCEPT_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-AU,en;q=0.9",
}
TIMEOUT = httpx.Timeout(7.0, connect=4.0)

# Pages most likely to carry a real email address, best first.
CONTACT_PATHS = ("/contact", "/contact-us", "/contactus", "/get-in-touch",
                 "/about", "/about-us", "/our-team", "/team")

# Links worth following from the homepage — brokers and trades put the address
# on a "meet the team" or "enquiries" page as often as on /contact.
_LINK_RE = re.compile(
    r'href=["\']([^"\']*(?:contact|about|team|enquir|connect|get-in-touch)[^"\']*)["\']',
    re.I,
)
MAX_PAGES = 4

SOCIAL_PATTERNS = {
    "linkedin": re.compile(r"https?://(?:[a-z]{2,3}\.)?linkedin\.com/(?:company|in)/[A-Za-z0-9_\-%.]+", re.I),
    "facebook": re.compile(r"https?://(?:www\.)?facebook\.com/[A-Za-z0-9_\-.]+", re.I),
    "instagram": re.compile(r"https?://(?:www\.)?instagram\.com/[A-Za-z0-9_\-.]+", re.I),
}

# WordPress sites — most Australian small businesses — emit schema.org JSON-LD
# through Yoast or RankMath, and a LocalBusiness block often carries the email
# even when the visible page shows only a contact form.
_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)


def emails_from_jsonld(html: str) -> list[str]:
    """Pull any "email" value out of a page's structured data."""
    import json

    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() == "email" and isinstance(value, str):
                    found.append(value.replace("mailto:", ""))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for block in _JSONLD_RE.findall(html or ""):
        try:
            walk(json.loads(block.strip()))
        except (json.JSONDecodeError, RecursionError):
            continue
    return [e for e in (clean_email(f) for f in found) if e]


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


def _fetch(client: httpx.Client, url: str, blocked: list[int] | None = None) -> str:
    try:
        r = client.get(url)
        if r.status_code in (401, 403, 429) and blocked is not None:
            blocked.append(r.status_code)
        if r.status_code >= 400 or "html" not in r.headers.get("content-type", "html"):
            return ""
        # Unhide anything the page obfuscated before anyone scans it.
        return deobfuscate(r.text)
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
    blocked: list[int] = []
    site_domain = domain_of(url)

    with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers=ACCEPT_HEADERS) as client:
        home = _fetch(client, url, blocked)
        if not home:
            # Nothing served. A dead domain, a blocked bot, or — the reason this
            # is recorded rather than shrugged off — a website that was never
            # real. Either way it is not somewhere to send a prospect.
            return {"enrich_error": "could not fetch site", "website_status": "unreachable"}
        pages.append(home)

        # Prefer pages the homepage actually links to, then fall back to the
        # usual paths. Same-host links only, so an offsite link can't send us
        # crawling someone else's site.
        candidates: list[str] = []
        for link in _LINK_RE.findall(home)[:8]:
            absolute = link if link.startswith("http") else url.rstrip("/") + "/" + link.lstrip("/")
            absolute = normalise_url(absolute)
            if absolute and domain_of(absolute) == site_domain:
                candidates.append(absolute)
        candidates += [url.rstrip("/") + path for path in CONTACT_PATHS]

        for candidate in list(dict.fromkeys(c for c in candidates if c and c != url))[:MAX_PAGES]:
            page = _fetch(client, candidate, blocked)
            if not page:
                continue
            pages.append(page)
            # Stop as soon as we have an address on the business's own domain —
            # anything else is worth another page or two to try to better.
            if any(e.endswith("@" + site_domain) for e in find_emails(page)):
                break

    blob = "\n".join(pages)

    # Structured data first: when a site publishes its address there it is the
    # business's own contact address, not a stray one picked out of the markup.
    emails = emails_from_jsonld(blob)
    for candidate in find_emails(blob):
        if candidate not in emails:
            emails.append(candidate)
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
    found["website_status"] = "live"
    if not found.get("email"):
        found["enrich_note"] = (
            f"Site refused the request (HTTP {blocked[0]}) — no address readable"
            if blocked else
            f"No email published on {len(pages)} page(s) checked"
        )
    return found


def website_is_live(website: str) -> str:
    """
    Does this website serve anything? "live", "unreachable", or "" for no URL.

    Only the homepage, so it is cheap enough to run across a whole database.
    A fabricated domain has no DNS record and fails here, which is the point.
    """
    url = normalise_url(website)
    if not url:
        return ""
    try:
        with httpx.Client(timeout=TIMEOUT, follow_redirects=True,
                          headers=ACCEPT_HEADERS) as client:
            response = client.get(url)
        return "live" if response.status_code < 400 else "unreachable"
    except httpx.HTTPError:
        return "unreachable"


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
