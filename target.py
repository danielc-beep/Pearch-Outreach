"""
The number to hit, and whether we are going to.

A dashboard full of absolutes reports; it does not tell you anything. Closed
won of $41,000 is either a triumph or a disaster and the page had no way to
say which. A target turns every other figure on the screen into a position.

Kept in the settings table rather than the environment, because a target is a
decision somebody makes in a meeting and changes in June — not a deployment
setting they have to find a dashboard password for.
"""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any

import db

SETTING_AMOUNT = "revenue_target"
SETTING_PERIOD = "revenue_period"
PERIODS = ("month", "quarter", "year")


def get() -> dict[str, Any]:
    try:
        amount = float(db.get_setting(SETTING_AMOUNT, "0") or 0)
    except ValueError:
        amount = 0.0
    period = db.get_setting(SETTING_PERIOD, "quarter") or "quarter"
    return {"amount": amount, "period": period if period in PERIODS else "quarter"}


def set_target(amount: float, period: str = "quarter") -> dict[str, Any]:
    if amount < 0 or amount > 1_000_000_000:
        raise ValueError("A target has to be between 0 and a billion.")
    if period not in PERIODS:
        raise ValueError(f"Unknown period: {period}")
    db.set_setting(SETTING_AMOUNT, str(int(amount)))
    db.set_setting(SETTING_PERIOD, period)
    return get()


def window(period: str, today: date | None = None) -> tuple[date, date]:
    """The first and last day of the period we are inside."""
    today = today or datetime.now(timezone.utc).date()
    if period == "month":
        start = today.replace(day=1)
        end = (start + timedelta(days=32)).replace(day=1) - timedelta(days=1)
    elif period == "year":
        start = date(today.year, 1, 1)
        end = date(today.year, 12, 31)
    else:
        first_month = 3 * ((today.month - 1) // 3) + 1
        start = date(today.year, first_month, 1)
        end = (date(today.year + (first_month + 3 > 12), (first_month + 3 - 1) % 12 + 1, 1)
               - timedelta(days=1))
    return start, end


def progress(today: date | None = None) -> dict[str, Any]:
    """
    Where the period stands, and what it would take from here.

    `needed_weekly` is the honest version of a pace figure: what is left,
    divided by the weeks left, rather than a projection dressed up as a
    forecast. On the last day of the period it is simply what is left.
    """
    target = get()
    today = today or datetime.now(timezone.utc).date()
    start, end = window(target["period"], today)
    won = db.won_between(start.isoformat(), end.isoformat())

    days_total = (end - start).days + 1
    days_gone = min(days_total, max(0, (today - start).days + 1))
    days_left = max(0, (end - today).days)
    expected = target["amount"] * (days_gone / days_total) if target["amount"] else 0.0
    short = max(0.0, target["amount"] - won)

    return {
        "set": target["amount"] > 0,
        "amount": target["amount"],
        "period": target["period"],
        "start": start.isoformat(),
        "end": end.isoformat(),
        "won": won,
        "pct": (100.0 * won / target["amount"]) if target["amount"] else 0.0,
        "days_left": days_left,
        "weeks_left": max(1, round(days_left / 7)) if days_left else 0,
        "expected": expected,
        # Ahead of where the calendar says we should be, or behind it.
        "ahead_by": won - expected if target["amount"] else 0.0,
        "short": short,
        "needed_weekly": (short / max(1, round(days_left / 7))) if days_left and short else short,
    }
