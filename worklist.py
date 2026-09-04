"""
What is waiting to be done, and where to go and do it.

Every part of the app already knows its own state — the queue knows how many
await a decision, the outbox knows what is approved, the database knows what
is missing an address or a masthead. Nothing collected it, so the dashboard
reported the past instead of answering the only question you have on opening
the app: what do I do next.

Each item is a count, a sentence saying why it matters, and the one link that
starts the work. Items with nothing waiting are dropped, so the board is a
list of jobs rather than a wall of zeroes.
"""
from __future__ import annotations

from typing import Any

import backup
import db
import review
from config import MIN_PROSPECT_RATING


def _item(key: str, count: int, label: str, detail: str, action: str,
          href: str, tone: str = "") -> dict[str, Any] | None:
    return None if count <= 0 else {
        "key": key, "count": count, "label": label,
        "detail": detail, "action": action, "href": href, "tone": tone,
    }


def board() -> list[dict[str, Any]]:
    """
    The jobs waiting, in the order they should be done.

    Ordered by the workflow rather than by size: businesses have to be
    reviewed before they can be sent, and a masthead has to be right before
    an email goes out claiming it.
    """
    approved = len(db.list_messages(status="approved", limit=5000))
    replied = db.list_businesses(status="replied", limit=1)[1]
    no_email = db.list_businesses(has_email=False, limit=1)[1]
    unaligned = db.count_without_masthead()
    below_rating = db.count_below_rating(MIN_PROSPECT_RATING)
    to_review = len(review.queue_ids())

    # A backup nudge, but only once there is something worth losing.
    stale = backup.age_in_days()
    backup_note = None
    if db.stats()["total"] >= 20:
        if stale is None:
            backup_note = ("No backup has ever been taken. Everything here lives in one "
                           "file on one disk.")
        elif stale >= 7:
            backup_note = (f"The newest backup is {int(stale)} days old, and it is on the "
                           "same disk as the database it protects.")

    items = [
        _item("replied", replied,
              "replied to an email",
              "Someone answered. That is the point of all of this, and it goes cold fastest.",
              "Open them", "/businesses?status=replied", "hot"),
        # Above the review row on purpose: nothing here can be reviewed until
        # it has a masthead, so this is the queue that unblocks that one.
        _item("unaligned", unaligned,
              "not aligned to a masthead",
              "Nothing moves without one. Their emails would come from ACM rather than "
              "the local paper that carries weight.",
              "Align them", "/align", "warn"),
        _item("review", to_review,
              "waiting for a decision",
              "Read the business and its email, then approve it or rule it out.",
              "Work the queue", "/review"),
        _item("send", approved,
              "approved and ready to send",
              "Reviewed, drafted and signed off. Nothing is stopping these but the sending.",
              "Open the outbox", "/outbox?status=approved"),
        _item("no_email", no_email,
              "have no email address",
              "Found, scored, and unusable until someone has an address to write to.",
              "Find addresses", "/addresses"),
        _item("below_rating", below_rating,
              f"under {MIN_PROSPECT_RATING:.0f} stars",
              "Every email opens by congratulating the business on its rating. These cannot be worked.",
              "Review them", "/businesses?masthead=&min_rating=0.1", "warn"),
        # Last on purpose. It is housekeeping, not the day's work — but it is
        # the only item here whose cost is unrecoverable.
        _item("backup", 1 if backup_note else 0,
              "backup to take",
              backup_note or "",
              "Back it up", "/backups", "warn"),
    ]
    return [i for i in items if i]


def next_step(has_businesses: bool) -> dict[str, str]:
    """
    The single sentence shown when there is nothing waiting.

    An empty board should say what to do about being empty, not congratulate
    you on it.
    """
    if not has_businesses:
        return {"title": "Nothing in the database yet",
                "detail": "Sweep a masthead's patch and it fills in a couple of minutes.",
                "action": "Find businesses", "href": "/prospect"}
    return {"title": "All clear",
            "detail": "Everything found has been decided on. Time to find more.",
            "action": "Sweep a patch", "href": "/prospect"}
