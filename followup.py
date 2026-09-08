"""
The second and third emails, which is where most of the replies actually are.

Until this, every business got one email and then silence. In cold B2B
outreach the majority of replies arrive on touch two and three, so a list
worked once is a list worked about a third of the way. The coach already said
so — "2 contacted businesses have sat more than a fortnight with no reply" —
and there was nothing in the app that could act on it.

What this is careful about:

- **It stops the moment anyone answers.** Not when the stage says Replied,
  which a human can forget to set, but when anything human has come back
  through replies.py. Chasing someone who already wrote to you is the one
  mistake a sequence must never make.
- **It goes through the same gate as everything else.** A follow-up is a
  draft in the outbox, approved by a person, sent by the same machinery,
  subject to the same preflight, the same cap, the same suppression list.
  Nothing here sends anything.
- **It knows what was already said.** The first email is in the prompt, so
  the second one does not repeat its opening, restate the offer, or
  congratulate them on their rating a second time.
"""
from __future__ import annotations

import json
import logging
from typing import Any

import anthropic
from pydantic import BaseModel, Field

import db
import outreach
from config import ANTHROPIC_API_KEY, DRAFT_EFFORT, DRAFT_MODEL, FROM_NAME

log = logging.getLogger(__name__)

# The schedule. Two touches after the pitch and then it stops — a third
# follow-up to somebody who has ignored three is not persistence, it is the
# thing that gets a sending domain a reputation.
STEPS: list[dict[str, Any]] = [
    {"step": 2, "after_days": 5, "label": "Nudge",
     "aim": "A short nudge. They probably did not read the first one."},
    {"step": 3, "after_days": 12, "label": "Last word",
     "aim": "The last one. Say plainly that it is, and make it easy to say no."},
]
BY_STEP = {s["step"]: s for s in STEPS}
LAST_STEP = STEPS[-1]["step"]

SETTING = "followup_schedule"


def schedule() -> list[dict[str, Any]]:
    """The steps in force, which are the defaults unless someone changed them."""
    raw = db.get_setting(SETTING)
    if not raw:
        return [dict(s) for s in STEPS]
    try:
        saved = json.loads(raw)
    except ValueError:
        return [dict(s) for s in STEPS]
    out = []
    for step in STEPS:
        days = saved.get(str(step["step"]))
        out.append({**step, "after_days": int(days) if days else step["after_days"]})
    return out


def set_schedule(days_by_step: dict[int, int]) -> list[dict[str, Any]]:
    """Change how long each follow-up waits. Bounded, because 0 days is a mistake."""
    clean = {}
    for step, days in days_by_step.items():
        if int(step) not in BY_STEP:
            raise ValueError(f"There is no step {step}.")
        days = int(days)
        if not 1 <= days <= 90:
            raise ValueError("A follow-up waits between 1 and 90 days.")
        clean[str(int(step))] = days
    db.set_setting(SETTING, json.dumps(clean))
    return schedule()


def due(limit: int = 50) -> list[dict[str, Any]]:
    """
    Everyone owed a follow-up right now, earliest first, with the step they are owed.

    Later steps are asked for first so a business that has fallen a long way
    behind is caught up before a fresher one is nudged.
    """
    out: list[dict[str, Any]] = []
    for step in reversed(schedule()):
        if len(out) >= limit:
            break
        for business in db.due_for_followup(step["step"], step["after_days"],
                                            limit - len(out)):
            out.append({**business, "step": step["step"], "step_label": step["label"]})
    return out


class Followup(BaseModel):
    subject: str = Field(description="Under 60 characters. A reply to the first "
                                     "email, so it usually keeps its subject with Re:.")
    body: str = Field(description="Under 90 words.")


def _claude_followup(business: dict[str, Any], step: dict[str, Any]) -> tuple[str, str] | None:
    """Write the follow-up, or None if Claude is unconfigured or the call fails."""
    if not ANTHROPIC_API_KEY:
        return None

    title = outreach.masthead_for(business)
    who = title["name"] if title["site"] else "Australian Community Media"
    last_sent = (business.get("last_sent_at") or "")[:10]

    prompt = f"""You write short follow-up emails for the AEO team at {who}.

{business.get('name')} was emailed on {last_sent} and has not replied. Here is
the email they were sent, in full:

---
Subject: {business.get('last_subject')}

{business.get('last_body')}
---

Write follow-up number {step['step']} of {LAST_STEP}. {step['aim']}

Rules:
- Under 90 words. Australian English. Plain and direct. No hype.
- It is a follow-up to that exact email, so do not reintroduce yourself, do
  not restate the whole offer, and do not congratulate them on their Google
  rating again. They have read that, or they have not — either way, saying it
  twice is what a mailing list does.
- Add one thing the first email did not say, or ask one specific question
  they can answer in a line. A follow-up that only says "just checking in"
  is worth less than not sending it.
- Never invent a fact, a number, a result, or a claim about their current
  visibility. You know only what is in the email above.
- If you mention what we do, name the part of Google: being quoted inside AI
  Mode and the AI Overview, which is neither paid advertising nor SEO. Never
  write a vague line like "get found on Google".
- GOOGLE ONLY. Do not mention ChatGPT, Perplexity, Gemini, Copilot, Claude,
  "AI assistants" or "chatbots", even as an example.
- Make saying no easy and mean it. One line is enough.
- Sign off as {FROM_NAME}.
{"- This is the last email they will get from us. Say so, once, without drama." if step['step'] == LAST_STEP else ""}"""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.parse(
            model=DRAFT_MODEL,
            max_tokens=3000,
            output_config={"effort": DRAFT_EFFORT},
            messages=[{"role": "user", "content": prompt}],
            output_format=Followup,
        )
    except anthropic.AuthenticationError:
        log.warning("Anthropic rejected the API key — check ANTHROPIC_API_KEY")
        return None
    except anthropic.RateLimitError:
        log.warning("Anthropic rate limited the follow-up request")
        return None
    except anthropic.APIStatusError as e:
        log.warning("Anthropic API error %s: %s", e.status_code, e.message)
        return None
    except anthropic.APIConnectionError as e:
        log.warning("Could not reach Anthropic: %s", e)
        return None

    if response.stop_reason == "refusal":
        return None
    drafted = response.parsed_output
    if not drafted or not drafted.subject.strip() or not drafted.body.strip():
        return None
    return drafted.subject.strip(), drafted.body.strip()


def _plain_followup(business: dict[str, Any], step: dict[str, Any]) -> tuple[str, str]:
    """
    The follow-up when Claude cannot be asked.

    Deliberately short and a little plain. A template nudge that says one true
    thing beats a generated one that says nothing, and it beats no second
    email by a very long way.
    """
    title = outreach.masthead_for(business)
    paper = title["name"] if title["site"] else "Australian Community Media"
    subject = business.get("last_subject") or f"{business.get('name')} in Google's AI Overviews"
    if not subject.lower().startswith("re:"):
        subject = f"Re: {subject}"

    if step["step"] == LAST_STEP:
        body = (
            f"Hi,\n\nI wrote a couple of weeks ago about getting {business.get('name')} "
            f"quoted inside Google's AI Mode and AI Overview answers through editorial "
            f"in {paper}. I have not heard back, so I will leave it there — this is the "
            f"last you will hear from me on it.\n\nIf the timing is simply wrong, reply "
            f"with a month and I will come back then. If it is not for you, no reply "
            f"needed at all.\n\n{FROM_NAME}"
        )
    else:
        body = (
            f"Hi,\n\nJust following up on my note about {business.get('name')} being "
            f"quoted in Google's AI Mode and AI Overview answers — the sources Google "
            f"names when it writes the answer, rather than the ads above them or the "
            f"ranked results underneath.\n\nIs this worth fifteen minutes, or is it not "
            f"the right time? Either answer is useful.\n\n{FROM_NAME}"
        )
    return subject, body


def compose(business: dict[str, Any], step: int, use_ai: bool = True) -> dict[str, Any]:
    """The follow-up draft for one business, without touching the database."""
    plan = BY_STEP[step]
    if business.get("do_not_contact"):
        raise ValueError(f"{business['name']} is marked do-not-contact")
    to_email = business.get("last_to") or business.get("email") or ""
    if not to_email:
        raise ValueError(f"no email address on file for {business['name']}")
    if db.is_suppressed(to_email):
        raise ValueError(f"{to_email} is on the suppression list")

    subject, body = _plain_followup(business, plan)
    if use_ai:
        written = _claude_followup(business, plan)
        if written:
            subject, body = written
            if not subject.lower().startswith("re:"):
                subject = f"Re: {subject}"
    return {
        "business_id": int(business["id"]),
        "campaign_id": None,
        "to_email": to_email,
        "subject": subject[:200],
        "body": body,
        "status": "draft",
        "step": step,
    }


def draft_due(limit: int = 20, use_ai: bool = True) -> dict[str, Any]:
    """
    Write the follow-ups that are owed, into the outbox for approval.

    Nothing is sent. They land as drafts beside every other draft and go out
    the same way, which is the whole point: a sequence that could send on its
    own is a sequence that can go wrong on its own.
    """
    owed = due(limit)
    written, skipped = [], []
    for business in owed:
        try:
            payload = compose(business, business["step"], use_ai=use_ai)
        except ValueError as e:
            skipped.append({"business_id": int(business["id"]),
                            "name": business.get("name"), "why": str(e)})
            continue
        message_id = db.insert_message(payload)
        db.log_activity(int(business["id"]), "followup",
                        f"Follow-up {business['step']} drafted")
        written.append({"message_id": message_id, "business_id": int(business["id"]),
                        "name": business.get("name"), "step": business["step"]})
    log.info("follow-ups: %s drafted, %s skipped", len(written), len(skipped))
    return {"drafted": written, "skipped": skipped,
            "waiting": db.followups_waiting()}
