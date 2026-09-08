"""
The year's money, month by month, against the same months last year.

Two decisions shape this.

**Where the number comes from.** The contracts ledger, not the businesses
table. A renewal is revenue and it is not a new deal, so counting won_at
alone would report a client's second year as nothing at all. Revenue lands in
the month a contract was *signed*, which is when the money was agreed —
distinct from the month it *starts*, which is when the content goes live and
the term begins.

**How last year is drawn.** As a reference mark on each bar rather than a
second coloured series. Last year is not a peer of this year competing for
the eye, it is the line this year is being measured against — and a neutral
tick on a cyan bar reads as exactly that, where two similar-lightness lines
on a dark ground read as a puzzle. It also sidesteps a real problem: against
this page's ink, cyan and any muted slate come out about eleven units apart
in normal vision, which the palette validator rejects outright.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import db
from config import DEFAULT_DEAL_VALUE

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun",
          "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

# ---------- What an unpriced deal is worth ----------
# A pipeline number is only worth having if you know how much of it somebody
# actually agreed to, so this fills in for deals nobody has priced and is
# always reported separately from the ones that carry a real figure.
#
# It lives in the database rather than in an environment variable, for the
# same reason the password does: it is a pricing decision somebody makes in a
# meeting, not a hosting setting anybody should file a ticket to change. The
# env var stays as the fallback for a fresh install.

SETTING = "default_deal_value"
MAX_DEAL = 10_000_000.0


def default_value() -> float:
    """What one unpriced deal counts as. Set in the app, or from the env."""
    stored = db.get_setting(SETTING)
    if stored:
        try:
            return max(0.0, float(stored))
        except ValueError:
            pass
    return float(DEFAULT_DEAL_VALUE)


def set_default_value(amount: float) -> float:
    """Change it. Bounded, because a typo here silently inflates every figure."""
    amount = float(amount)
    if not 0 <= amount <= MAX_DEAL:
        raise ValueError(f"A deal value runs between $0 and ${MAX_DEAL:,.0f}.")
    db.set_setting(SETTING, repr(amount))
    return amount


def suggested_value() -> float:
    """
    What to put in the box before anybody has decided.

    The middle of what has already been priced, rounded to something a person
    would say out loud. Better than a number this app invented, because it is
    the business's own.
    """
    middle = db.median_deal_value()
    if not middle:
        return 0.0
    step = 500 if middle < 20_000 else 1000
    return float(round(middle / step) * step)


def this_year() -> int:
    return datetime.now(timezone.utc).year


def _cumulative(months: list[dict[str, Any]]) -> list[float]:
    running, out = 0.0, []
    for month in months:
        running += month["total"]
        out.append(running)
    return out


def _change(now: float, before: float) -> float | None:
    """
    Percentage change, or None when there is nothing to change from.

    Growth from zero is not infinite growth, it is a first year — and a page
    that prints "+∞%" or "+100%" for it is lying about something nobody
    needed a number for.
    """
    if not before:
        return None
    return 100.0 * (now - before) / before


def year(target_year: int | None = None, today: datetime | None = None) -> dict[str, Any]:
    """
    A full year of revenue, and the same stretch of the year before it.

    Year-to-date is compared like for like: eight months of this year against
    the first eight of last, never against last year's complete twelve. That
    comparison is the one people actually make in September, and getting it
    wrong makes a good year look like a collapse.
    """
    today = today or datetime.now(timezone.utc)
    target_year = int(target_year or today.year)
    months = db.revenue_by_month(target_year)
    before = db.revenue_by_month(target_year - 1)

    # How much of the year has been lived. A past year is complete; the
    # current one runs to this month.
    through = today.month if target_year == today.year else 12

    total = sum(m["total"] for m in months)
    to_date = sum(m["total"] for m in months[:through])
    last_to_date = sum(m["total"] for m in before[:through])
    last_total = sum(m["total"] for m in before)

    rows = []
    for i, month in enumerate(months):
        rows.append({
            **month, "name": MONTHS[i],
            "last": before[i]["total"],
            "ahead": month["total"] - before[i]["total"],
            "future": (i + 1) > through,
        })

    return {
        "year": target_year,
        "last_year": target_year - 1,
        "months": rows,
        "through": through,
        "through_name": MONTHS[through - 1],
        "cumulative": _cumulative(months),
        "last_cumulative": _cumulative(before),
        "total": total,
        "new": sum(m["new"] for m in months),
        "renewal": sum(m["renewal"] for m in months),
        "deals": sum(m["deals"] for m in months),
        "to_date": to_date,
        "last_to_date": last_to_date,
        "last_total": last_total,
        "change": _change(to_date, last_to_date),
        "ahead_by": to_date - last_to_date,
        "best": max(rows[:through], key=lambda r: r["total"], default=None),
        "has_last_year": last_total > 0,
        "years": db.contract_years(),
    }


# ---------- The chart, worked out here so the template only draws it ----------

WIDTH, HEIGHT = 720.0, 200.0
PAD_LEFT, PAD_BOTTOM, PAD_TOP = 4.0, 22.0, 10.0


def _nice_ceiling(value: float) -> float:
    """A round number above the tallest bar, so the axis reads in whole steps."""
    if value <= 0:
        return 1000.0
    step = 10.0 ** (len(str(int(value))) - 1)
    for multiple in (1, 2, 2.5, 5, 10):
        if step * multiple >= value:
            return step * multiple
    return step * 10


def chart(data: dict[str, Any]) -> dict[str, Any]:
    """
    Bar geometry for one year, with last year as a reference tick on each bar.

    Every month is drawn whether or not anything was signed in it — skipping
    the empty ones turns a quiet August into a busy one.
    """
    top = _nice_ceiling(max([m["total"] for m in data["months"]] +
                            [m["last"] for m in data["months"]] + [0]))
    plot = HEIGHT - PAD_BOTTOM - PAD_TOP
    slot = (WIDTH - PAD_LEFT) / 12.0
    gap = slot * 0.26
    width = slot - gap

    bars = []
    for i, month in enumerate(data["months"]):
        x = PAD_LEFT + i * slot + gap / 2
        # Stacked, with a two-pixel hole between the segments so the join is a
        # boundary rather than a colour change.
        new_h = (month["new"] / top) * plot if top else 0
        renew_h = (month["renewal"] / top) * plot if top else 0
        base = PAD_TOP + plot
        bars.append({
            **month,
            "x": round(x, 2), "w": round(width, 2), "mid": round(x + width / 2, 2),
            "new_y": round(base - new_h, 2), "new_h": round(max(new_h, 0), 2),
            "renew_y": round(base - new_h - renew_h - (2 if new_h and renew_h else 0), 2),
            "renew_h": round(max(renew_h, 0), 2),
            "last_y": round(base - (month["last"] / top) * plot, 2) if top else base,
            "has_last": month["last"] > 0,
        })

    ticks = [{"value": top * f, "y": round(PAD_TOP + plot - plot * f, 2)}
             for f in (0, 0.5, 1.0)]
    return {"bars": bars, "ticks": ticks, "top": top, "base": round(PAD_TOP + plot, 2),
            "width": WIDTH, "height": HEIGHT}
