"""
The network as a map: all 78 ACM mastheads, and what each one is worth.

The dashboard used to carry a "Coverage" panel that listed the three
mastheads with the most records. It answered a question nobody asks. The
useful question is the other way round — which of our own papers has nobody
touched — and answering it needs every title on screen, not the top three.

So this joins the live book onto the full list of mastheads and reports the
empty ones as loudly as the busy ones. A masthead with no prospects is not
missing data, it is the next patch to sweep, and it is one click from a
search of its home town.
"""
from __future__ import annotations

from typing import Any

import db
import mastheads
import revenue

# The bands a masthead's book can be in, coldest first. Ordinal, not
# categorical — this is one quantity in four steps, so the board shades it
# along a single ramp rather than giving each step an unrelated colour.
BANDS = [
    {"key": "untouched", "label": "Untouched", "blurb": "No prospects at all."},
    {"key": "found",     "label": "Found",     "blurb": "Prospects on the books, none pitched."},
    {"key": "working",   "label": "Working",   "blurb": "Emails have gone out."},
    {"key": "earning",   "label": "Earning",   "blurb": "At least one signed."},
]


def _band(row: dict[str, Any]) -> str:
    if row["won"]:
        return "earning"
    if row["pitched"]:
        return "working"
    if row["prospects"]:
        return "found"
    return "untouched"


def board() -> dict[str, Any]:
    """
    Every masthead, grouped by state, with its book attached.

    Titles carrying prospects that are not one of the 78 are gathered under
    "Not aligned" rather than dropped: they are real records and they need
    fixing, so hiding them would be the wrong kind of tidy.
    """
    live = {row["site"]: row for row in db.masthead_performance()}
    blank = {"prospects": 0, "contactable": 0, "pitched": 0, "replied": 0,
             "won": 0, "revenue": 0.0, "pipeline": 0.0}

    groups: list[dict[str, Any]] = []
    seen: set[str] = set()
    for group in mastheads.options():
        titles = []
        for title in group["titles"]:
            row = live.get(title["site"], blank)
            seen.add(title["site"])
            titles.append({
                **title, **row,
                "band": _band(row),
                "patch": mastheads.home_location(title["site"]),
                "pipeline_est": row["pipeline"] or row["pitched"] * revenue.default_value(),
            })
        groups.append({
            "state": group["state"],
            "titles": titles,
            "prospects": sum(t["prospects"] for t in titles),
            "won": sum(t["won"] for t in titles),
            "revenue": sum(t["revenue"] for t in titles),
            "untouched": sum(1 for t in titles if t["band"] == "untouched"),
        })

    stray = sum(row["prospects"] for site, row in live.items() if site not in seen)
    titles = [t for g in groups for t in g["titles"]]
    return {
        "groups": groups,
        "totals": {
            "titles": len(titles),
            "untouched": sum(1 for t in titles if t["band"] == "untouched"),
            "working": sum(1 for t in titles if t["band"] in ("working", "earning")),
            "earning": sum(1 for t in titles if t["band"] == "earning"),
            "prospects": sum(t["prospects"] for t in titles),
            "revenue": sum(t["revenue"] for t in titles),
            "unaligned": stray,
        },
        "bands": BANDS,
    }


def next_to_sweep(limit: int = 6) -> list[dict[str, Any]]:
    """
    The untouched mastheads worth doing first — the ones with a town to search.

    A national or rural title has no patch, so a territory run cannot sweep it
    and suggesting one would be advice you cannot take.
    """
    got = board()
    empty = [t for g in got["groups"] for t in g["titles"]
             if t["band"] == "untouched" and t["patch"]]
    return empty[:limit]
