"""
What an answer engine sees when it looks at a business's website.

The fit score used to be a completeness score wearing a fit score's name: it
counted how many fields we had filled in. Every one of those — email, phone,
address, a website — is now a gate the business has to clear before it is a
prospect at all, so awarding points for them again told us nothing about which
of six hundred qualified prospects to ring first.

What we sell is a citation inside Google's AI Mode and AI Overviews. So the
question the score should answer is the one the pitch answers: does this
business have a reputation worth citing, and is there a gap between that
reputation and what an answer engine can actually read about them?

That gap is measurable, and we were throwing the evidence away. Enrichment
already downloads the homepage and several inside pages looking for an email
address. This reads the same HTML for the things that decide whether a
business gets quoted:

  structured data  — schema.org markup is how a page tells a machine what it
                     is. No LocalBusiness block and the engine is guessing.
  FAQ content      — question-and-answer pages are the single most quoted
                     shape on the web, because they match how people ask.
  published pages  — a business that never publishes has nothing to cite.
  a meta description, real headings, actual words on the page.

Nothing here is a judgement about the business. A shop with three hundred
five-star reviews and no schema markup is not a bad business — it is the best
prospect on the list, because everything it is missing is what we sell.

One rule throughout: a site we could not read has an UNKNOWN gap, not a big
one. Absence of evidence is not evidence, and the last time this app forgot
that it told a working electrician their website was dead.
"""
from __future__ import annotations

import json
import re
from typing import Any

from util import strip_tags

# schema.org types worth having. A page can carry dozens; these are the ones
# that change whether an answer engine can quote it.
LOCAL_TYPES = ("localbusiness", "organization", "store", "professionalservice",
               "homeandconstructionbusiness", "medicalbusiness", "restaurant",
               "automotivebusiness", "legalservice", "financialservice",
               "realestateagent", "dentist", "physician", "electrician",
               "plumber", "generalcontractor", "roofingcontractor")
ANSWER_TYPES = ("faqpage", "question", "howto", "qapage")
DEPTH_TYPES = ("product", "service", "offer", "review", "aggregaterating",
               "article", "blogposting", "newsarticle", "breadcrumblist",
               "person", "event", "menu", "itemlist")

_JSONLD_RE = re.compile(
    r'<script[^>]+type=["\']application/ld\+json["\'][^>]*>(.*?)</script>', re.I | re.S)
_MICRODATA_RE = re.compile(r'itemtype=["\']https?://schema\.org/([A-Za-z]+)', re.I)
_META_DESC_RE = re.compile(
    r'<meta[^>]+name=["\']description["\'][^>]+content=["\']([^"\']{20,})', re.I)
_HEADING_RE = re.compile(r"<h([1-3])[^>]*>(.*?)</h\1>", re.I | re.S)

# A heading shaped like something somebody would type into a search box. These
# are what gets lifted into an answer, which is why we count them separately
# from headings in general.
_QUESTION_RE = re.compile(
    r"^\s*(?:how|what|what's|why|when|where|which|who|can|do|does|is|are|should|"
    r"will|""‘|“)\b|\?\s*$", re.I)

# Words in a link or heading that mean this site publishes something.
PUBLISHING_WORDS = ("blog", "news", "article", "guide", "resource", "insight",
                    "advice", "tips", "case stud", "knowledge", "learn",
                    "journal", "post", "update")
FAQ_WORDS = ("faq", "faqs", "frequently asked", "common questions",
             "questions we", "q&a", "your questions")

# Below this a site is a business card rather than something to be quoted from.
THIN_SITE_WORDS = 400


def schema_types(html: str) -> list[str]:
    """
    Every schema.org type the page declares, lowercased and deduplicated.

    Reads JSON-LD first — what WordPress, Yoast and Wix all emit — then falls
    back to inline microdata for the hand-built sites that still use it.
    """
    found: list[str] = []

    def walk(node: Any) -> None:
        if isinstance(node, dict):
            for key, value in node.items():
                if key.lower() == "@type":
                    if isinstance(value, str):
                        found.append(value)
                    elif isinstance(value, list):
                        found.extend(v for v in value if isinstance(v, str))
                else:
                    walk(value)
        elif isinstance(node, list):
            for item in node:
                walk(item)

    for block in _JSONLD_RE.findall(html or ""):
        try:
            walk(json.loads(block.strip()))
        except (json.JSONDecodeError, RecursionError, ValueError):
            # A broken JSON-LD block is worth knowing about but is not worth
            # failing an audit over: the rest of the page still reads.
            continue
    found.extend(_MICRODATA_RE.findall(html or ""))

    seen: list[str] = []
    for kind in found:
        kind = kind.strip().lower().lstrip("/")
        if kind and kind not in seen:
            seen.append(kind)
    return seen


def _mentions(text: str, words: tuple[str, ...]) -> bool:
    lowered = text.lower()
    return any(word in lowered for word in words)


def audit(pages: list[str]) -> dict[str, Any]:
    """
    What an answer engine can make of these pages.

    Takes the HTML enrichment has already downloaded — no extra requests, no
    extra time. `pages[0]` is the homepage; the rest are inside pages.
    """
    pages = [p for p in pages if p]
    if not pages:
        return {}

    home, blob = pages[0], "\n".join(pages)
    types = schema_types(blob)
    headings = [strip_tags(h[1]) for h in _HEADING_RE.findall(blob)]
    questions = [h for h in headings if h and _QUESTION_RE.search(h)]
    text = strip_tags(blob)

    has_answer_schema = any(t in ANSWER_TYPES for t in types)
    return {
        "aeo_schema": ",".join(types[:12]),
        # Two or more question-shaped headings is a page answering questions;
        # one is a heading that happens to end in a question mark.
        "aeo_faq": int(has_answer_schema or _mentions(blob, FAQ_WORDS)
                       or len(questions) >= 2),
        "aeo_blog": int(_mentions(blob, PUBLISHING_WORDS)),
        "aeo_meta": int(bool(_META_DESC_RE.search(home))),
        "aeo_pages": len(pages),
        "aeo_words": len(text.split()),
        "aeo_questions": len(questions),
    }


def findings(b: dict[str, Any]) -> list[str]:
    """
    What we would fix, in the order we would say it on the phone.

    This is the audit read back as the reason to call: every line is something
    the business is missing that we sell. Empty means their site is already in
    good order — which is a real answer, and a reason to pitch something else.
    """
    # Nothing to report on a site we never managed to read.
    if not (b.get("aeo_audited_at") and int(b.get("aeo_pages") or 0)):
        return []

    types = [t for t in (b.get("aeo_schema") or "").split(",") if t]
    notes = []
    if not types:
        notes.append("No structured data at all — an answer engine has to guess "
                     "what the business is, where it is and what it sells.")
    elif not any(t in LOCAL_TYPES for t in types):
        notes.append("Structured data, but nothing that says it is a local "
                     "business — no address, hours or service area a machine can read.")
    if not b.get("aeo_faq"):
        notes.append("No questions answered anywhere on the site. Question-and-answer "
                     "pages are the shape AI Overviews quote most.")
    if not b.get("aeo_blog"):
        notes.append("Nothing published — no guides, news or advice for an answer "
                     "engine to cite.")
    if not b.get("aeo_meta"):
        notes.append("No meta description on the homepage.")
    if int(b.get("aeo_words") or 0) < THIN_SITE_WORDS:
        notes.append(f"Only about {int(b.get('aeo_words') or 0)} words across the site — "
                     "too thin to be quoted from.")
    return notes
