"""
Replies coming back, and the pipeline believing them.

Replied is the most valuable stage on the board and, until this, the only one
kept up to date by somebody remembering. `log_reply` existed and nothing in
the app called it. So the coach's number-one suggestion — answer the people
who answered you — rested on a number a human had to maintain by hand, and
the stage that goes cold in two days is exactly the one that will not survive
being maintained by hand.

This takes an inbound email, works out which business it came from, decides
what kind of reply it is, and moves the pipeline accordingly. Three
distinctions do most of the work:

- **An out-of-office is not a reply.** It is the classic false positive, and
  letting it move a business to Replied would corrupt the very number this
  exists to keep true. Auto-replies are recorded and change nothing.
- **An opt-out is not a reply either.** It is a suppression, and it is acted
  on rather than filed. Being too eager here costs a prospect; being too slow
  costs a complaint, so this errs towards suppressing.
- **A bounce is about somebody who is not the sender.** It arrives from a mail
  system, so matching it on who sent it finds nobody — the address that failed
  is inside the message. A hard bounce takes that address out of service; left
  alone it would collect the pitch, a nudge and a last word, three sends to a
  mailbox that does not exist, which is how a sending domain earns a
  reputation.
- **An unmatched reply is not thrown away.** It goes in the inbox for someone
  to attach, and the dashboard counts it, because a reply nobody can find is
  the same as a reply nobody read.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import db
import outreach

log = logging.getLogger(__name__)

# Subject prefixes and body markers that mean a machine wrote it. Deliberately
# narrow: a false "this is automatic" silently loses a real reply, which is
# the expensive direction to be wrong in.
AUTO_SUBJECTS = (
    "out of office", "out-of-office", "automatic reply", "auto reply",
    "autoreply", "auto-reply", "away from the office", "on leave",
    "annual leave", "undeliverable", "delivery status notification",
    "mail delivery failed", "returned mail", "delivery has failed",
)
AUTO_MARKERS = (
    "i am currently out of the office", "i'm currently out of the office",
    "will be out of the office", "away from my desk until",
    "this is an automated", "do not reply to this",
    "your message could not be delivered", "address not found",
)

# A mail system telling us an address is finished. Kept apart from the
# out-of-office list above, because those two need opposite treatment: one is
# a person who will be back on Monday, the other is a dead mailbox.
BOUNCE_SUBJECTS = (
    "undeliverable", "delivery status notification", "mail delivery failed",
    "returned mail", "delivery has failed", "failure notice",
    "message not delivered", "delivery incomplete", "mail delivery subsystem",
)
# Permanent: the address does not exist and never will.
HARD_BOUNCE = (
    "address not found", "does not exist", "no such user", "user unknown",
    "recipient not found", "unknown recipient", "mailbox unavailable",
    "no mailbox", "recipient address rejected", "550 5.1.1", "550 5.1.10",
    "5.1.1", "unrouteable address", "invalid recipient", "account has been disabled",
)
# Temporary: the mailbox is real and having a bad week.
SOFT_BOUNCE = (
    "mailbox full", "over quota", "quota exceeded", "temporarily unavailable",
    "try again later", "greylisted", "4.2.2", "452 4.2.2", "message too large",
    "out of storage",
)

# Addresses that carry a bounce rather than being the subject of one.
POSTMASTER = ("mailer-daemon", "postmaster", "no-reply", "noreply", "bounce",
              "bounces", "mail-daemon")

# What someone writes when they want to be left alone. Matched as whole
# phrases against a lowercased body, so "remove me" does not fire on
# "remove metal".
OPT_OUT = (
    "unsubscribe", "opt out", "opt-out", "remove me", "take me off",
    "stop emailing", "stop contacting", "do not contact", "don't contact",
    "no longer wish to receive", "not interested in receiving",
    "delete my details", "remove my details", "remove my email",
)

ADDRESS = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def _address(value: str) -> tuple[str, str]:
    """Split "Bob Smith <bob@acme.com.au>" into a name and an address."""
    value = (value or "").strip()
    found = ADDRESS.search(value)
    email = found.group(0).lower() if found else ""
    name = value.split("<")[0].strip().strip('"').strip() if "<" in value else ""
    return email, name


def _domain(email: str) -> str:
    return email.split("@", 1)[1].lower() if "@" in email else ""


def classify(subject: str, body: str, sender: str = "") -> str:
    """
    What this is: human, auto, optout, bounce or soft_bounce.

    Order matters. Opt-out wins over everything, because a holiday message
    that also says "and take me off your list" is an opt-out with a holiday
    message attached. A bounce is looked for before an out-of-office, because
    several bounce notices carry wording that reads like one.

    An unclassifiable failure notice counts as a hard bounce. Wrong in the
    safe direction: the cost is one prospect nobody emails again, against a
    dead address collecting three more sends.
    """
    subject_l = (subject or "").strip().lower()
    body_l = (body or "").strip().lower()
    both = f"{subject_l}\n{body_l}"

    if any(phrase in body_l or phrase in subject_l for phrase in OPT_OUT):
        return "optout"

    from_daemon = any(p in (sender or "").lower().split("@")[0] for p in POSTMASTER)
    looks_failed = any(p in subject_l for p in BOUNCE_SUBJECTS)
    if looks_failed or (from_daemon and any(m in both for m in HARD_BOUNCE + SOFT_BOUNCE)):
        if any(m in both for m in HARD_BOUNCE):
            return "bounce"
        if any(m in both for m in SOFT_BOUNCE):
            return "soft_bounce"
        return "bounce"

    if any(subject_l.startswith(p) or f" {p}" in subject_l for p in AUTO_SUBJECTS):
        return "auto"
    if any(marker in body_l for marker in AUTO_MARKERS):
        return "auto"
    return "human"


def failed_recipient(payload: dict[str, Any], body: str) -> str:
    """
    The address a bounce is about, which is never the address it came from.

    Providers name it half a dozen ways, and when none of them are there it is
    still in the text of the notice — so the body is read for an address we
    have actually emailed, which also stops a stray support address in the
    footer being mistaken for the prospect.
    """
    for key in ("original_recipient", "failed_recipient", "x_failed_recipients",
                "recipient", "to"):
        value = payload.get(key)
        if isinstance(value, list):
            value = value[0] if value else ""
        if isinstance(value, dict):
            value = value.get("address") or value.get("email") or ""
        email, _ = _address(str(value or ""))
        if email and db.business_by_email(email):
            return email

    for found in ADDRESS.findall(body or ""):
        email = found.lower()
        if any(p in email.split("@")[0] for p in POSTMASTER):
            continue
        if db.business_by_email(email):
            return email
    return ""


def match(from_email: str, subject: str = "") -> tuple[dict[str, Any] | None, str]:
    """
    Whose reply this is, and what we matched it on.

    The address first, because it is certain. Then the domain, because a reply
    often comes from a colleague of the person we wrote to — but only when the
    domain resolves to a business we have actually emailed, so a shared
    mailbox at a big provider cannot drag in a stranger.
    """
    from_email = (from_email or "").strip().lower()
    if not from_email:
        return None, ""

    exact = db.business_by_email(from_email)
    if exact:
        return exact, "address"

    domain = _domain(from_email)
    # Nobody's company domain is gmail.com, and matching on one would attach a
    # reply to whichever prospect happened to use a free address.
    if not domain or domain in FREE_MAIL:
        return None, ""
    candidates = db.businesses_by_domain(domain)
    contacted = [b for b in candidates if b.get("last_contacted_at")]
    if contacted:
        return contacted[0], "domain"
    if len(candidates) == 1:
        return candidates[0], "domain"
    return None, ""


FREE_MAIL = {
    "gmail.com", "googlemail.com", "hotmail.com", "outlook.com", "live.com",
    "live.com.au", "yahoo.com", "yahoo.com.au", "bigpond.com", "bigpond.net.au",
    "icloud.com", "me.com", "optusnet.com.au", "iinet.net.au", "tpg.com.au",
    "internode.on.net", "protonmail.com", "aol.com", "msn.com",
}


def receive(payload: dict[str, Any]) -> dict[str, Any]:
    """
    Take one inbound email and do whatever it deserves.

    Tolerant about the shape it arrives in: Resend's inbound webhook, a
    Cloudflare email worker and a hand-rolled forwarder all name these fields
    differently, and the difference is not worth a provider-specific parser.
    """
    data = payload.get("data") if isinstance(payload.get("data"), dict) else payload
    raw_from = (data.get("from") or data.get("sender") or data.get("from_email") or "")
    if isinstance(raw_from, dict):                 # some providers nest it
        raw_from = raw_from.get("address") or raw_from.get("email") or ""
    from_email, from_name = _address(str(raw_from))
    from_name = from_name or str(data.get("from_name") or "")
    subject = str(data.get("subject") or "")
    body = str(data.get("text") or data.get("body") or data.get("plain") or
               data.get("stripped_text") or "")
    received_at = str(data.get("created_at") or data.get("received_at") or "") or db.now()

    if not from_email:
        log.warning("inbound with no usable sender: %s", list(data)[:8])
        return {"stored": False, "why": "No sender address."}

    kind = classify(subject, body, from_email)
    if kind in ("bounce", "soft_bounce"):
        # The sender is a mail system, so matching on it finds nobody. What
        # the notice is about is inside the notice.
        failed = failed_recipient(data, body)
        business = db.business_by_email(failed) if failed else None
        matched_on = "bounced address" if business else ""
        if failed:
            from_email = failed
    else:
        business, matched_on = match(from_email, subject)
    business_id = int(business["id"]) if business else None
    message = db.last_message_to(business_id) if business_id else None

    inbound_id = db.record_inbound({
        "received_at": received_at, "from_email": from_email, "from_name": from_name,
        "subject": subject, "body": body[:20000], "kind": kind,
        "business_id": business_id, "matched_on": matched_on,
        "message_id": int(message["id"]) if message else None,
        # An unmatched one is work for a person; a matched one is already done.
        "handled": 1 if business_id else 0,
    })

    result = {"stored": True, "id": inbound_id, "kind": kind,
              "business_id": business_id, "matched_on": matched_on, "acted": "none"}
    if not business_id:
        log.info("inbound from %s matched nothing — left in the inbox", from_email)
        return result

    return {**result, "acted": apply_to(inbound_id, business_id, kind, from_email,
                                        subject, body)}


def apply_to(inbound_id: int, business_id: int, kind: str, from_email: str,
             subject: str, body: str) -> str:
    """Move the pipeline the way this kind of reply says to. Returns what it did."""
    excerpt = " ".join((body or "").split())[:180]
    who = from_email

    if kind == "optout":
        # Suppress first, so a failure later still leaves them protected.
        outreach.unsubscribe(from_email, "asked to be removed in a reply")
        db.update_business(business_id, {"do_not_contact": 1, "status": "disqualified"})
        db.log_activity(business_id, "replied",
                        f"{who} asked to be taken off the list: {excerpt}")
        log.info("opt-out from %s — suppressed and ruled out", who)
        return "suppressed"

    if kind == "bounce":
        # The address is finished. Suppressing it is what actually stops the
        # sequence: preflight refuses a suppressed address and the follow-up
        # query skips one, so one write closes both doors. The stage is left
        # alone — a dead mailbox is not a business that said no, and somebody
        # should go and find them a working address.
        # The address, not the business. outreach.unsubscribe() also marks the
        # business do-not-contact and rules it out, which is right for someone
        # who asked to be left alone and wrong here: a dead mailbox is not a
        # refusal, and ruling them out means nobody ever goes looking for a
        # working address.
        db.suppress(who, "hard bounce — address does not exist")
        db.log_activity(business_id, "bounced",
                        f"{who} bounced: {excerpt or subject or 'address not found'}")
        log.info("hard bounce for %s — suppressed", who)
        return "bounced"

    if kind == "soft_bounce":
        # A real mailbox having a bad week. Suppressing it would throw away a
        # prospect over a full inbox, so it is recorded and nothing else.
        db.log_activity(business_id, "bounced",
                        f"{who} deferred: {excerpt or subject}")
        return "logged"

    if kind == "auto":
        # Recorded, and deliberately does not touch the stage. An out-of-office
        # is not a person answering, and counting it as one would make Replied
        # a number nobody could trust.
        db.log_activity(business_id, "auto-reply",
                        f"Automatic reply from {who}: {subject or excerpt}")
        return "logged"

    outreach.log_reply(business_id, f"{who}: {excerpt}" if excerpt else f"{who} wrote back")
    db.update_inbound(inbound_id, {"handled": 1})
    return "replied"


def attach(inbound_id: int, business_id: int) -> dict[str, Any] | None:
    """
    Point an unmatched reply at the business it belongs to, by hand.

    Whoever attaches it has read it, so it takes effect the same way it would
    have on arrival — including an opt-out, which must not need a second
    decision from somebody who has already seen the words.
    """
    row = db.get_inbound(inbound_id)
    if not row or not db.get_business(business_id):
        return None
    db.update_inbound(inbound_id, {"business_id": business_id, "matched_on": "by hand",
                                   "handled": 1})
    apply_to(inbound_id, business_id, row["kind"], row["from_email"],
             row["subject"] or "", row["body"] or "")
    return db.get_inbound(inbound_id)


def dismiss(inbound_id: int) -> None:
    """Mark an unmatched reply as dealt with without attaching it to anything."""
    db.update_inbound(inbound_id, {"handled": 1})


def log_by_hand(business_id: int, text: str, from_email: str = "") -> dict[str, Any] | None:
    """
    Paste in a reply that arrived somewhere this app cannot see.

    The fallback that needs no DNS, no webhook and no provider — and the only
    way any of this worked before today.
    """
    business = db.get_business(business_id)
    if not business:
        return None
    sender = (from_email or business.get("email") or "").strip().lower()
    kind = classify("", text)
    inbound_id = db.record_inbound({
        "received_at": db.now(), "from_email": sender or "unknown",
        "from_name": business.get("name") or "", "subject": "",
        "body": (text or "")[:20000], "kind": kind, "business_id": business_id,
        "matched_on": "by hand", "handled": 1,
    })
    apply_to(inbound_id, business_id, kind, sender or "them", "", text or "")
    return db.get_business(business_id)
