"""
The bar a business clears without anybody looking at it.

The review queue is for judgement calls. Most of what sat in it were not
judgement calls: a well-rated business with a working website, an address to
write to and a good fit score is a prospect by every measure the app has, and
pressing Approve on four hundred of those is re-running a filter by hand.

So a business that clears all four gates is qualified where it stands, and the
queue keeps the ones that actually need a person. Four gates, checked
together — clearing three of them is not clearing the bar.

This qualifies the business, not the letter. Every draft is still read and
approved in the outbox before it goes anywhere.
"""
from __future__ import annotations

from typing import Any

import db
from config import AUTO_QUALIFY_MIN_RATING, AUTO_QUALIFY_MIN_SCORE

# Only these two. A business somebody has already decided about — contacted,
# won, lost, and disqualified above all — must never be qualified back out of
# that decision by a rule.
OPEN_STATUSES = ("new", "researching")


def unmet(business: dict[str, Any]) -> list[str]:
    """
    Which gates this business fails, in the words the page uses.

    Empty means nobody needs to look at it. It is the same list the sweep
    runs in SQL, kept here so a single record can say why it did not move.
    """
    missing = []
    rating = business.get("rating")
    if rating is None or float(rating) <= AUTO_QUALIFY_MIN_RATING:
        missing.append(f"rated above {AUTO_QUALIFY_MIN_RATING:g}"
                       f" (has {rating if rating else 'no rating'})")
    if business.get("website_status") != "live":
        missing.append("a website confirmed live"
                       f" (is {business.get('website_status') or 'unchecked'})")
    if not (business.get("email") or "").strip():
        missing.append("a contact address")
    score = int(business.get("fit_score") or 0)
    if score <= AUTO_QUALIFY_MIN_SCORE:
        missing.append(f"a fit score above {AUTO_QUALIFY_MIN_SCORE} (has {score})")
    return missing


def clears_the_bar(business: dict[str, Any]) -> bool:
    """All four gates, and a status nobody has decided about yet."""
    return (business.get("status") in OPEN_STATUSES
            and not business.get("do_not_contact")
            and not unmet(business))


def qualify_one(business_id: int) -> bool:
    """Qualify this business if it clears the bar. True if it moved."""
    business = db.get_business(business_id)
    if not business or not clears_the_bar(business):
        return False
    db.update_business(business_id, {"status": "qualified"})
    db.log_activity(business_id, "qualified",
                    "Clears the bar on its own: rated above "
                    f"{AUTO_QUALIFY_MIN_RATING:g}, website live, contact address, "
                    f"fit score {business.get('fit_score')}")
    return True


def sweep(limit: int = 0) -> dict[str, Any]:
    """
    Qualify everything already in the database that clears the bar.

    One query rather than a pass per record: the gates are all stored columns,
    so the whole backlog moves in a single statement and the page does not sit
    there for four hundred round trips.
    """
    moved = db.qualify_clearing_the_bar(
        min_rating=AUTO_QUALIFY_MIN_RATING,
        min_score=AUTO_QUALIFY_MIN_SCORE,
        statuses=OPEN_STATUSES,
        limit=limit,
    )
    return {"qualified": len(moved), "businesses": moved}


def waiting(**filters: Any) -> int:
    """How many businesses would move if the sweep ran now."""
    return db.count_clearing_the_bar(
        min_rating=AUTO_QUALIFY_MIN_RATING,
        min_score=AUTO_QUALIFY_MIN_SCORE,
        statuses=OPEN_STATUSES,
        **filters,
    )
