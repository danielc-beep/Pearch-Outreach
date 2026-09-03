"""Small shared helpers: normalising the messy fields every source produces."""
from __future__ import annotations

import html as html_module
import re
from urllib.parse import urlparse

AU_STATES = {"NSW", "VIC", "QLD", "SA", "WA", "TAS", "NT", "ACT"}

# Addresses like "12 Hunter St, Newcastle NSW 2300, Australia"
_ADDRESS_RE = re.compile(
    r"(?P<suburb>[A-Za-z' \-]+?)\s+(?P<state>NSW|VIC|QLD|SA|WA|TAS|NT|ACT)\s+(?P<postcode>\d{4})",
    re.IGNORECASE,
)
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
# Candidate phone-ish runs; clean_phone() decides which are really valid.
_AU_PHONE_RE = re.compile(r"(?:\+?61|\(0\d\)|\b0\d)[\d\s\-().]{7,16}")

# Addresses harvested from a page that are never a person or a business inbox.
JUNK_EMAIL_PREFIXES = ("noreply", "no-reply", "donotreply", "example", "your@", "email@example")
JUNK_EMAIL_DOMAINS = ("sentry.io", "wixpress.com", "example.com", "domain.com", "godaddy.com")


def normalise_url(url: str | None) -> str:
    """Add a scheme, strip tracking cruft. Returns '' for junk input."""
    url = (url or "").strip()
    if not url:
        return ""
    if not url.startswith(("http://", "https://")):
        url = "https://" + url.lstrip("/")
    parsed = urlparse(url)
    if not parsed.netloc or "." not in parsed.netloc:
        return ""
    path = parsed.path.rstrip("/")
    return f"{parsed.scheme}://{parsed.netloc}{path}"


def domain_of(url: str | None) -> str:
    """Bare registrable-ish domain, lowercase, no www. Our dedupe key."""
    url = normalise_url(url)
    if not url:
        return ""
    host = urlparse(url).netloc.lower().split(":")[0]
    return host[4:] if host.startswith("www.") else host


def clean_email(value: str | None) -> str:
    value = (value or "").strip().lower().rstrip(".,;:")
    if not value or not _EMAIL_RE.fullmatch(value):
        return ""
    if value.startswith(JUNK_EMAIL_PREFIXES):
        return ""
    if any(value.endswith(d) for d in JUNK_EMAIL_DOMAINS):
        return ""
    return value


def find_emails(text: str) -> list[str]:
    """Every plausible email in a blob of HTML, best-first, deduped."""
    seen: dict[str, None] = {}
    for raw in _EMAIL_RE.findall(text or ""):
        email = clean_email(raw)
        if email and email not in seen:
            seen[email] = None
    ordered = sorted(seen, key=lambda e: (not e.split("@")[0] in
                                          ("info", "hello", "enquiries", "admin", "contact", "office"), e))
    return ordered


def clean_phone(value: str | None) -> str:
    """Normalise an Australian number to '02 4000 0000' / '0400 000 000'."""
    digits = re.sub(r"[^\d+]", "", value or "")
    if digits.startswith("+61"):
        digits = "0" + digits[3:]
    elif digits.startswith("61") and len(digits) == 11:
        digits = "0" + digits[2:]
    digits = re.sub(r"\D", "", digits)
    if len(digits) != 10 or not digits.startswith("0"):
        return ""
    if digits.startswith("04"):
        return f"{digits[:4]} {digits[4:7]} {digits[7:]}"
    return f"{digits[:2]} {digits[2:6]} {digits[6:]}"


def find_phone(text: str) -> str:
    """First valid Australian number in a blob of text, normalised."""
    for candidate in _AU_PHONE_RE.findall(text or ""):
        phone = clean_phone(candidate)
        if phone:
            return phone
        # A run can swallow the digits that follow it; retry on the prefix.
        digits = re.sub(r"\D", "", candidate)
        if len(digits) > 10:
            phone = clean_phone(digits[:10])
            if phone:
                return phone
    return ""


def parse_address(address: str | None) -> dict[str, str]:
    """Pull suburb / state / postcode out of a one-line Australian address."""
    out = {"suburb": "", "state": "", "postcode": ""}
    if not address:
        return out
    match = _ADDRESS_RE.search(address)
    if match:
        out["suburb"] = match.group("suburb").strip().title()
        out["state"] = match.group("state").upper()
        out["postcode"] = match.group("postcode")
    return out


# Cloudflare replaces every address on a page with an encoded blob, so a site
# behind it looks like it has no email at all. The encoding is a single-byte
# XOR whose key is the first byte — trivial to reverse, and worth doing: it is
# one of the most common reasons a real address goes unfound.
_CFEMAIL_RE = re.compile(r'data-cfemail=["\']([0-9a-fA-F]{8,})["\']')

# "info [at] acme [dot] com [dot] au" and its many cousins.
_OBFUSCATED_RE = re.compile(
    r"([A-Za-z0-9._%+\-]+)\s*[\[(\{]\s*(?:at|@)\s*[\])\}]\s*"
    r"((?:[A-Za-z0-9\-]+\s*[\[(\{]\s*(?:dot|\.)\s*[\])\}]\s*)+[A-Za-z]{2,})",
    re.I,
)


def _decode_cfemail(encoded: str) -> str:
    """Reverse Cloudflare's data-cfemail encoding."""
    try:
        key = int(encoded[:2], 16)
        return "".join(
            chr(int(encoded[i:i + 2], 16) ^ key) for i in range(2, len(encoded), 2)
        )
    except ValueError:
        return ""


def deobfuscate(html: str) -> str:
    """
    Make hidden email addresses visible to the scanner.

    Sites hide addresses from scrapers three common ways — Cloudflare's
    encoding, HTML entities, and "name [at] domain [dot] com" — and a plain
    regex over raw HTML misses all three.
    """
    text = html or ""

    decoded = [_decode_cfemail(match) for match in _CFEMAIL_RE.findall(text)]
    text = html_module.unescape(text)

    def unmask(match: re.Match[str]) -> str:
        domain = re.sub(r"\s*[\[(\{]\s*(?:dot|\.)\s*[\])\}]\s*", ".", match.group(2))
        return f"{match.group(1)}@{domain}"

    text = _OBFUSCATED_RE.sub(unmask, text)
    return text + "\n" + "\n".join(e for e in decoded if e)


def strip_tags(html: str) -> str:
    """Crude but dependency-free text extraction for description sniffing."""
    html = re.sub(r"(?is)<(script|style|noscript)[^>]*>.*?</\1>", " ", html or "")
    text = re.sub(r"(?s)<[^>]+>", " ", html)
    return re.sub(r"\s+", " ", text).strip()


def truncate(text: str, limit: int = 280) -> str:
    text = (text or "").strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"
