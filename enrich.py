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
import socket
import time
from typing import Any

import httpx

import aeo
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
# Generous, because these are small-business sites on shared hosting reached
# from a server that is not in Australia. Seven seconds looked fine locally
# and marked live sites dead in production. The batch is kept bounded by a
# per-business budget instead — see PAGE_BUDGET.
TIMEOUT = httpx.Timeout(20.0, connect=8.0)

# The most time one business may take, however many pages it has. Without it
# a single slow site holds a worker for a minute and the batch outlives the
# request that asked for it.
PAGE_BUDGET = 14.0

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


# What a website check can honestly conclude. Four answers, not two — the
# difference between them is the difference between "bin this record" and
# "we could not read it today".
LIVE = "live"                 # HTML came back. Everything works.
BLOCKED = "blocked"           # A server answered and refused us. The site is up.
UNREACHABLE = "unreachable"   # The name does not resolve. Nothing is there.
UNKNOWN = "error"             # Timeout, TLS, a proxy in the way. We do not know.


def _stamp() -> str:
    """A UTC timestamp. Local to this module so enrich does not import db."""
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _host_variants(url: str) -> list[str]:
    """The URL as held, then with www flipped. A record easily carries the
    variant that happens not to be served."""
    host = domain_of(url)
    if host.startswith("www."):
        return [url, url.replace("www.", "", 1)]
    return [url, url.replace("://", "://www.", 1)]


def _name_resolves(host: str) -> bool:
    """
    Does this domain exist at all? The one question with a definite answer.

    Everything else about an unanswered request is ambiguous — a firewall, a
    slow host, a proxy between us and them — but a name that does not resolve
    is a name nobody has registered or pointed anywhere. It is the only
    evidence good enough to call a business's website dead.
    """
    try:
        socket.getaddrinfo(host, None)
        return True
    except socket.gaierror:
        return False
    except OSError:
        # Cannot tell. Not the business's fault, so not held against them.
        return True


def probe(client: httpx.Client, url: str) -> tuple[str, str]:
    """
    Visit a site and report what actually happened. Returns (status, html).

    The rule that matters: a server answering at all — even with a 403, even
    with a Cloudflare challenge — means the domain resolves and somebody is
    running a web server on it, which is exactly what a made-up domain cannot
    do. Plenty of Australian small-business sites sit behind a WAF that
    answers a datacentre IP with 403, and calling those dead is what put real,
    working businesses on a list to be deleted.
    """
    answered = False
    for candidate in _host_variants(url):
        for _attempt in range(2):        # one retry: a single timeout is not proof
            try:
                response = client.get(candidate)
            except httpx.HTTPError:
                continue
            answered = True
            content_type = response.headers.get("content-type", "html")
            if response.status_code < 400 and "html" in content_type:
                return LIVE, deobfuscate(response.text)
            break                        # answered, just not with a page we can read

    if answered:
        return BLOCKED, ""
    if not _name_resolves(domain_of(url)):
        return UNREACHABLE, ""
    # It resolves and nothing came back. That is a bad afternoon, not a dead
    # business, so nothing is recorded and it will be asked again.
    return UNKNOWN, ""


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
    deadline = time.monotonic() + PAGE_BUDGET

    with httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers=ACCEPT_HEADERS) as client:
        status, home = probe(client, url)
        if status != LIVE:
            # No page to read. What that means depends entirely on why, and
            # this used to report all three as a dead website — which is how a
            # working electrician behind a WAF ended up on a list to delete.
            if status == UNKNOWN:
                return {"enrich_error": "could not reach site"}
            return {"enrich_error": f"site {status}", "website_status": status}
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
            # The homepage is worth waiting for; the fifth contact page is not.
            # Without this one slow site holds a worker for the whole batch.
            if time.monotonic() > deadline:
                break
            page = _fetch(client, candidate, blocked)
            if not page:
                continue
            # Keep the first contact page that actually served, whether or not
            # an address is found on it. Where the scraper fails, a person can
            # open that page and read the address off it in ten seconds —
            # throwing the URL away made them go and find it themselves.
            if "contact_url" not in found and "contact" in candidate.lower():
                found["contact_url"] = candidate
            pages.append(page)
            # Stop as soon as we have an address on the business's own domain —
            # anything else is worth another page or two to try to better.
            if any(e.endswith("@" + site_domain) for e in find_emails(page)):
                break

    if "contact_url" not in found and len(pages) > 1:
        found["contact_url"] = url          # at least the homepage served

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

    # What an answer engine can make of the same pages. Free: the HTML is
    # already downloaded and this is the half of the fit score that says
    # whether there is anything worth selling them.
    found.update(aeo.audit(pages))
    found["aeo_audited_at"] = _stamp()

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
    What the website check concluded. One of live, blocked, unreachable, error.

    A thin wrapper on probe(), because the two ways this app looks at a
    website used to reason differently and the one that ran during
    prospecting was the wrong one.
    """
    url = normalise_url(website)
    if not url:
        return ""
    with httpx.Client(timeout=TIMEOUT, follow_redirects=True,
                      headers=ACCEPT_HEADERS) as client:
        return probe(client, url)[0]


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
