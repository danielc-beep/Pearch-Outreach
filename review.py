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


# ---------- Triage ----------
# The gates already decide which businesses are worth emailing: four stars,
# an ACM masthead, an address, an industry in the ICP. Asking a person to
# confirm that four hundred times is asking them to re-run a filter by hand.
#
# What no gate can check is the email itself — whether Claude named the right
# paper, whether it claimed something we cannot keep, whether the address is a
# person or a bin. So the queue is triaged: every draft is run through the
# same checks the send does, the ones that pass are offered as a single
# approval, and a person reads the ones that do not.

def flags_for(business: dict[str, Any], draft: dict[str, Any] | None) -> list[str]:
    """
    Every reason this one wants a human, in the reviewer's words.

    Empty means the automated checks are all satisfied — which is not the
    same as "definitely fine", only "nothing we know how to check is wrong".
    """
    if not draft:
        return ["No email has been written for it yet."]

    reasons = list(outreach.warnings(draft))

    # The send's own blockers, minus the ones that are about configuration
    # rather than this business: sending being switched off and the draft not
    # being approved yet are true of every draft in the queue.
    about_the_setup = ("Sending is disabled", "No RESEND_API_KEY",
                       "Message must be approved", "Daily send cap")
    for problem in outreach.preflight({**draft, "status": "approved"}):
        if not problem.startswith(about_the_setup):
            reasons.append(problem)

    body = (draft.get("body") or "").strip()
    if len(body) < 200:
        reasons.append("The draft looks too short to be a real email.")
    masthead = outreach.masthead_for(business)["name"]
    if masthead and masthead.lower() not in body.lower():
        reasons.append(f"The draft never names {masthead}.")
    return reasons


def inspect(business_id: int) -> dict[str, Any] | None:
    """One business, its draft, and what the checks make of it."""
    business = db.get_business(business_id)
    if not business:
        return None
    draft = _latest_draft(business_id)
    return {"business": business, "draft": draft,
            "flags": flags_for(business, draft)}


def triage(**filters: Any) -> dict[str, Any]:
    """
    Split the queue into what a person needs to read and what they do not.

    Returns ids rather than records: the clean list exists to be approved in
    one go, and the flagged list is worked one card at a time as before.
    """
    clean: list[int] = []
    flagged: list[dict[str, Any]] = []
    for business_id in queue_ids(**filters):
        business = db.get_business(business_id)
        if not business:
            continue
        reasons = flags_for(business, _latest_draft(business_id))
        if reasons:
            flagged.append({"id": business_id, "name": business.get("name"),
                            "flags": reasons})
        else:
            clean.append(business_id)
    return {"clean": clean, "flagged": flagged,
            "clean_count": len(clean), "flagged_count": len(flagged)}


def approve_all(business_ids: list[int]) -> dict[str, Any]:
    """
    Approve a batch, re-checking each one as it goes.

    Re-checked rather than trusted: the list was built when the page loaded,
    and a draft edited or a business changed since then must not be waved
    through on the strength of a check that has gone stale.
    """
    approved, skipped = 0, []
    for business_id in business_ids:
        looked = inspect(int(business_id))
        if looked is None:
            continue
        if looked["flags"]:
            skipped.append({"id": business_id, "name": looked["business"].get("name"),
                            "flags": looked["flags"]})
            continue
        decide(int(business_id), "approve")
        approved += 1
    return {"approved": approved, "skipped": skipped}


def _latest_draft(business_id: int) -> dict[str, Any] | None:
    drafts = [m for m in db.list_messages(business_id=business_id) if m["status"] == "draft"]
    return drafts[0] if drafts else None


def card(business_id: int) -> dict[str, Any] | None:
    """Everything the reviewer needs to decide, in one payload."""
    business = db.get_business(business_id)
    if not business:
        return None

    # The newest draft, if there is one. Anything already approved or sent
    # would have taken the business out of the queue.
    draft = _latest_draft(business_id)

    standing = outreach.review_standing(business)
    return {
        "business": business,
        "band": band(int(business.get("fit_score") or 0)),
        "masthead": outreach.masthead_for(business)["name"],
        "reviews": standing["phrase"],
        "praiseworthy": standing["praiseworthy"],
        "reasons": business.get("score_reasons") or [],
        "draft": draft,
        "flags": flags_for(business, draft),
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
