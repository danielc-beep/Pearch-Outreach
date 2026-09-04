"""
The review queue: one business at a time, with a decision at the end of it.

Working a list means opening a record, reading it, acting, going back, and
finding your place again — six interactions per business, and no sense of
how many are left. Triage wants the opposite shape: one thing on screen,
three ways out of it, and the next one already loaded.

A business leaves the queue when a decision is made about it. Skipping is
not a decision, so a skipped business comes back next session.
"""
from __future__ import annotations

from typing import Any

import db
import mastheads
import outreach
from scoring import band

DECISIONS = ("approve", "reject", "skip")


def queue_ids(**filters: Any) -> list[int]:
    """
    The businesses awaiting a decision, best fit first.

    Ids only. The cards are fetched one at a time as the queue is worked, so
    a queue of four hundred costs one small request rather than four hundred
    records the reviewer will mostly never look at.
    """
    filters.pop("needs_review", None)
    filters.setdefault("sort", "score")
    rows, _ = db.list_businesses(needs_review=True, limit=1000, **filters)
    return [int(r["id"]) for r in rows]


def card(business_id: int) -> dict[str, Any] | None:
    """Everything the reviewer needs to decide, in one payload."""
    business = db.get_business(business_id)
    if not business:
        return None

    # The newest draft, if there is one. Anything already approved or sent
    # would have taken the business out of the queue.
    drafts = [m for m in db.list_messages(business_id=business_id) if m["status"] == "draft"]
    draft = drafts[0] if drafts else None

    standing = outreach.review_standing(business)
    return {
        "business": business,
        "band": band(int(business.get("fit_score") or 0)),
        "masthead": outreach.masthead_for(business)["name"],
        "reviews": standing["phrase"],
        "praiseworthy": standing["praiseworthy"],
        "reasons": business.get("score_reasons") or [],
        "draft": draft,
    }


def decide(business_id: int, decision: str, *, note: str = "") -> dict[str, Any]:
    """
    Record a decision and take the business out of the queue.

    approve — worth emailing. The business is qualified and its draft is
              approved, which is what makes it ready to send.
    reject  — not worth emailing. Disqualified, and it does not come back.
    skip    — no decision. Nothing changes, and it is here again next time.
    """
    if decision not in DECISIONS:
        raise ValueError(f"unknown decision: {decision}")
    business = db.get_business(business_id)
    if not business:
        raise ValueError(f"no business with id {business_id}")

    if decision == "skip":
        return {"business_id": business_id, "decision": "skip", "status": business["status"]}

    if decision == "reject":
        db.update_business(business_id, {"status": "disqualified"})
        db.log_activity(business_id, "reviewed", f"Not a fit{': ' + note if note else ''}")
        return {"business_id": business_id, "decision": "reject", "status": "disqualified"}

    # Approve. The draft is approved too where there is one, because a
    # business approved without its email approved is still not sendable —
    # and the reviewer just read that email.
    db.update_business(business_id, {"status": "qualified"})
    approved = None
    drafts = [m for m in db.list_messages(business_id=business_id) if m["status"] == "draft"]
    if drafts:
        approved = outreach.approve_message(int(drafts[0]["id"]))
    db.log_activity(business_id, "reviewed",
                    "Approved for outreach" + (" with its draft" if approved else ""))
    return {"business_id": business_id, "decision": "approve", "status": "qualified",
            "message": approved}
