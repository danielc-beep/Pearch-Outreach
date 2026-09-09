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
import followup as _followup
import renewals as _renewals
import delivery as _delivery
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
    # Only the ones the addresses screen can actually work: no email, but a
    # website to go looking on. Counting the rest gives a number that never
    # reaches nought however much work is done.
    no_email = db.list_businesses(has_email=False, has_website=True, limit=1)[1]
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

    unplaced = db.count_unmatched_inbound()

    book = _renewals.book()

    items = [
        # Above everything: a client whose content nobody has published is
        # somebody paying for nothing, and their term has not even started.
        _item("not_live", book["counts"].get("not_live", 0),
              "signed but not live",
              "The content has not been published, so their twelve months has not "
              "started and nothing is being delivered.",
              "Set the date", "/revenue#renewals", "warn"),
        # A piece sitting on a desk is a term that has not started, and a
        # month with no report is a renewal with nothing behind it.
        _item("content", _delivery.board()["stale"],
              "pieces sitting too long",
              "Written up but not published. Until it goes live the client's twelve "
              "months has not started and they are paying for nothing.",
              "Open delivery", "/revenue/delivery", "warn"),
        _item("reports", _delivery.count_outstanding(),
              "clients owed a report",
              "Every live client gets the Pearch report each month. It is the evidence "
              "the renewal conversation rests on.",
              "File them", "/revenue/delivery"),
        # Sold and not booked in Adpoint is sold and not being invoiced.
        _item("booking", db.clients_without_booking(),
              "sales with no Adpoint number",
              "ACM invoices out of Adpoint, so until the booking number is on the "
              "record there is nothing to bill against.",
              "Add the numbers", "/revenue#renewals", "warn"),
        _item("renewals", book["counts"].get("overdue", 0)
              + book["counts"].get("imminent", 0),
              "up for renewal",
              "Their year is up or nearly up. Renewing an existing client is the "
              "cheapest revenue there is.",
              "Open the book", "/revenue#renewals", "hot"),
        # Above the replies themselves: a reply nobody could place is a reply
        # nobody has read, and it is not counted in the number underneath.
        _item("unplaced", unplaced,
              "replies nobody could place",
              "They came from an address we do not hold, so no business has moved. "
              "Attach one and it takes effect as though it had arrived matched.",
              "Open the inbox", "/emails/inbox", "hot"),
        _item("replied", replied,
              "replied to an email",
              "Someone answered. That is the point of all of this, and it goes cold fastest.",
              "Open them", "/admin/database?status=replied", "hot"),
        # Above the review row on purpose: nothing here can be reviewed until
        # it has a masthead, so this is the queue that unblocks that one.
        _item("unaligned", unaligned,
              "not aligned to a masthead",
              "Nothing moves without one. Their emails would come from ACM rather than "
              "the local paper that carries weight.",
              "Align them", "/admin/align", "warn"),
        _item("review", to_review,
              "waiting for a decision",
              "Read the business and its email, then approve it or rule it out.",
              "Work the queue", "/admin/review"),
        # Written and waiting. Separate from the pitch queue because a
        # follow-up needs nothing researched — it is read and approved.
        _item("followup", db.followups_waiting(),
              "follow-ups drafted",
              "Second and third emails, written and waiting for approval.",
              "Read them", "/emails/outbox?status=draft"),
        _item("owed", len(_followup.due(200)),
              "owed another email",
              "Emailed once, no answer, and past the waiting period. Most replies "
              "come from the second or third touch.",
              "Draft them", "/followups"),
        _item("send", approved,
              "approved and ready to send",
              "Reviewed, drafted and signed off. Nothing is stopping these but the sending.",
              "Open the outbox", "/emails/outbox?status=approved"),
        # The record is good and the address is dead — two minutes on their
        # website is all that stands between it and an email.
        _item("bounced", db.count_bounced(),
              "addresses that bounced",
              "The email came back undeliverable, so they have been taken out of "
              "sending until somebody finds a working address.",
              "Find a new one", "/addresses?bounced=1", "warn"),
        _item("no_email", no_email,
              "have no email address",
              "Found, scored, and unusable until someone has an address to write to. "
              "Each has a website to find one on.",
              "Find emails", "/addresses"),
        _item("below_rating", below_rating,
              f"under {MIN_PROSPECT_RATING:.0f} stars",
              "Every email opens by congratulating the business on its rating. These cannot be worked.",
              "Review them", "/admin/database?masthead=&min_rating=0.1", "warn"),
        # Last on purpose. It is housekeeping, not the day's work — but it is
        # the only item here whose cost is unrecoverable.
        _item("backup", 1 if backup_note else 0,
              "backup to take",
              backup_note or "",
              "Back it up", "/backups", "warn"),
    ]
    return [i for i in items if i]


def setup_gaps() -> list[dict[str, str]]:
    """
    What needs a person outside the app, rather than work inside it.

    board() is the day's work: replies to answer, queues to clear, content to
    publish. This is the other list — the handful of things nobody in the app
    can do because they live in a hosting console or on somebody's laptop. It
    is what the morning brief reads, since that is the one place Dan sees the
    app without opening it.

    Deliberately plain about the state and quiet about the remedy: this is
    served on a public health endpoint, so it says a thing is off, not which
    environment variable turns it on.
    """
    import backup
    import delivery
    import revenue
    import sources
    from config import ANTHROPIC_API_KEY, INBOUND_SECRET, SEND_ENABLED

    gaps: list[dict[str, str]] = []

    def add(key: str, title: str, why: str, where: str) -> None:
        gaps.append({"key": key, "title": title, "why": why, "where": where})

    if not any(s.available for s in sources.all_sources() if s.key != "csv"):
        add("prospecting", "Nothing is configured to search with",
            "No new businesses can be found until a prospecting source is set up.",
            "/prospect")

    if not SEND_ENABLED:
        approved = db.stats()["approved"]
        add("sending", "Sending is off",
            (f"{approved} approved email{'' if approved == 1 else 's'} "
             f"{'is' if approved == 1 else 'are'} waiting in the outbox and cannot go."
             if approved else "Approved emails will stay in the outbox."),
            "/emails/outbox")

    if not INBOUND_SECRET:
        add("inbound", "Replies are not collected automatically",
            "A reply or a bounce only counts when somebody types it in by hand, and "
            "a bounced address keeps collecting follow-ups.",
            "/emails/inbox")

    money = db.revenue(revenue.default_value())["pipeline"]
    if not revenue.default_value() and money["unpriced"]:
        add("deal_value", "Unpriced deals count as nothing",
            f"{money['unpriced']} of {money['count']} open deals have no figure on them, "
            f"so the pipeline number is only the deals somebody has priced.",
            "/revenue")

    if not ANTHROPIC_API_KEY:
        add("coach", "The coach cannot answer questions",
            "Its three suggestions still work — they are arithmetic on your own "
            "figures — but the chat needs a key.",
            "/")

    if db.stats()["total"] >= 20:
        age = backup.age_in_days()
        held = delivery.uploads_held()
        extra = (f" The {held['count']} uploaded report files are not in a snapshot either."
                 if held["count"] else "")
        if age is None:
            add("backup", "No backup has ever been taken",
                "Everything here lives in one file on one disk." + extra, "/backups")
        elif age >= 7:
            add("backup", f"The newest backup is {int(age)} days old",
                "And it is on the same disk as the database it protects." + extra,
                "/backups")

    return gaps


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
