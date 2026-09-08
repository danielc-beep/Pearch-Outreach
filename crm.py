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
import revenue

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

# How many cards a column shows at once. A stage holding four hundred is
# common early on, and a column that renders all of them is neither readable
# nor quick. Twenty is about a screen of cards: enough to work through, few
# enough to see the shape of. The column header always says the real total,
# and the rest arrive twenty at a time on ask.
PAGE = 20

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


def funnel(**filters: Any) -> dict[str, Any]:
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
    counts = db.status_counts(**filters)
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
        cold = (db.count_stale_in_stage(stage["key"], stage["stale_after"], **filters)
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


def summary(**filters: Any) -> dict[str, Any]:
    """
    What this slice of the board is worth, and how much of it is moving.

    On an unfiltered board it is the whole database, which the dashboard
    already says. Its reason to exist is the filtered one: pick a masthead and
    this is that masthead's book — prospects, open pipeline, signed revenue —
    without doing the arithmetic in your head from eight column headers.
    """
    counts = db.status_counts(**filters)
    money = db.revenue(revenue.default_value(), **filters)
    working = sum(counts.get(s["key"], 0) for s in STAGES if s["key"] in WORKING)
    cold = sum(db.count_stale_in_stage(s["key"], s["stale_after"], **filters)
               for s in STAGES if s["stale_after"] and counts.get(s["key"]))
    return {
        "total": sum(counts.values()),
        "working": working,
        "cold": cold,
        "pipeline": money["pipeline"]["total"],
        "pipeline_count": money["pipeline"]["count"],
        "unpriced": money["pipeline"]["unpriced"],
        "won": money["won"]["total"],
        "won_count": money["won"]["count"],
    }


def board(**filters: Any) -> list[dict[str, Any]]:
    """Every stage as a column, in the order work moves through them."""
    return [column(stage["key"], **filters) for stage in STAGES]


def conversion() -> dict[str, Any]:
    """
    Where deals actually go, from the record of where they went.

    Of everything that ever reached a stage, how much of it went further.
    Defined as "went further" rather than "reached the very next one" on
    purpose: a deal that jumps Qualified straight to Won has not leaked, and
    counting stage-to-stage would report it as a loss at Contacted and a
    miracle at Won — which is how the first attempt at this managed to print
    200%. Measured this way the figure cannot exceed a hundred.

    It counts only businesses that actually moved through the app. Anything
    imported straight into a stage never moved, so `based_on` says how many
    the number rests on and the page prints it.
    """
    order = [s["key"] for s in STAGES if s["key"] not in ("lost", "disqualified")]
    beyond = {key: set(order[i + 1:]) for i, key in enumerate(order)}

    ever: dict[str, set[int]] = {key: set(db.reached(key)) for key in order}
    rows = []
    for key in order[:-1]:                       # Won has nowhere further to go
        arrived = ever[key]
        went_on = {b for later in beyond[key] for b in ever[later]}
        advanced = arrived & went_on
        days = db.stage_durations(key)
        rows.append({
            **BY_KEY[key],
            "reached": len(arrived),
            "advanced": len(advanced),
            "rate": (100.0 * len(advanced) / len(arrived)) if arrived else None,
            "median_days": _median(days),
            "measured": len(days),
        })
    return {"stages": rows, "based_on": len({b for s in ever.values() for b in s})}


def _median(values: list[float]) -> float | None:
    """The middle one. A mean is dragged around by the one deal that took a year."""
    if not values:
        return None
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return (ordered[middle - 1] + ordered[middle]) / 2


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

    # Stamp the win, so revenue can be counted against a period rather than
    # only ever as a running total. Moving it back out clears the date: a deal
    # that is no longer won was not won in March either.
    patch: dict[str, Any] = {"status": to_stage}
    if to_stage == "won":
        patch["won_at"] = db.now()
    elif was == "won":
        patch["won_at"] = None
    db.update_business(business_id, patch)
    db.record_move(business_id, was, to_stage)
    db.log_activity(business_id, "stage",
                    f"Moved from {label_for(was)} to {label_for(to_stage)}.")
    return {"id": business_id, "from": was, "to": to_stage, "changed": True}
