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

import httpx

import db
from config import (ANTHROPIC_API_KEY, DAILY_SEND_CAP, DRAFT_MODEL, FROM_EMAIL,
                    FROM_NAME, REPLY_TO_EMAIL, RESEND_API_KEY, SEND_ENABLED,
                    SENDER_IDENTITY, UNSUBSCRIBE_URL)

log = logging.getLogger(__name__)

DEFAULT_CAMPAIGN = {
    "name": "AI visibility — first touch",
    "subject": "{business_name} and Google's AI answers",
    "body": """Hi {first_name},

I had a look at how {business_name} shows up when someone in {suburb} asks
Google or ChatGPT for a {industry} — the sort of question that used to be a
search and is now an answer.

Right now those answers cite a handful of sources, and yours isn't one of
them. That's fixable, and it's what we do: we get businesses cited inside AI
answers by publishing credible editorial across the Australian Community
Media mastheads that AI already trusts.

Worth a 15-minute call to walk you through what we found for {business_name}?

{sender_name}
""",
}


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
        "sender_name": FROM_NAME,
    }


def render_template(template: str, fields: dict[str, str]) -> str:
    """Fill {placeholders}; an unknown one is left visible rather than crashing."""
    def replace(match: re.Match[str]) -> str:
        key = match.group(1)
        return fields.get(key, match.group(0))
    return re.sub(r"\{(\w+)\}", replace, template or "")


# ---------- Drafting ----------

def _claude_draft(business: dict[str, Any], fields: dict[str, str],
                  campaign: dict[str, Any]) -> tuple[str, str] | None:
    """Ask Claude for a sharper version of the campaign template. None on failure."""
    if not ANTHROPIC_API_KEY:
        return None
    context = "\n".join(
        f"{k}: {v}" for k, v in {
            "Business": business.get("name"),
            "Industry": business.get("industry"),
            "Location": f"{business.get('suburb') or ''} {business.get('state') or ''}".strip(),
            "Website": business.get("website"),
            "Rating": f"{business.get('rating')} from {business.get('review_count')} reviews"
            if business.get("rating") else None,
            "What we know": business.get("description"),
        }.items() if v
    )
    prompt = f"""You write short B2B outreach emails for Pearch, an Australian
company that gets businesses cited inside AI answers (Google AI Overviews,
ChatGPT) by publishing editorial across the Australian Community Media
masthead network.

Here is the prospect:
{context}

Here is the campaign template we normally send:
---
Subject: {render_template(campaign['subject'], fields)}

{render_template(campaign['body'], fields)}
---

Rewrite it for this specific business. Rules:
- Keep it under 120 words, Australian English, plain and direct — no hype,
  no "I hope this email finds you well", no em-dash-heavy prose.
- Reference something concrete and true about them from the context above.
  Never invent a fact, a statistic, or a result.
- One clear ask: a 15-minute call.
- Sign off as {FROM_NAME}.

Return exactly this shape and nothing else:
SUBJECT: <subject line>
BODY:
<email body>"""
    try:
        with httpx.Client(timeout=30.0) as client:
            response = client.post(
                "https://api.anthropic.com/v1/messages",
                headers={
                    "x-api-key": ANTHROPIC_API_KEY,
                    "anthropic-version": "2023-06-01",
                    "content-type": "application/json",
                },
                json={
                    "model": DRAFT_MODEL,
                    "max_tokens": 700,
                    "messages": [{"role": "user", "content": prompt}],
                },
            )
        if response.status_code >= 300:
            log.warning("Anthropic non-2xx: %s %s", response.status_code, response.text[:200])
            return None
        text = "".join(
            block.get("text", "")
            for block in response.json().get("content", [])
            if block.get("type") == "text"
        ).strip()
    except httpx.HTTPError as e:
        log.warning("Anthropic call failed: %s", e)
        return None

    match = re.search(r"SUBJECT:\s*(.+?)\s*\nBODY:\s*\n?(.+)", text, re.S)
    if not match:
        return None
    return match.group(1).strip(), match.group(2).strip()


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
