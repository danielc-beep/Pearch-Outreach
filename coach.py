"""
The advisor on the dashboard: three things to do next, and somewhere to
think them through.

Three rules shape this module, and they are the reason it can be trusted on
a screen someone makes decisions from:

1. It never invents a number. Claude is handed a brief built from the live
   database and told those figures are the only ones it may use. Everything
   it says can be checked against the same dashboard it sits on.
2. It never invents a link. A suggestion names one of the screens in FOCUS
   and the app resolves the URL, so a suggestion cannot send you somewhere
   that does not exist.
3. It works without Claude. When ANTHROPIC_API_KEY is unset, or the call
   fails, a plain rule-based advisor produces the same three suggestions
   from the same brief. The panel is never empty and never lies about where
   its advice came from.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any

import anthropic
from pydantic import BaseModel, Field

import crm
import db
import renewals
import target
import worklist
from config import ANTHROPIC_API_KEY, DEFAULT_DEAL_VALUE, DRAFT_MODEL

log = logging.getLogger(__name__)

# Advice is worth about as much thought as a short email, and it is asked for
# on every dashboard visit. Medium keeps it quick enough to feel live.
COACH_EFFORT = "medium"

CACHE_KEY = "coach_suggestions"

# How many turns of a conversation to carry. Long enough to follow a thread,
# short enough that the brief stays the biggest thing in the prompt.
MAX_HISTORY = 12
MAX_QUESTION = 2000

# The only places a suggestion can send you. The model picks a key; the app
# owns the URL. That way a made-up path is impossible rather than unlikely.
FOCUS: dict[str, dict[str, str]] = {
    "replied":   {"label": "Open the replies",     "url": "/admin/database?status=replied"},
    "outbox":    {"label": "Open the outbox",      "url": "/emails/outbox?status=approved"},
    "review":    {"label": "Work the queue",       "url": "/admin/review"},
    "align":     {"label": "Align mastheads",      "url": "/admin/align"},
    "addresses": {"label": "Find emails",          "url": "/addresses"},
    "contacted": {"label": "Chase the follow-ups", "url": "/crm"},
    "qualified": {"label": "Open Qualified",       "url": "/crm"},
    "pipeline":  {"label": "Open the pipeline",    "url": "/crm"},
    "pricing":   {"label": "Put values on deals",  "url": "/crm"},
    "prospect":  {"label": "Find more businesses", "url": "/prospect"},
    "target":    {"label": "Set one on the dashboard", "url": "/#target"},
    "renewals":  {"label": "Open the client book",   "url": "/revenue#renewals"},
    "revenue":   {"label": "Open the year",          "url": "/revenue"},
}
DEFAULT_FOCUS = "pipeline"


def _money(amount: float | None) -> str:
    return "$0" if not amount else f"${amount:,.0f}"


# ---------- What the coach knows ----------

def _cold_cards(stage: str, limit: int = 5) -> list[dict[str, Any]]:
    """The oldest cards sitting at a stage past its patience, named."""
    stale_after = crm.BY_KEY[stage]["stale_after"]
    if not stale_after:
        return []
    got = crm.column(stage, per_page=40)
    cold = [c for c in got["cards"] if c["cold"]]
    cold.sort(key=lambda c: c["days"], reverse=True)
    return [{"name": c.get("name"), "suburb": c.get("suburb") or c.get("region") or "",
             "industry": c.get("industry") or c.get("category") or "",
             "days": c["days"], "value": c.get("deal_value")}
            for c in cold[:limit]]


def brief() -> dict[str, Any]:
    """
    Everything true about the pipeline right now, in one dictionary.

    This is both the prompt's evidence and the fallback advisor's input, so
    the two can never disagree about the facts — only about who wrote the
    sentence around them.
    """
    stats = db.stats()
    money = db.revenue(DEFAULT_DEAL_VALUE)
    board = worklist.board()
    funnel = crm.funnel()
    return {
        "at": db.now(),
        "target": target.progress(),
        "revenue": money,
        "stats": {"total": stats["total"], "with_email": stats["with_email"],
                  "sent": stats["sent"], "approved": stats.get("approved", 0),
                  "added_this_week": stats.get("added_this_week", 0)},
        "stages": [{"key": s["key"], "label": s["label"], "count": s["count"],
                    "cold": s["cold"]} for s in funnel["stages"]],
        "waiting": [{"key": i["key"], "count": i["count"], "label": i["label"]}
                    for i in board],
        "conversion": crm.conversion(),
        "trades": db.industry_performance(),
        "cold": {stage: _cold_cards(stage) for stage in ("replied", "contacted", "qualified")},
        "book": renewals.book(),
    }


def fingerprint(data: dict[str, Any]) -> str:
    """
    A hash of the facts that would change the advice.

    The dashboard polls every few seconds; asking Claude each time would be
    both slow and absurd. Anything that moves the pipeline moves this hash,
    and nothing else does — so advice refreshes when the work does.
    """
    material = {
        "stages": data["stages"],
        "waiting": data["waiting"],
        "won": data["revenue"]["won"]["total"],
        "pipeline": data["revenue"]["pipeline"]["total"],
        "unpriced": data["revenue"]["pipeline"]["unpriced"],
        "short": round(data["target"]["short"]),
        "weeks_left": data["target"]["weeks_left"],
        "total": data["stats"]["total"],
    }
    blob = json.dumps(material, sort_keys=True, default=str)
    return hashlib.sha256(blob.encode()).hexdigest()[:16]


def brief_text(data: dict[str, Any]) -> str:
    """The brief as prose, because that is what a prompt can read."""
    t = data["target"]
    money = data["revenue"]
    lines: list[str] = []

    if t["set"]:
        pace = ("ahead of pace by " + _money(t["ahead_by"])) if t["ahead_by"] >= 0 else \
               ("behind pace by " + _money(-t["ahead_by"]))
        lines.append(
            f"TARGET: {_money(t['amount'])} this {t['period']}. Closed so far "
            f"{_money(t['won'])}. Still to find {_money(t['short'])}. "
            f"{t['days_left']} days left, {pace}, "
            f"{_money(t['needed_weekly'])} a week from here."
        )
    else:
        lines.append("TARGET: none set.")

    lines.append(
        f"REVENUE: closed won {_money(money['won']['total'])} across "
        f"{money['won']['count']} deals. Open pipeline "
        f"{_money(money['pipeline']['total'])} across {money['pipeline']['count']} "
        f"deals, of which {money['pipeline']['unpriced']} have no value on them yet."
    )

    lines.append("PIPELINE BY STAGE (count, and how many have sat too long):")
    for stage in data["stages"]:
        cold = f", {stage['cold']} of them cold" if stage["cold"] else ""
        lines.append(f"- {stage['label']}: {stage['count']}{cold}")

    if data["waiting"]:
        lines.append("JOBS WAITING:")
        for item in data["waiting"]:
            lines.append(f"- {item['count']} {item['label']}")

    conv = data["conversion"]
    measured = [s for s in conv["stages"] if s["rate"] is not None]
    if measured:
        lines.append(
            f"CONVERSION (of everything that ever reached a stage, how much went "
            f"further — based on {conv['based_on']} businesses that actually moved):")
        for stage in measured:
            days = f", median {stage['median_days']:.0f} days there" if stage["median_days"] else ""
            lines.append(f"- {stage['label']}: {stage['rate']:.0f}% went further"
                         f" ({stage['advanced']} of {stage['reached']}){days}")

    worked = [trade for trade in data["trades"] if trade["reached_out"]]
    if worked:
        lines.append("TRADES PITCHED SO FAR:")
        for trade in worked[:6]:
            rate = f"{trade['reply_rate']:.0f}% reply rate" if trade["reply_rate"] is not None \
                else "too few pitched to quote a rate"
            lines.append(
                f"- {trade['industry']}: {trade['prospects']} prospects, "
                f"{trade['reached_out']} pitched, {trade['replied']} replied, "
                f"{trade['won']} won, {_money(trade['revenue'])} earned — {rate}")

    for stage, cards in data["cold"].items():
        if not cards:
            continue
        lines.append(f"GOING COLD AT {crm.BY_KEY[stage]['label'].upper()}:")
        for card in cards:
            value = f", {_money(card['value'])} on it" if card["value"] else ""
            where = f" in {card['suburb']}" if card["suburb"] else ""
            lines.append(f"- {card['name']}{where} ({card['industry']}), "
                         f"{card['days']} days at this stage{value}")

    book = data.get("book") or {}
    if book.get("total"):
        due = [c for c in book["clients"] if c["state"] in ("overdue", "imminent", "soon")]
        lines.append(
            f"CLIENTS: {book['total']} signed, worth {_money(book['book_value'])} on the "
            f"books. {book['counts'].get('not_live', 0)} have no go-live date, so their "
            f"term has not started. {book['at_risk']} are up for renewal inside three "
            f"months, worth {_money(book['at_risk_value'])}.")
        for client in due[:5]:
            lines.append(f"- {client['name']} renews {client['due']} "
                         f"({client['days']} days), {_money(client['value'])}")

    lines.append("SCREENS A SUGGESTION CAN POINT AT (use the key on the left):")
    for key, place in FOCUS.items():
        lines.append(f"- {key}: {place['label']}")
    return "\n".join(lines)


# ---------- The advice itself ----------

class Suggestion(BaseModel):
    """One thing to do next. Validated by the SDK, so no parsing."""
    headline: str = Field(description="The action itself, imperative, under 60 characters.")
    why: str = Field(description="One or two sentences saying why, quoting at least one "
                                 "real figure from the brief.")
    focus: str = Field(description="Which screen this sends the user to. Must be one of "
                                   "the keys listed under SCREENS in the brief.")
    urgency: str = Field(description="One of: now, soon, steady.")


class Advice(BaseModel):
    suggestions: list[Suggestion] = Field(description="Exactly three, best first.")


SYSTEM = """You are the outreach coach inside ACM Outreach Database, the tool
the AEO team at Australian Community Media uses to find local businesses, align
them to a local masthead, and email them.

What ACM sells: getting a business quoted inside Google's own AI answers — AI
Mode, and the AI Overview at the top of a results page — by publishing credible
editorial in mastheads Google already trusts. It is not advertising and it is
not SEO, and being mistaken for either loses the deal.

How the pipeline works: a business is found and scored (New), read
(Researching), approved for an email (Qualified), emailed (Contacted), answers
(Replied), and signs (Won). Nothing can be emailed until it is aligned to a
masthead and holds at least 4.0 stars on Google — those are hard gates in the
app, not preferences.

You are given a brief of live figures. Those figures are the only numbers you
may use. Never invent a count, a rate, a dollar amount, a business name or a
result, and never guess at something the brief does not say. If the brief is
thin, say so plainly and advise on what it does show.

Write like a good sales manager on a Monday: short, specific, Australian
English, no hype, no filler openings, no em-dashes. Advice must be something
the person can do today in this app."""


def _ask_claude(data: dict[str, Any]) -> list[dict[str, Any]] | None:
    """Three suggestions from Claude, or None if it is unconfigured or fails."""
    if not ANTHROPIC_API_KEY:
        return None

    prompt = f"""Here is where ACM outreach stands right now.

{brief_text(data)}

Give exactly three things to do next to close more business, best first.

Rules:
- Each one names a real number from the brief. No number, no suggestion.
- Prefer the money that is closest to landing. A reply nobody has answered
  beats a new prospect every time; an approved email that has not been sent
  beats a fresh draft.
- Name specific businesses when the brief names them.
- Do not suggest anything the app cannot do: it finds businesses, aligns
  mastheads, drafts and sends email, and tracks stages. It does not make
  phone calls, run ads, or post on social media.
- `focus` must be one of the keys under SCREENS, and must be the screen that
  actually does the thing you are suggesting.
- `urgency` is "now" for money going cold today, "soon" for this week,
  "steady" for the work that keeps the pipeline full."""

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.parse(
            model=DRAFT_MODEL,
            max_tokens=3000,
            output_config={"effort": COACH_EFFORT},
            system=SYSTEM,
            messages=[{"role": "user", "content": prompt}],
            output_format=Advice,
        )
    except anthropic.AuthenticationError:
        log.warning("Anthropic rejected the API key — check ANTHROPIC_API_KEY")
        return None
    except anthropic.RateLimitError:
        log.warning("Anthropic rate limited the coach request")
        return None
    except anthropic.APIStatusError as e:
        log.warning("Anthropic API error %s: %s", e.status_code, e.message)
        return None
    except anthropic.APIConnectionError as e:
        log.warning("Could not reach Anthropic: %s", e)
        return None

    if response.stop_reason == "refusal":
        log.warning("Claude declined to advise")
        return None

    advice = response.parsed_output
    if not advice or not advice.suggestions:
        return None
    return [_tidy(s.model_dump()) for s in advice.suggestions[:3]]


def _tidy(suggestion: dict[str, Any]) -> dict[str, Any]:
    """Resolve the focus key to a real link, and keep urgency to the three we style."""
    key = (suggestion.get("focus") or "").strip()
    if key not in FOCUS:
        key = DEFAULT_FOCUS
    urgency = (suggestion.get("urgency") or "steady").strip().lower()
    if urgency not in ("now", "soon", "steady"):
        urgency = "steady"
    return {
        "headline": (suggestion.get("headline") or "").strip(),
        "why": (suggestion.get("why") or "").strip(),
        "focus": key,
        "action": FOCUS[key]["label"],
        "url": FOCUS[key]["url"],
        "urgency": urgency,
    }


def _rules(data: dict[str, Any]) -> list[dict[str, Any]]:
    """
    The same three suggestions, worked out arithmetically.

    This is what the panel shows before Claude has answered, when there is no
    API key, and when the call fails. It is ordered by how close the money is
    to landing, which is the same instruction the prompt gives Claude.
    """
    waiting = {i["key"]: i["count"] for i in data["waiting"]}
    stages = {s["key"]: s for s in data["stages"]}
    money = data["revenue"]
    t = data["target"]
    out: list[dict[str, Any]] = []

    def add(count: int, headline: str, why: str, focus: str, urgency: str) -> None:
        if count > 0:
            out.append(_tidy({"headline": headline, "why": why,
                              "focus": focus, "urgency": urgency}))

    replied = waiting.get("replied", 0)
    add(replied, f"Answer the {replied} who replied",
        f"{replied} {'business has' if replied == 1 else 'businesses have'} answered an "
        f"email and {'is' if replied == 1 else 'are'} waiting on you. Replied goes cold "
        f"in two days, and this is the closest money in the pipeline.",
        "replied", "now")

    approved = waiting.get("send", 0)
    add(approved, f"Send the {approved} approved emails",
        f"{approved} {'email is' if approved == 1 else 'emails are'} drafted, reviewed and "
        f"signed off. Nothing is stopping them but the sending.",
        "outbox", "now")

    book = data.get("book") or {}
    counts = book.get("counts") or {}
    not_live = counts.get("not_live", 0)
    add(not_live, f"Set the go-live date on {not_live} client"
                  f"{'' if not_live == 1 else 's'}",
        f"{not_live} signed {'client has' if not_live == 1 else 'clients have'} no "
        f"go-live date, so nobody has published their content and their twelve months "
        f"has not started.",
        "renewals", "now")

    up = counts.get("overdue", 0) + counts.get("imminent", 0)
    add(up, f"Renew {up} client{'' if up == 1 else 's'}",
        f"{up} {'is' if up == 1 else 'are'} at or past their renewal date, out of "
        f"{_money(book.get('at_risk_value', 0))} up inside three months. Renewing "
        f"someone who already bought is the cheapest revenue there is.",
        "renewals", "now")

    cold_contacted = stages.get("contacted", {}).get("cold", 0)
    add(cold_contacted, f"Follow up {cold_contacted} that went quiet",
        f"{cold_contacted} contacted {'business has' if cold_contacted == 1 else 'businesses have'} "
        f"sat more than a fortnight with no reply. A second touch costs nothing.",
        "contacted", "soon")

    to_review = waiting.get("review", 0)
    add(to_review, f"Clear the {to_review} waiting on a decision",
        f"{to_review} {'business is' if to_review == 1 else 'businesses are'} drafted and "
        f"waiting for approval. None of them can be emailed until you decide.",
        "review", "soon")

    unaligned = waiting.get("unaligned", 0)
    add(unaligned, f"Align {unaligned} to a masthead",
        f"{unaligned} {'business has' if unaligned == 1 else 'businesses have'} no masthead, "
        f"so none can be emailed. The local paper is what gives the pitch its weight.",
        "align", "soon")

    unpriced = money["pipeline"]["unpriced"]
    add(unpriced, f"Put a value on {unpriced} open deals",
        f"{unpriced} of {money['pipeline']['count']} open deals have no dollar figure, so "
        f"the {_money(money['pipeline']['total'])} pipeline is a guess.",
        "pricing", "steady")

    no_email = waiting.get("no_email", 0)
    add(no_email, f"Find addresses for {no_email} businesses",
        f"{no_email} scored {'business has' if no_email == 1 else 'businesses have'} a "
        f"website but no email address, so {'it is' if no_email == 1 else 'they are'} "
        f"unworkable until someone goes looking.",
        "addresses", "steady")

    best = next((trade for trade in data["trades"] if trade["won"]), None)
    if best and t["set"] and t["short"] > 0:
        add(1, f"Prospect more {best['industry']}",
            f"{best['industry']} has won {best['won']} "
            f"{'deal' if best['won'] == 1 else 'deals'} worth {_money(best['revenue'])} "
            f"from {best['reached_out']} pitched. There is {_money(t['short'])} left to find.",
            "prospect", "steady")

    # Below here is the filler: true, useful, and almost always applicable, so
    # the panel offers three even on a quiet morning when nothing is on fire.
    stuck = max((s for s in data["stages"]
                 if s["key"] not in ("replied", "won") and s["count"]),
                key=lambda s: s["count"], default=None)
    if stuck:
        add(stuck["count"], f"Move the {stuck['count']} sitting at {stuck['label']}",
            f"{stuck['count']} {'business is' if stuck['count'] == 1 else 'businesses are'} "
            f"parked at {stuck['label']}, the fullest stage on the board. Nothing behind "
            f"them can land until they move.",
            "pipeline", "steady")

    if not t["set"]:
        add(1, "Set a revenue target",
            f"There is no target, so {_money(data['revenue']['won']['total'])} closed has "
            f"nothing to be measured against and the pace figures cannot be worked out.",
            "target", "steady")

    add(1, "Find more businesses to work",
        f"There are {data['stats']['total']} businesses in the database and "
        f"{data['stats']['added_this_week']} arrived this week. The pipeline only stays "
        f"full if the top of it does.",
        "prospect", "steady")

    return out[:3]


def _cached() -> dict[str, Any] | None:
    raw = db.get_setting(CACHE_KEY)
    if not raw:
        return None
    try:
        return json.loads(raw)
    except ValueError:
        return None


def suggestions(force: bool = False) -> dict[str, Any]:
    """
    Three things to do next, from Claude when it can be asked.

    Cached against the brief's fingerprint: the same pipeline gets the same
    advice without a second model call, and any real movement asks again.
    `source` says who wrote it, and the panel prints that — advice from a
    rule and advice from a model are worth different amounts of trust.
    """
    data = brief()
    stamp = fingerprint(data)
    cached = _cached()
    if not force and cached and cached.get("fingerprint") == stamp:
        return {**cached, "cached": True}

    from_claude = _ask_claude(data)
    answer = {
        "suggestions": from_claude or _rules(data),
        "source": "claude" if from_claude else "rules",
        "at": db.now(),
        "fingerprint": stamp,
        "based_on": data["stats"]["total"],
    }
    if from_claude:
        # Only a model answer is worth storing. Caching the rule-based one
        # would pin the panel to the fallback until the pipeline moved, which
        # is exactly when a key arriving should have fixed it.
        db.set_setting(CACHE_KEY, json.dumps(answer))
    return {**answer, "cached": False}


def preview() -> dict[str, Any]:
    """
    What to render before the page has asked for anything.

    The cache if it fits the pipeline as it stands, and the rule-based three
    otherwise. Never a model call: this runs while someone is waiting for the
    dashboard to draw.
    """
    data = brief()
    stamp = fingerprint(data)
    cached = _cached()
    if cached and cached.get("fingerprint") == stamp:
        return {**cached, "cached": True}
    return {"suggestions": _rules(data), "source": "rules", "at": db.now(),
            "fingerprint": stamp, "based_on": data["stats"]["total"], "cached": False}


# ---------- The sounding board ----------

def ask(question: str, history: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """
    A question about the pipeline, answered against the same brief.

    Without an API key there is nothing honest to say, so it says that rather
    than pretending to a conversation it cannot have.
    """
    question = (question or "").strip()[:MAX_QUESTION]
    if not question:
        return {"reply": "Ask me something about the pipeline.", "source": "rules"}
    if not ANTHROPIC_API_KEY:
        return {
            "reply": "The coach needs an Anthropic API key to talk. Set "
                     "ANTHROPIC_API_KEY and the chat turns on. The three "
                     "suggestions above are worked out from your own figures "
                     "and do not need it.",
            "source": "unconfigured",
        }

    data = brief()
    turns: list[dict[str, str]] = []
    for turn in (history or [])[-MAX_HISTORY:]:
        role = "assistant" if turn.get("role") == "assistant" else "user"
        text = (turn.get("text") or "").strip()[:MAX_QUESTION]
        if text:
            turns.append({"role": role, "content": text})
    # A conversation has to start with the person, or the API rejects it.
    while turns and turns[0]["role"] != "user":
        turns.pop(0)
    turns.append({"role": "user", "content": question})

    system = (SYSTEM + "\n\nHere is where ACM outreach stands right now. These "
              "figures are the only numbers you may use.\n\n" + brief_text(data) +
              "\n\nAnswer in under 150 words unless asked for more. You are a "
              "sounding board: give a straight opinion and say what you would do, "
              "rather than listing options. If the question needs a fact the brief "
              "does not have, say which fact is missing.")

    try:
        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY)
        response = client.messages.create(
            model=DRAFT_MODEL,
            max_tokens=2000,
            system=system,
            messages=turns,
        )
    except anthropic.AuthenticationError:
        return {"reply": "Anthropic rejected the API key. Check ANTHROPIC_API_KEY.",
                "source": "error"}
    except anthropic.RateLimitError:
        return {"reply": "Anthropic is rate limiting us. Try again in a moment.",
                "source": "error"}
    except anthropic.APIStatusError as e:
        log.warning("Anthropic API error %s: %s", e.status_code, e.message)
        return {"reply": "The coach could not answer just then. Try again.",
                "source": "error"}
    except anthropic.APIConnectionError as e:
        log.warning("Could not reach Anthropic: %s", e)
        return {"reply": "Could not reach Anthropic. Try again in a moment.",
                "source": "error"}

    if response.stop_reason == "refusal":
        return {"reply": "I would rather not answer that one.", "source": "claude"}

    text = "\n".join(block.text for block in response.content
                     if getattr(block, "type", "") == "text").strip()
    return {"reply": text or "No answer came back. Try asking again.", "source": "claude"}
