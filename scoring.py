"""
Fit scoring.

A 0-100 score answering one question: how worth an outreach email is this
business? The weights are deliberately blunt and readable — the point is to
sort a list of thousands, not to be a model. Tune the ICP in config.py.
"""
from __future__ import annotations

from typing import Any

from config import ICP

# (points, label) per signal. The maximum reachable is 100.
WEIGHTS = {
    "email":      (22, "Contactable — email on file"),
    "website":    (14, "Has a website we can audit"),
    "industry":   (18, "Industry is in the ICP"),
    "region":     (14, "In an ACM masthead region"),
    "phone":      (6,  "Phone number on file"),
    "address":    (6,  "Physical address — a real local business"),
    "rating":     (8,  "Well reviewed"),
    "reviews":    (7,  "Enough reviews to be established"),
    "social":     (5,  "Active on social"),
}

PENALTIES = {
    "do_not_contact": (-100, "Marked do-not-contact"),
    "no_website":     (-10,  "No website — little for us to work with"),
}


def _matches_icp_industry(value: str | None) -> bool:
    if not value:
        return False
    v = value.lower()
    return any(term in v for term in ICP["industries"])


def _matches_icp_region(region: str | None, suburb: str | None = None) -> bool:
    haystack = f"{region or ''} {suburb or ''}".lower()
    if not haystack.strip():
        return False
    return any(r.lower() in haystack for r in ICP["regions"])


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

    award("email", bool((b.get("email") or "").strip()))
    award("website", bool((b.get("website") or "").strip()))
    award("industry", _matches_icp_industry(b.get("industry") or b.get("category")))
    award("region", _matches_icp_region(b.get("region"), b.get("suburb")))
    award("phone", bool((b.get("phone") or "").strip()))
    award("address", bool((b.get("address") or "").strip()))

    rating = b.get("rating")
    award("rating", rating is not None and float(rating or 0) >= ICP["min_rating"])
    reviews = b.get("review_count")
    award("reviews", reviews is not None and int(reviews or 0) >= ICP["min_reviews"])
    award("social", any(b.get(k) for k in ("linkedin", "facebook", "instagram")))

    if b.get("do_not_contact"):
        points, label = PENALTIES["do_not_contact"]
        score += points
        reasons.append(f"{points} {label}")
    elif not (b.get("website") or "").strip():
        points, label = PENALTIES["no_website"]
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
