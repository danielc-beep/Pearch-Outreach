"""
The pipeline, as a pipeline.

Every business carries a status and the database could always be filtered by
it, but a filter answers "show me the contacted ones" — it never answers
"where is everything sitting, and what is stuck". This module answers that:
one shape showing the whole book of work, and a way into any stage of it.

The colours are not decoration and they are not arbitrary. The five working
stages are an ORDINAL scale — swap two of them and the meaning changes — so
they take one hue in monotone lightness steps, and the reader sees the
progression in the colour itself. The three outcomes are not stages at all,
so they take reserved status colours instead: green for won, red for lost,
grey for ruled out. Every step below was validated rather than eyeballed:
monotone lightness, visible gaps between steps, and the dimmest step still
clearing 2:1 against the panel it sits on.
"""
from __future__ import annotations

import math
from typing import Any

import db

# key, label, what it means, colour, where it goes next
STAGES: list[dict[str, Any]] = [
    {"key": "new", "label": "New", "colour": "#35688F", "next": "researching",
     "blurb": "Found and scored. Nobody has looked at them yet."},
    {"key": "researching", "label": "Researching", "colour": "#1188B4", "next": "qualified",
     "blurb": "Being read — the website, the reviews, whether they are worth the call."},
    {"key": "qualified", "label": "Qualified", "colour": "#00A9D4", "next": "contacted",
     "blurb": "Worth an email. Approved in the review queue and waiting to go out."},
    {"key": "contacted", "label": "Contacted", "colour": "#4FC8EE", "next": "replied",
     "blurb": "The email has gone. Now it is a waiting game."},
    {"key": "replied", "label": "Replied", "colour": "#A2E5FB", "next": "won",
     "blurb": "Someone answered. This is the one that goes cold fastest."},
    {"key": "won", "label": "Won", "colour": "#5FE3A8", "next": "",
     "blurb": "Signed. The whole point of the exercise."},
    {"key": "lost", "label": "Lost", "colour": "#FF9186", "next": "",
     "blurb": "Asked and answered no. Worth knowing why."},
    {"key": "disqualified", "label": "Ruled out", "colour": "#7C8AA8", "next": "",
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

# Donut geometry. A hole rather than a full pie so the total can live in the
# middle, where a reader looks first.
SIZE = 240.0
CENTRE = SIZE / 2
OUTER = 104.0
INNER = 64.0
GAP_PX = 2.0            # the surface showing between segments


def get(key: str) -> dict[str, Any] | None:
    return BY_KEY.get(key)


def label_for(key: str) -> str:
    stage = BY_KEY.get(key)
    return stage["label"] if stage else (key or "—")


def _point(angle: float, radius: float) -> tuple[float, float]:
    """A point on the circle. Angles run clockwise from twelve o'clock."""
    radians = math.radians(angle - 90)
    return (CENTRE + radius * math.cos(radians), CENTRE + radius * math.sin(radians))


def _wedge(start: float, end: float) -> str:
    """The SVG path for one segment of the ring."""
    large = 1 if (end - start) > 180 else 0
    x1, y1 = _point(start, OUTER)
    x2, y2 = _point(end, OUTER)
    x3, y3 = _point(end, INNER)
    x4, y4 = _point(start, INNER)
    return (f"M {x1:.2f} {y1:.2f} A {OUTER} {OUTER} 0 {large} 1 {x2:.2f} {y2:.2f} "
            f"L {x3:.2f} {y3:.2f} A {INNER} {INNER} 0 {large} 0 {x4:.2f} {y4:.2f} Z")


def overview() -> dict[str, Any]:
    """
    Every stage with its count, its share, and the arc that draws it.

    Stages holding nothing keep their place in the legend — a pipeline with an
    empty "Replied" is telling you something, and dropping the row hides it.
    """
    counts = db.stats()["by_status"]
    total = sum(counts.get(s["key"], 0) for s in STAGES)

    rows: list[dict[str, Any]] = []
    for stage in STAGES:
        count = int(counts.get(stage["key"], 0))
        rows.append({**stage, "count": count,
                     "pct": (100.0 * count / total) if total else 0.0,
                     "path": ""})

    filled = [r for r in rows if r["count"]]
    if len(filled) == 1:
        # One segment covering the whole ring: an arc from a point back to
        # itself draws nothing at all, so the ring is drawn as a ring.
        filled[0]["path"] = "ring"
    elif filled:
        # A gap of GAP_PX measured where the eye reads the boundary — at the
        # middle of the band, not at the outer edge where it would look wider.
        mid_radius = (OUTER + INNER) / 2
        pad = math.degrees(GAP_PX / mid_radius)
        angle = 0.0
        for row in filled:
            sweep = 360.0 * row["count"] / total
            row["path"] = _wedge(angle + pad / 2, angle + sweep - pad / 2)
            angle += sweep

    return {"stages": rows, "total": total, "size": SIZE, "centre": CENTRE,
            "outer": OUTER, "inner": INNER}


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
