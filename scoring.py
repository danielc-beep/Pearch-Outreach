"""
Fit scoring.

A 0-100 score answering one question: of the businesses we could email, which
should we ring first? Blunt and readable on purpose — the job is to sort
thousands, not to be a model.

Three rules keep it honest, and all three were broken at some point:

1. A signal that never varies is not a signal. Prospecting refuses anything
   under four stars, so awarding points for "four stars or better" handed the
   same eight points to every record in the database. Rating and review count
   are graded, so they discriminate inside the pool that actually exists.

2. Score against the data you have, not the data you had. "In an ACM masthead
   region" was matched against ten heartland names, which between them miss
   sixty of the sixty-seven towns an ACM masthead actually covers. It is
   matched against the masthead list now, which is the real footprint.

3. Score the thing you are selling. This card used to be a completeness score
   wearing a fit score's name: 62 of its 100 points went to having an email,
   a website, a phone number and an address. Those are now gates a business
   clears before it is a prospect at all — so among qualified prospects every
   one of them scored the same, and the number could not tell you who to ring.

   What we sell is a citation inside Google's AI Mode and AI Overviews. So the
   card is in two halves that multiply in practice: a reputation worth citing,
   and a gap between that reputation and what an answer engine can read. A
   business with three hundred five-star reviews and no structured data is the
   best prospect on the list, because everything it is missing is what we sell.
   A polished site with eleven reviews is not.

   The gap comes from aeo.audit(), read out of HTML enrichment already
   downloads. Where a site has not been audited the opportunity is unknown,
   not large: the card says so and awards the middle of the range rather than
   pretending either way.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import aeo
import config
import mastheads

VERSION = ""      # set at the bottom of this file, from the card itself

# ---------- Half one: is this a reputation worth getting cited? (50) ----------
# Everything here is about the business as the town sees it. A citation is
# worth money to a firm people are already looking for and worth nothing to
# one nobody asks about.

# Graded, because everything here already cleared the four-star floor. The
# question is no longer "is it well reviewed" but "how well, and by how many".
RATING_BANDS = ((4.8, 10, "Outstanding rating"),
                (4.5, 8, "Strong rating"),
                (4.2, 5, "Good rating"),
                (0.0, 2, "Above the floor"))

# The best single proxy we have for size, age and how much searching people
# already do for them — and the number the pitch opens on.
REVIEW_BANDS = ((300, 20, "Hundreds of reviews — a name in its town"),
                (150, 17, "A long review history"),
                (75,  14, "Well established"),
                (40,  10, "A solid review history"),
                (15,  6,  "Some reviews"),
                (5,   3,  "A handful of reviews"))

REACH = {
    "industry": (12, "Industry is in the ICP"),
    "masthead": (8,  "Covered by an ACM masthead"),
}

# ---------- Half two: is there a gap we fill? (40) ----------
# Read off the site by aeo.audit(). Each one is a thing the business does not
# have that we sell, so a high score here is not a criticism of them — it is
# the reason to call.
GAPS = {
    "no_schema":   (16, "No structured data — an answer engine has to guess what they are"),
    "thin_schema": (8,  "Structured data, but nothing that says local business"),
    "no_faq":      (10, "No questions answered on the site"),
    "no_blog":     (8,  "Publishes nothing an answer engine could cite"),
    "no_meta":     (3,  "No meta description on the homepage"),
    "thin_site":   (3,  "Too few words on the site to be quoted from"),
}
# The most a site can be missing. Not the sum of the table: a site either has
# no structured data or has some that is not local, never both, so the schema
# slot counts once.
GAP_TOTAL = (max(GAPS["no_schema"][0], GAPS["thin_schema"][0])
             + GAPS["no_faq"][0] + GAPS["no_blog"][0]
             + GAPS["no_meta"][0] + GAPS["thin_site"][0])

# What to award when nobody has looked at the site yet. Half, and labelled —
# an unaudited site has an unknown gap, not a large one, and the last time
# this app treated "we could not read it" as a finding it told a working
# electrician their website was dead.
UNAUDITED = GAP_TOTAL // 2

# ---------- Half three: can we act on it at all? (10) ----------
# What is left of the old completeness card. Deliberately small: these are
# gates now, so among qualified prospects they are constant, and a constant
# cannot sort anything.
WORKABLE = {
    "email":   (6, "Contactable — email on file"),
    "contact": (4, "Phone or address on file"),
}

PENALTIES = {
    "do_not_contact": (-100, "Marked do-not-contact"),
    "no_website":     (-30,  "No website — nothing for us to work on"),
    "dead_website":   (-8,   "Website did not respond when we checked"),
}


def _matches_icp_industry(value: str | None) -> bool:
    if not value:
        return False
    v = value.lower()
    return any(term in v for term in config.ICP["industries"])


def _covered_by_a_masthead(b: dict[str, Any]) -> bool:
    """
    Whether an ACM title covers this business.

    The stored alignment first, then a match on its town — a record can be
    scored before anything has aligned it, and "we have not got to it yet" is
    not the same as "no paper covers them".
    """
    if (b.get("masthead") or "").strip() in mastheads.BY_SITE:
        return True
    return bool(mastheads.match(b.get("suburb"), b.get("region")))


def _graded(value: float | int | None, bands: tuple) -> tuple[int, str]:
    if value is None:
        return 0, ""
    for floor, points, label in bands:
        if value >= floor:
            return points, label
    return 0, ""


def gap_points(b: dict[str, Any]) -> tuple[int, list[str]]:
    """
    How much of what we sell this business is missing, and which parts.

    Returns the middle of the range for a site nobody has read yet, and says
    so. Guessing high would put unaudited records at the top of the call list
    on no evidence; guessing low would bury them.
    """
    # Audited AND actually read. A stamp on its own only means we went and
    # looked; a site that refused us has an unknown gap, and awarding the full
    # score for one would be the website-audit bug all over again — treating
    # "we could not read it" as a finding about the business.
    if not (b.get("aeo_audited_at") and int(b.get("aeo_pages") or 0)):
        return UNAUDITED, [f"+{UNAUDITED} Site not read yet — opportunity unknown"]

    score, reasons = 0, []

    def award(key: str) -> None:
        nonlocal score
        points, label = GAPS[key]
        score += points
        reasons.append(f"+{points} {label}")

    types = [t for t in (b.get("aeo_schema") or "").split(",") if t]
    if not types:
        award("no_schema")
    elif not any(t in aeo.LOCAL_TYPES for t in types):
        award("thin_schema")
    if not b.get("aeo_faq"):
        award("no_faq")
    if not b.get("aeo_blog"):
        award("no_blog")
    if not b.get("aeo_meta"):
        award("no_meta")
    if int(b.get("aeo_words") or 0) < aeo.THIN_SITE_WORDS:
        award("thin_site")

    if not reasons:
        reasons.append("+0 Site is already in good order — nothing obvious to fix")
    return score, reasons


def score_business(b: dict[str, Any]) -> tuple[int, list[str]]:
    """Return (score, reasons). Reasons are shown verbatim in the UI."""
    score = 0
    reasons: list[str] = []

    def award(table: dict[str, tuple[int, str]], key: str, ok: bool) -> None:
        nonlocal score
        if ok:
            points, label = table[key]
            score += points
            reasons.append(f"+{points} {label}")

    def award_graded(value: Any, bands: tuple, suffix: str) -> None:
        nonlocal score
        try:
            number = float(value) if value is not None else None
        except (TypeError, ValueError):
            return
        points, label = _graded(number, bands)
        if points:
            score += points
            reasons.append(f"+{points} {label} ({suffix})")

    # A business marked do-not-contact is not a prospect at any score, and
    # nothing below is worth computing for it.
    if b.get("do_not_contact"):
        points, label = PENALTIES["do_not_contact"]
        return 0, [f"{points} {label}"]

    # Half one — the reputation.
    reviews = b.get("review_count")
    award_graded(reviews, REVIEW_BANDS, f"{int(reviews)} reviews" if reviews else "")
    rating = b.get("rating")
    award_graded(rating, RATING_BANDS, f"{float(rating):.1f}★" if rating else "")
    award(REACH, "industry", _matches_icp_industry(b.get("industry") or b.get("category")))
    award(REACH, "masthead", _covered_by_a_masthead(b))

    # Half two — the gap, but only for a business with a site to fix. There is
    # no AEO work to sell somebody with nothing to optimise, so the absence of
    # a website is a disqualifier rather than an opportunity.
    has_website = bool((b.get("website") or "").strip())
    if has_website:
        points, why = gap_points(b)
        score += points
        reasons.extend(why)

    # Half three — reachability.
    award(WORKABLE, "email", bool((b.get("email") or "").strip()))
    award(WORKABLE, "contact", bool((b.get("phone") or "").strip()
                                    or (b.get("address") or "").strip()))

    if not has_website:
        points, label = PENALTIES["no_website"]
        score += points
        reasons.append(f"{points} {label}")
    elif b.get("website_status") == "unreachable":
        # The domain does not resolve — nobody has registered it or pointed it
        # anywhere. That is the only website result worth marking a business
        # down for. "blocked" means a server answered and refused us, which
        # says something about their firewall and nothing about their
        # business, and "error" means we could not tell; docking either one
        # was scoring a working electrician down for having a WAF.
        points, label = PENALTIES["dead_website"]
        score += points
        reasons.append(f"{points} {label}")

    return max(0, min(100, score)), reasons


def band(score: int) -> str:
    """Bucket a score for the UI pill."""
    if score >= 75:
        return "hot"
    if score >= 55:
        return "warm"
    if score >= 35:
        return "cool"
    return "cold"


def apply_score(b: dict[str, Any]) -> dict[str, Any]:
    """Score a business dict in place and return it — used by every source."""
    score, reasons = score_business(b)
    b["fit_score"] = score
    b["score_reasons"] = reasons
    return b


# The scorecard's own fingerprint.
#
# Stored scores carrying a different one are recomputed on the next start,
# because a fixed scorecard that never reaches the records already in the
# database has fixed nothing. Derived rather than typed on purpose: a version
# string you have to remember to bump is a version string that eventually does
# not get bumped, and then the scores are wrong again with nothing to show it.
# Edit a weight, a band, a penalty or the ICP, and this changes by itself.
def fingerprint() -> str:
    payload = json.dumps({
        "reach": REACH,
        "gaps": GAPS,
        "unaudited": UNAUDITED,
        "workable": WORKABLE,
        "rating": RATING_BANDS,
        "reviews": REVIEW_BANDS,
        "penalties": PENALTIES,
        "thin_site": aeo.THIN_SITE_WORDS,
        "local_types": sorted(aeo.LOCAL_TYPES),
        "industries": sorted(config.ICP["industries"]),
        "min_rating": config.ICP["min_rating"],
        "min_reviews": config.ICP["min_reviews"],
    }, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


VERSION = "card-" + fingerprint()
