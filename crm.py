"""
The pipeline, as a pipeline.

Every business carries a status and the database could always be filtered by
it, but a filter answers "show me the contacted ones" — it never answers
"where is everything sitting, and what is stuck". This module answers that:
one shape showing the whole book of work, and a way into any stage of it.

The shape is a bar and a board, not a pie. Stages are ordered — the entire
point is that a business moves left to right — and a ring throws that ordering
away. A real early-stage database is also ninety-odd per cent New, which as a
pie is one flat colour with seven slivers: accurate, and useless. A
proportional bar reads the same at 99/1 as it does at 12/12/12, and under it a
column per stage is the shape every working CRM settled on, because seeing a
card and moving a card should be the same gesture.

The colours are not decoration and they are not arbitrary. The five working
stages are an ORDINAL scale — swap two of them and the meaning changes — so
they take one hue in monotone lightness steps, and the reader sees the
progression in the colour itself. The three outcomes are not stages at all,
so they take reserved status colours instead: green for won, red for lost,
grey for ruled out. Every step was validated rather than eyeballed: monotone
lightness, visible gaps between steps, and the dimmest step still clearing
2:1 against the panel it sits on.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import db

# key, label, what it means, colour, where it goes next
STAGES: list[dict[str, Any]] = [
    {"key": "new", "label": "New", "colour": "#35688F", "next": "researching",
     "stale_after": 0,
     "blurb": "Found and scored. Nobody has looked at them yet."},
    {"key": "researching", "label": "Researching", "colour": "#1188B4", "next": "qualified",
     "stale_after": 7,
     "blurb": "Being read — the website, the reviews, whether they are worth the call."},
    {"key": "qualified", "label": "Qualified", "colour": "#00A9D4", "next": "contacted",
     "stale_after": 7,
     "blurb": "Worth an email. Approved in the review queue and waiting to go out."},
    {"key": "contacted", "label": "Contacted", "colour": "#4FC8EE", "next": "replied",
     "stale_after": 14,
     "blurb": "The email has gone. Now it is a waiting game."},
    {"key": "replied", "label": "Replied", "colour": "#A2E5FB", "next": "won",
     "stale_after": 2,
     "blurb": "Someone answered. This is the one that goes cold fastest."},
    {"key": "won", "label": "Won", "colour": "#5FE3A8", "next": "", "stale_after": 0,
     "blurb": "Signed. The whole point of the exercise."},
    {"key": "lost", "label": "Lost", "colour": "#FF9186", "next": "", "stale_after": 0,
     "blurb": "Asked and answered no. Worth knowing why."},
    {"key": "disqualified", "label": "Ruled out", "colour": "#7C8AA8", "next": "",
     "stale_after": 0,
     "blurb": "Not a fit, no address, or asked not to be contacted."},
]

BY_KEY = {stage["key"]: stage for stage in STAGES}

# The name of the stage each one leads to, resolved once. It was computed in
# overview() alone before, so anything reading a stage directly — the "Move to
# …" button, for one — got an empty string where the destination should be.
for _stage in STAGES:
    _stage["next_label"] = BY_KEY[_stage["next"]]["label"] if _stage["next"] else ""

# The five that are a sequence, as opposed to the three that are an ending.
WORKING = [s["key"] for s in STAGES if s["next"]] + ["won"]

# How many cards a column loads at once. A stage holding four hundred is
# common early on and rendering all of them costs a second of layout for rows
# nobody scrolls to; the column says what it is holding and loads more on ask.
PAGE = 24

# A card older than its stage's threshold is cold. Thresholds live on the
# stages because they are not the same everywhere: a fortnight in Contacted is
# a follow-up, two days in Replied is a lost deal.
CLOSED = ("won", "lost", "disqualified")


def get(key: str) -> dict[str, Any] | None:
    return BY_KEY.get(key)


def label_for(key: str) -> str:
    stage = BY_KEY.get(key)
    return stage["label"] if stage else (key or "—")


def _age_days(stamp: str | None) -> int:
    if not stamp:
        return 0
    try:
        when = datetime.fromisoformat(stamp)
    except ValueError:
        return 0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - when).days)


def funnel() -> dict[str, Any]:
    """
    The pipeline as a pipeline: one bar, in stage order, plus what falls out.

    A pie was the wrong shape for this. Stages are ordered — the whole point
    is that a business moves left to right — and a ring throws that ordering
    away. Worse, a real early-stage database is ninety-odd per cent New, and a
    pie of that is one flat colour with seven slivers: technically accurate,
    useless to look at. A proportional bar reads the same at 99/1 as at
    12/12/12, and the number that actually matters — how many survive each
    step — has nowhere to live on a pie at all.
    """
    counts = db.stats()["by_status"]
    working = [s for s in STAGES if s["key"] not in ("lost", "disqualified")]
    live = sum(int(counts.get(s["key"], 0)) for s in working)
    total = sum(int(counts.get(s["key"], 0)) for s in STAGES)

    rows: list[dict[str, Any]] = []
    for stage in working:
        count = int(counts.get(stage["key"], 0))
        # Not a conversion rate. A conversion rate needs to know how many ever
        # reached a stage, and all this database knows is where things are
        # standing now — measured against the stage behind it, the first
        # attempt cheerfully reported 200%, which is worse than no number.
        # This is the one that is both true and worth acting on: of the ones
        # sitting here, how many have sat too long.
        cold = (db.count_stale_in_stage(stage["key"], stage["stale_after"])
                if stage["stale_after"] and count else 0)
        rows.append({**stage, "count": count, "cold": cold,
                     "pct": (100.0 * count / live) if live else 0.0})

    endings = [{**BY_KEY[key], "count": int(counts.get(key, 0))} for key in ("lost", "disqualified")]
    return {"stages": rows, "endings": endings, "live": live, "total": total}


def column(stage_key: str, offset: int = 0, per_page: int = PAGE,
           **filters: Any) -> dict[str, Any]:
    """One column of the board: its cards, and how many more there are."""
    stage = BY_KEY[stage_key]
    rows, total = db.list_businesses(status=stage_key, sort="score",
                                     limit=per_page, offset=offset, **filters)
    since = db.stage_since([int(r["id"]) for r in rows])
    cards = []
    for business in rows:
        business_id = int(business["id"])
        days = _age_days(since.get(business_id) or business.get("created_at"))
        limit = stage["stale_after"]
        cards.append({**business, "days": days,
                      "cold": bool(limit and days >= limit),
                      "value": business.get("deal_value")})
    return {**stage, "cards": cards, "total": total,
            "shown": offset + len(cards),
            "more": max(0, total - (offset + len(cards)))}


def board(**filters: Any) -> list[dict[str, Any]]:
    """Every stage as a column, in the order work moves through them."""
    return [column(stage["key"], **filters) for stage in STAGES]


def move(business_id: int, to_stage: str) -> dict[str, Any]:
    """
    Move one business along, and say so in its history.

    Raises ValueError on an unknown stage rather than writing it: a status
    that is not in STAGES is invisible everywhere that groups by stage.
    """
    if to_stage not in BY_KEY:
        raise ValueError(f"Unknown stage: {to_stage}")
    business = db.get_business(business_id)
    if not business:
        raise ValueError(f"no business with id {business_id}")

    was = business.get("status") or "new"
    if was == to_stage:
        return {"id": business_id, "from": was, "to": to_stage, "changed": False}

    db.update_business(business_id, {"status": to_stage})
    db.log_activity(business_id, "stage",
                    f"Moved from {label_for(was)} to {label_for(to_stage)}.")
    return {"id": business_id, "from": was, "to": to_stage, "changed": True}
