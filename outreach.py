"""
Drafting and sending outreach email.

Two deliberate constraints run through this module:

1. Nothing is ever sent straight from a draft. A message is written to the
   database as `draft`, a human moves it to `approved`, and only then can
   send_message() touch it.
2. Every outgoing email carries sender identification and a working
   unsubscribe link, and is checked against the suppression list first —
   the requirements the Spam Act 2003 (Cth) puts on commercial email.

Drafting uses Claude when ANTHROPIC_API_KEY is set and falls back to a
straight template merge when it isn't, so the app works either way.
"""
from __future__ import annotations

import logging
import re
from typing import Any

import anthropic
import httpx
from pydantic import BaseModel, Field

import db
from config import (ANTHROPIC_API_KEY, DAILY_SEND_CAP, DRAFT_EFFORT, DRAFT_MODEL,
                    FROM_EMAIL, FROM_NAME, MIN_PROSPECT_RATING, REPLY_TO_EMAIL,
                    RESEND_API_KEY, SEND_ENABLED, SENDER_IDENTITY, UNSUBSCRIBE_URL)

log = logging.getLogger(__name__)

DEFAULT_CAMPAIGN = {
    "name": "AI visibility — first touch",
    "subject": "{business_name} and Google's AI answers",
    "body": """Hi {first_name},

Congratulations on {reviews} —
that is why we are getting in touch. You are clearly doing good work for
people in {suburb}, and we would like to put it in front of more of them.

Here is the gap. When someone asks Google or ChatGPT for a {industry} in
{suburb}, the answer cites a handful of sources, and yours isn't one of them.
That's what we fix: we get businesses cited inside AI answers by publishing
credible editorial across the Australian Community Media mastheads those
answers already trust.

Worth a 15-minute call to walk you through what we found for {business_name}?

Places in our content are limited, and if this isn't right for you that is
completely fine — either way, congratulations on the work you have put in. We
think it is something that genuinely helps our partners, and it also helps ACM
produce the trusted content that keeps our communities connected and informed.

{sender_name}
""",
}


# ---------- Google reviews ----------

# Tied to the prospecting floor on purpose. A business is in the database
# because it cleared that bar, and the email opens by congratulating it on the
# rating that got it there — so a second, higher bar here would leave records
# we deliberately collected with an opening we refuse to write for them.
PRAISEWORTHY_RATING = MIN_PROSPECT_RATING if MIN_PROSPECT_RATING > 0 else 4.0
PRAISEWORTHY_REVIEWS = 5


def review_standing(business: dict[str, Any]) -> dict[str, Any]:
    """
    Whether this business's Google reviews can honestly carry the opening.

    Every email opens by congratulating them on their rating, so the one thing
    that must never happen is congratulating a business that hasn't earned it,
    or naming a number we don't have. This returns what is true, and the rest
    of the module writes around it.
    """
    rating = business.get("rating")
    count = int(business.get("review_count") or 0)

    if not rating:
        return {"praiseworthy": False, "why": "no rating on file",
                "phrase": "the reputation you have built locally"}

    rating = float(rating)
    if rating < PRAISEWORTHY_RATING:
        return {"praiseworthy": False, "why": f"{rating} is below {PRAISEWORTHY_RATING}",
                "phrase": "the reputation you have built locally"}
    if count < PRAISEWORTHY_REVIEWS:
        # A 5.0 from two reviews is not a track record, and saying so out loud
        # to someone who knows their own review count reads as a form letter.
        return {"praiseworthy": False, "why": f"only {count} reviews",
                "phrase": "the reputation you have built locally"}

    stars = f"{rating:g}"
    return {"praiseworthy": True, "why": "",
            "phrase": f"your {stars}-star rating from {count} Google reviews"}


# ---------- Merge fields ----------

def merge_fields(business: dict[str, Any], contact: dict[str, Any] | None = None) -> dict[str, str]:
    """The values available to a campaign template. Never returns None."""
    first_name = (contact or {}).get("first_name") or ""
    return {
        "business_name": business.get("name") or "there",
        "first_name": first_name or "there",
        "suburb": business.get("suburb") or business.get("region") or "your area",
        "region": business.get("region") or business.get("state") or "your region",
        "state": business.get("state") or "",
        "industry": (business.get("industry") or "business").lower(),
        "website": business.get("website") or "",
        "reviews": review_standing(business)["phrase"],
        "sender_name": FROM_NAME,
    }


def render_template(template: str, fields: dict[str, str]) -> str:
    """Fill {placeholders}; an unknown one is left visible rather than crashing."""
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return fields.get(key, match.group(0))
    return re.sub(r"\{(\w+)\}", replace, template or "")


# ---------- Drafting ----------

class DraftedEmail(BaseModel):
    """The shape Claude must return. Validated by the SDK, so no parsing."""
    subject: str = Field(description="Subject line. Under 60 characters, no clickbait.")
    body: str = Field(description="Email body, under 120 words, signed off by the sender.")


def _prospect_brief(business: dict[str, Any]) -> str:
    """
    Everything true we know about this business, for the model to draw on.

    Only facts already in the database go in here. The model is told not to
    invent anything, and giving it real detail is what makes that instruction
    followable rather than an invitation to fill gaps.
    """
    rating = ""
    if business.get("rating"):
        rating = f"{business['rating']} stars from {business.get('review_count') or 0} Google reviews"
        if not review_standing(business)["praiseworthy"]:
            rating += " (NOT high enough to congratulate — do not praise it)"

    facts = {
        "Business": business.get("name"),
        "Industry": business.get("industry"),
        "Suburb": business.get("suburb"),
        "Region": business.get("region"),
        "Website": business.get("website"),
        "Google rating": rating,
        "Staff": business.get("size_band"),
        "What their site says": business.get("description"),
    }
    return "\n".join(f"- {k}: {v}" for k, v in facts.items() if v)


def _claude_draft(business: dict[str, Any], fields: dict[str, str],
                  campaign: dict[str, Any]) -> tuple[str, str] | None:
    """
    Rewrite the campaign template for one specific business.

    Returns (subject, body), or None if Claude is unconfigured or the call
    fails — the caller then falls back to a straight template merge, so a
    drafting outage degrades rather than breaks.
    """
    if not ANTHROPIC_API_KEY:
        return None

    standing = review_standing(business)
    if standing["praiseworthy"]:
        rating_rule = (
            "- Name the star rating and the review count in the first sentence or two.\n"
            "  It is the reason for the email, so it cannot be a throwaway line."
        )
    else:
        # No rating, a mediocre one, or too few reviews to mean anything.
        # Praising it anyway would be a lie the recipient can check in one
        # click, so the opening falls back to something else that is true.
        rating_rule = (
            "- EXCEPTION for this business: their Google reviews will not carry that\n"
            "  opening (" + standing["why"] + "). Do NOT congratulate them on their\n"
            "  rating and do NOT mention a star rating or review count at all. Open\n"
            "  instead on their trade and their suburb, and say we are getting in\n"
            "  touch because of the work they do locally."
        )

    prompt = f"""You write short B2B outreach emails for the AEO team at
Australian Community Media. ACM gets businesses cited inside AI answers
(Google AI Overviews, ChatGPT) by publishing credible editorial across its
network of more than 140 mastheads — the sources those answers already trust.

Here is everything we know about the prospect. It all comes from their own
website and their Google listing:

{_prospect_brief(business)}

Here is the campaign template we would otherwise send as-is:
---
Subject: {render_template(campaign['subject'], fields)}

{render_template(campaign['body'], fields)}
---

Rewrite it for this specific business.

Rules:
- Under 160 words. Australian English. Plain and direct.
- OPEN by congratulating them on their Google review rating, and say plainly
  that their rating is why we are getting in touch — we want to highlight
  work that is already good. Use the real numbers from the facts above.
{rating_rule}
- Never invent a fact, a number, a result, or a claim about their current AI
  visibility. If you would need a fact we have not given you, write around it.
- No "I hope this email finds you well", no hype, no em-dash-heavy prose.
- One ask: a 15-minute call.
- CLOSE, before the sign-off, with a short paragraph in your own words that
  makes these four points, in this order and in this spirit: places in our
  content are limited; if this isn't right for them that is completely fine;
  either way we congratulate them on their hard work; and taking part helps
  our partners while also helping ACM produce the trusted content that keeps
  our communities connected and informed. Keep it warm and unpushy — it is a
  genuine no-pressure note, not a scarcity tactic.
- Sign off as {FROM_NAME}."""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.parse(
            model=DRAFT_MODEL,
            # Generous, because thinking tokens count against this too — a tight
            # ceiling truncates the email rather than the reasoning.
            max_tokens=4000,
            output_config={"effort": DRAFT_EFFORT},
            messages=[{"role": "user", "content": prompt}],
            output_format=DraftedEmail,
        )
    except anthropic.AuthenticationError:
        log.warning("Anthropic rejected the API key — check ANTHROPIC_API_KEY")
        return None
    except anthropic.RateLimitError:
        log.warning("Anthropic rate limited the draft request")
        return None
    except anthropic.APIStatusError as e:
        log.warning("Anthropic API error %s: %s", e.status_code, e.message)
        return None
    except anthropic.APIConnectionError as e:
        log.warning("Could not reach Anthropic: %s", e)
        return None

    if response.stop_reason == "refusal":
        log.warning("Claude declined to draft for %s", business.get("name"))
        return None

    drafted = response.parsed_output
    if not drafted or not drafted.subject.strip() or not drafted.body.strip():
        return None
    return drafted.subject.strip(), drafted.body.strip()


def draft_message(business_id: int, campaign_id: int | None = None,
                  contact_id: int | None = None, use_ai: bool = True) -> dict[str, Any]:
    """
    Write a draft email for a business and store it. Returns the message row.

    Raises ValueError when there is nobody to write to — a draft with no
    recipient is worse than no draft.
    """
    business = db.get_business(business_id)
    if not business:
        raise ValueError(f"no business with id {business_id}")
    if business.get("do_not_contact"):
        raise ValueError(f"{business['name']} is marked do-not-contact")

    contacts = db.list_contacts(business_id)
    contact = None
    if contact_id:
        contact = next((c for c in contacts if c["id"] == contact_id), None)
    if contact is None:
        contact = next((c for c in contacts if c.get("email")), None)

    to_email = (contact or {}).get("email") or business.get("email") or ""
    if not to_email:
        raise ValueError(f"no email address on file for {business['name']}")
    if db.is_suppressed(to_email):
        raise ValueError(f"{to_email} is on the suppression list")

    campaign = get_campaign(campaign_id) if campaign_id else default_campaign()
    fields = merge_fields(business, contact)

    subject = render_template(campaign["subject"], fields)
    body = render_template(campaign["body"], fields)
    if use_ai:
        generated = _claude_draft(business, fields, campaign)
        if generated:
            subject, body = generated

    message_id = db.insert_message({
        "business_id": business_id,
        "contact_id": (contact or {}).get("id"),
        "campaign_id": campaign["id"],
        "to_email": to_email,
        "subject": subject,
        "body": body,
        "status": "draft",
    })
    db.log_activity(business_id, "drafted", f"Draft written for {to_email}")
    return db.get_message(message_id) or {}


# ---------- Campaigns ----------

def default_campaign() -> dict[str, Any]:
    """The first campaign, created on demand so a fresh install has one."""
    conn = db.get_conn()
    row = conn.execute("SELECT * FROM campaigns ORDER BY id LIMIT 1").fetchone()
    if row:
        return dict(row)
    with db.tx() as c:
        cur = c.execute(
            "INSERT INTO campaigns (created_at, name, subject, body, status) "
            "VALUES (?, ?, ?, ?, 'active')",
            (db.now(), DEFAULT_CAMPAIGN["name"], DEFAULT_CAMPAIGN["subject"],
             DEFAULT_CAMPAIGN["body"]),
        )
        campaign_id = int(cur.lastrowid)
    return dict(conn.execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone())


def list_campaigns() -> list[dict[str, Any]]:
    default_campaign()
    return [dict(r) for r in db.get_conn().execute("SELECT * FROM campaigns ORDER BY id DESC")]


def get_campaign(campaign_id: int) -> dict[str, Any]:
    row = db.get_conn().execute("SELECT * FROM campaigns WHERE id = ?", (campaign_id,)).fetchone()
    if not row:
        raise ValueError(f"no campaign with id {campaign_id}")
    return dict(row)


def save_campaign(name: str, subject: str, body: str,
                  campaign_id: int | None = None) -> dict[str, Any]:
    with db.tx() as conn:
        if campaign_id:
            conn.execute(
                "UPDATE campaigns SET name = ?, subject = ?, body = ? WHERE id = ?",
                (name, subject, body, campaign_id),
            )
        else:
            cur = conn.execute(
                "INSERT INTO campaigns (created_at, name, subject, body, status) "
                "VALUES (?, ?, ?, ?, 'active')",
                (db.now(), name, subject, body),
            )
            campaign_id = int(cur.lastrowid)
    return get_campaign(int(campaign_id))


# ---------- Sending ----------

def _footer(to_email: str, base_url: str) -> str:
    unsubscribe = UNSUBSCRIBE_URL or f"{base_url.rstrip('/')}/unsubscribe?email={to_email}"
    return (
        f"\n\n—\n{SENDER_IDENTITY}\n"
        f"Don't want to hear from us? Unsubscribe: {unsubscribe}\n"
    )


def _html_body(body: str, footer: str) -> str:
    def paragraphs(text: str) -> str:
        blocks = [b.strip() for b in text.split("\n\n") if b.strip()]
        return "".join(
            f'<p style="margin:0 0 14px;">{b.replace(chr(10), "<br>")}</p>' for b in blocks
        )
    return (
        '<div style="font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;'
        'font-size:15px;line-height:1.6;color:#1A2244;max-width:560px;">'
        f"{paragraphs(body)}"
        '<div style="margin-top:24px;padding-top:14px;border-top:1px solid #ECECF0;'
        f'font-size:12px;color:#7A819E;">{paragraphs(footer.strip())}</div>'
        "</div>"
    )


def preflight(message: dict[str, Any]) -> list[str]:
    """Every reason this message must not be sent. Empty list = clear to send."""
    problems: list[str] = []
    if not SEND_ENABLED:
        problems.append("Sending is disabled (set PEARCH_SEND_ENABLED=1).")
    if not RESEND_API_KEY:
        problems.append("No RESEND_API_KEY configured.")
    if message.get("status") == "sent":
        problems.append("This message has already been sent.")
    elif message.get("status") != "approved":
        problems.append("Message must be approved before it can be sent.")
    to_email = message.get("to_email") or ""
    if not to_email:
        problems.append("No recipient address.")
    elif db.is_suppressed(to_email):
        problems.append(f"{to_email} is on the suppression list.")
    business = db.get_business(int(message["business_id"])) if message.get("business_id") else None
    if business and business.get("do_not_contact"):
        problems.append(f"{business['name']} is marked do-not-contact.")
    if db.sends_today() >= DAILY_SEND_CAP:
        problems.append(f"Daily send cap of {DAILY_SEND_CAP} reached.")
    return problems


def edit_message(message_id: int, subject: str, body: str) -> dict[str, Any]:
    """
    Rewrite a draft's subject and body.

    A sent message is a record of what went out and cannot be edited. Editing
    an approved one returns it to draft: the approval was for the text that has
    just been replaced, so it has to be given again.
    """
    message = db.get_message(message_id)
    if not message:
        raise ValueError(f"no message with id {message_id}")
    if message["status"] == "sent":
        raise ValueError("This message has already been sent and cannot be edited.")

    subject, body = subject.strip(), body.strip()
    if not subject:
        raise ValueError("The subject cannot be empty.")
    if not body:
        raise ValueError("The body cannot be empty.")

    changes: dict[str, Any] = {"subject": subject, "body": body}
    reapproval_needed = message["status"] == "approved"
    if reapproval_needed:
        changes["status"] = "draft"
    db.update_message(message_id, changes)

    db.log_activity(int(message["business_id"]), "edited",
                    "Draft edited — needs approving again" if reapproval_needed
                    else "Draft edited")
    return db.get_message(message_id) or {}


def approve_message(message_id: int) -> dict[str, Any] | None:
    db.update_message(message_id, {"status": "approved"})
    message = db.get_message(message_id)
    if message:
        db.log_activity(int(message["business_id"]), "approved", "Draft approved for sending")
    return message


def send_message(message_id: int, base_url: str = "") -> dict[str, Any]:
    """
    Send an approved message via Resend.

    Returns {"sent": bool, ...}. Never raises on a provider failure — the
    error is recorded on the message so it can be retried.
    """
    message = db.get_message(message_id)
    if not message:
        return {"sent": False, "problems": ["Message not found."]}

    problems = preflight(message)
    if problems:
        return {"sent": False, "problems": problems}

    footer = _footer(message["to_email"], base_url)
    try:
        with httpx.Client(timeout=15.0) as client:
            response = client.post(
                "https://api.resend.com/emails",
                headers={
                    "Authorization": f"Bearer {RESEND_API_KEY}",
                    "Content-Type": "application/json",
                },
                json={
                    "from": f"{FROM_NAME} <{FROM_EMAIL}>",
                    "to": [message["to_email"]],
                    "reply_to": REPLY_TO_EMAIL,
                    "subject": message["subject"],
                    "text": message["body"] + footer,
                    "html": _html_body(message["body"], footer),
                },
            )
    except httpx.HTTPError as e:
        db.update_message(message_id, {"status": "failed", "error": str(e)})
        return {"sent": False, "problems": [f"Send failed: {e}"]}

    if response.status_code >= 300:
        error = f"Resend {response.status_code}: {response.text[:200]}"
        db.update_message(message_id, {"status": "failed", "error": error})
        return {"sent": False, "problems": [error]}

    provider_id = response.json().get("id", "")
    sent_at = db.now()
    db.update_message(message_id, {"status": "sent", "sent_at": sent_at,
                                   "provider_id": provider_id, "error": None})
    business_id = int(message["business_id"])
    db.update_business(business_id, {"last_contacted_at": sent_at, "status": "contacted"})
    db.log_activity(business_id, "sent", f"Emailed {message['to_email']}")
    return {"sent": True, "provider_id": provider_id}


def log_reply(business_id: int, note: str = "") -> dict[str, Any] | None:
    """
    Record that a prospect wrote back.

    A reply is the signal that matters — it turns a name on a list into a
    conversation — so it moves the business to `replied` whatever state it was
    in, and never backwards from won or lost.
    """
    business = db.get_business(business_id)
    if not business:
        return None
    if business["status"] not in ("won", "lost"):
        db.update_business(business_id, {"status": "replied"})
    db.log_activity(business_id, "replied", note or "They wrote back")
    return db.get_business(business_id)


def unsubscribe(email: str, reason: str = "unsubscribe link") -> int:
    """
    Suppress an address and mark every matching business do-not-contact.
    Returns how many businesses were flagged.
    """
    email = (email or "").strip().lower()
    if not email:
        return 0
    db.suppress(email, reason)
    conn = db.get_conn()
    rows = conn.execute("SELECT id FROM businesses WHERE lower(email) = ?", (email,)).fetchall()
    for row in rows:
        db.update_business(int(row["id"]), {"do_not_contact": 1, "status": "disqualified"})
        db.log_activity(int(row["id"]), "unsubscribed", f"{email} unsubscribed")
    return len(rows)
