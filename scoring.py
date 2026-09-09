"""
Fit scoring.

A 0-100 score answering one question: how worth an outreach email is this
business? Blunt and readable on purpose — the job is to sort thousands, not
to be a model.

Two rules keep it honest, and both were broken:

1. A signal that never varies is not a signal. Prospecting refuses anything
   under four stars, so awarding points for "four stars or better" handed the
   same eight points to every record in the database and told you nothing
   about which of them to call first. Rating and review count are graded now,
   so they discriminate inside the pool that actually exists.

2. Score against the data you have, not the data you had. "In an ACM masthead
   region" was matched against ten heartland names, which between them miss
   sixty of the sixty-seven towns an ACM masthead actually covers — a business
   in Albury, Bathurst, Burnie or Bega scored zero for it while being aligned
   to an ACM masthead on the same screen. It is matched against the masthead
   list now, which is the real footprint.
"""
from __future__ import annotations

import hashlib
import json
from typing import Any

import config
import mastheads

VERSION = ""      # set at the bottom of this file, from the card itself

# (points, label) per signal. The maximum reachable is 100.
WEIGHTS = {
    "email":      (22, "Contactable — email on file"),
    "website":    (14, "Has a website we can audit"),
    "industry":   (18, "Industry is in the ICP"),
    "masthead":   (14, "Covered by an ACM masthead"),
    "phone":      (6,  "Phone number on file"),
    "address":    (6,  "Physical address — a real local business"),
    "social":     (5,  "Active on social"),
}

# Graded, because everything here already cleared the four-star floor. The
# question is no longer "is it well reviewed" but "how well, and by how many".
RATING_BANDS = ((4.8, 8, "Outstanding rating"),
                (4.5, 6, "Strong rating"),
                (4.2, 4, "Good rating"),
                (0.0, 2, "Above the floor"))

REVIEW_BANDS = ((200, 7, "Hundreds of reviews"),
                (100, 6, "Well established"),
                (40,  4, "A solid review history"),
                (15,  2, "Some reviews"),
                (5,   1, "A handful of reviews"))

PENALTIES = {
    "do_not_contact": (-100, "Marked do-not-contact"),
    "no_website":     (-10,  "No website — little for us to work with"),
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


def score_business(b: dict[str, Any]) -> tuple[int, list[str]]:
    """Return (score, reasons). Reasons are shown verbatim in the UI."""
    score = 0
    reasons: list[str] = []

    def award(key: str, ok: bool) -> None:
        nonlocal score
        if ok:
            points, label = WEIGHTS[key]
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

    award("email", bool((b.get("email") or "").strip()))
    award("website", bool((b.get("website") or "").strip()))
    award("industry", _matches_icp_industry(b.get("industry") or b.get("category")))
    award("masthead", _covered_by_a_masthead(b))
    award("phone", bool((b.get("phone") or "").strip()))
    award("address", bool((b.get("address") or "").strip()))
    award("social", any(b.get(k) for k in ("linkedin", "facebook", "instagram")))

    rating = b.get("rating")
    award_graded(rating, RATING_BANDS, f"{float(rating):.1f}★" if rating else "")
    reviews = b.get("review_count")
    award_graded(reviews, REVIEW_BANDS, f"{int(reviews)} reviews" if reviews else "")

    if b.get("do_not_contact"):
        points, label = PENALTIES["do_not_contact"]
        score += points
        reasons.append(f"{points} {label}")
    elif not (b.get("website") or "").strip():
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
        "weights": WEIGHTS,
        "rating": RATING_BANDS,
        "reviews": REVIEW_BANDS,
        "penalties": PENALTIES,
        "industries": sorted(config.ICP["industries"]),
        "min_rating": config.ICP["min_rating"],
        "min_reviews": config.ICP["min_reviews"],
    }, sort_keys=True, default=str)
    return hashlib.sha256(payload.encode()).hexdigest()[:12]


VERSION = "card-" + fingerprint()
