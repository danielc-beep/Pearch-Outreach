"""
The client book: when content went live, and when each client is up again.

A signed client is not a finished deal. They pay for twelve months from the
day their content goes live, and somebody has to know when that year is up
while there is still time to do something about it. Two dates make the whole
thing work and they are not the same date:

- **signed** is when the money was agreed. It belongs to the year's revenue.
- **live** is when the content was published. It starts the term.

There are usually a couple of weeks of writing between them, and billing a
twelve-month term from the wrong end of that gap is how a renewal gets missed.

A won deal with no go-live date is not an oversight to hide — it is a client
whose content nobody has published yet, which is the most urgent row on the
page rather than one to leave off it.
"""
from __future__ import annotations

import calendar
import logging
from datetime import date, datetime, timezone
from typing import Any

import db

log = logging.getLogger(__name__)

DEFAULT_TERM = 12

# How far ahead a renewal starts being this month's problem. A quarter is
# about the shortest notice on which a twelve-month renewal conversation can
# be had without it sounding like a scramble.
SOON_DAYS = 90
IMMINENT_DAYS = 30

# The states a client can be in, in the order they matter on the page. Ordinal
# rather than categorical: this is one axis — how urgently somebody needs to
# pick up the phone — and the page shades it accordingly.
STATES = [
    {"key": "not_live", "label": "Not live yet",
     "blurb": "Signed, but the content has not been published. The term has not started."},
    {"key": "overdue", "label": "Overdue",
     "blurb": "The term has run out. They are past their renewal date."},
    {"key": "imminent", "label": "Up within a month", "blurb": "Ring them this week."},
    {"key": "soon", "label": "Up within three months", "blurb": "Get it in the diary."},
    {"key": "running", "label": "Running", "blurb": "Live, paid up, nothing due."},
]
BY_STATE = {s["key"]: s for s in STATES}
ORDER = [s["key"] for s in STATES]


def _today() -> date:
    return datetime.now(timezone.utc).date()


def _parse(value: str | None) -> date | None:
    """A stored date, however much of a timestamp it happens to carry."""
    text = (value or "").strip()[:10]
    if not text:
        return None
    try:
        return date.fromisoformat(text)
    except ValueError:
        return None


def add_months(start: date, months: int) -> date:
    """
    The same day, `months` later, clamped to the end of a shorter month.

    31 January plus one month is 28 February, not 3 March. Written out rather
    than pulled from dateutil, which is installed here only as somebody else's
    dependency and could go at any upgrade.
    """
    month_index = start.month - 1 + int(months)
    year = start.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def term_of(business: dict[str, Any]) -> int:
    months = business.get("term_months")
    return int(months) if months else DEFAULT_TERM


def renewal_date(business: dict[str, Any],
                 contract: dict[str, Any] | None = None) -> date | None:
    """
    When this client is next up, or None if the term has not started.

    Reads the running contract when there is one, because a renewal moves the
    date and the business row does not know that. Falls back to the go-live
    date for a client whose contract row predates the ledger.
    """
    if contract:
        started = _parse(contract.get("started_at"))
        if started:
            return add_months(started, int(contract.get("months") or DEFAULT_TERM))
    live = _parse(business.get("live_at"))
    return add_months(live, term_of(business)) if live else None


def state_of(business: dict[str, Any], contract: dict[str, Any] | None = None,
             today: date | None = None) -> dict[str, Any]:
    """Where this client stands, and how many days it is until it changes."""
    today = today or _today()
    if business.get("churned_at"):
        return {"key": "churned", "label": "Left", "due": None, "days": None}
    # No go-live date means nobody has published their content, whatever a
    # contract row says. The backfill had to put something in started_at for
    # deals that predate the ledger, and it used the day they signed — so
    # trusting that alone reported clients as running a term that has not
    # begun, with a renewal date a year out from the wrong end.
    due = renewal_date(business, contract) if business.get("live_at") else None
    if due is None:
        return {"key": "not_live", "label": BY_STATE["not_live"]["label"],
                "due": None, "days": None}
    days = (due - today).days
    if days < 0:
        key = "overdue"
    elif days <= IMMINENT_DAYS:
        key = "imminent"
    elif days <= SOON_DAYS:
        key = "soon"
    else:
        key = "running"
    return {"key": key, "label": BY_STATE[key]["label"], "due": due.isoformat(),
            "days": days}


def _card(business: dict[str, Any], today: date) -> dict[str, Any]:
    contract = db.latest_contract(int(business["id"]))
    state = state_of(business, contract, today)
    live = _parse(business.get("live_at"))
    contracts = db.list_contracts(int(business["id"]))
    return {
        **business,
        "state": state["key"],
        "state_label": state["label"],
        "due": state["due"],
        "days": state["days"],
        "live_at": live.isoformat() if live else None,
        "term": int(contract["months"]) if contract else term_of(business),
        "value": float(contract["value"]) if contract else (business.get("deal_value") or 0.0),
        "renewals": sum(1 for c in contracts if c["kind"] == "renewal"),
        "years": len(contracts),
    }


def book(include_churned: bool = False, today: date | None = None) -> dict[str, Any]:
    """
    Every client, sorted by how soon somebody needs to do something.

    Not by renewal date: a client with no go-live date has no renewal date at
    all, and sorting on a missing value would drop the row that needs doing
    first to the bottom of the page.
    """
    today = today or _today()
    cards = [_card(b, today) for b in db.clients(include_churned=include_churned)]
    cards.sort(key=lambda c: (ORDER.index(c["state"]) if c["state"] in ORDER else 99,
                              c["days"] if c["days"] is not None else 0))

    counts = {key: sum(1 for c in cards if c["state"] == key) for key in ORDER}
    at_risk = [c for c in cards if c["state"] in ("overdue", "imminent", "soon")]
    return {
        "clients": cards,
        "counts": counts,
        "states": STATES,
        "total": len(cards),
        "live": sum(1 for c in cards if c["state"] != "not_live"),
        # What is on the table in the next quarter, which is the number this
        # page exists to put in front of somebody.
        "at_risk_value": sum(c["value"] for c in at_risk),
        "at_risk": len(at_risk),
        "book_value": sum(c["value"] for c in cards),
        "churned": db.count_churned(),
    }


def go_live(business_id: int, when: str = "", months: int | None = None,
            value: float | None = None) -> dict[str, Any] | None:
    """
    Record the day this client's content went live, which starts their term.

    Writes the contract row too, because a term with no contract behind it
    would be a renewal date the revenue ledger knows nothing about.
    """
    business = db.get_business(business_id)
    if not business:
        return None
    day = _parse(when) or _today()
    months = DEFAULT_TERM if months is None else int(months)
    if not 1 <= months <= 60:
        raise ValueError("A term runs between 1 and 60 months.")
    amount = float(value if value is not None else (business.get("deal_value") or 0))

    db.update_business(business_id, {"live_at": day.isoformat(), "term_months": months,
                                     "deal_value": amount,
                                     # Going live means they are a client, whatever
                                     # the board said a moment ago.
                                     "status": "won",
                                     "won_at": business.get("won_at") or day.isoformat()})
    existing = db.list_contracts(business_id)
    if existing:
        # The first contract was written from the deal before anyone knew the
        # go-live date. Correct it rather than adding a second one, or the
        # client would appear to have bought two years at once.
        first = existing[0]
        db.update_contract(int(first["id"]), {"started_at": day.isoformat(),
                                              "months": months, "value": amount})
    else:
        signed = (business.get("won_at") or day.isoformat())[:10]
        db.add_contract(business_id, amount, signed, day.isoformat(), months, "new")
    db.log_activity(business_id, "live",
                    f"Content live {day.isoformat()} — {months}-month term")
    return db.get_business(business_id)


def renew(business_id: int, months: int | None = None, value: float | None = None,
          signed_at: str = "") -> dict[str, Any] | None:
    """
    Sign another term, starting the day the last one ends.

    Starting from the end of the current term rather than from today is the
    whole point: a client renewed a fortnight early has not bought eleven and
    a half months, and a client renewed a fortnight late has not lost a
    fortnight.
    """
    business = db.get_business(business_id)
    if not business:
        return None
    current = db.latest_contract(business_id)
    ends = renewal_date(business, current)
    if ends is None:
        raise ValueError(f"{business['name']} has no go-live date, so there is "
                         f"nothing to renew yet.")
    months = DEFAULT_TERM if months is None else int(months)
    if not 1 <= months <= 60:
        raise ValueError("A term runs between 1 and 60 months.")
    amount = float(value if value is not None else
                   (current["value"] if current else business.get("deal_value") or 0))

    db.add_contract(business_id, amount, (_parse(signed_at) or _today()).isoformat(),
                    ends.isoformat(), months, "renewal")
    db.update_business(business_id, {"churned_at": None, "term_months": months})
    db.log_activity(business_id, "renewed",
                    f"Renewed to {add_months(ends, months).isoformat()} — ${amount:,.0f}")
    return db.get_business(business_id)


def churn(business_id: int, why: str = "", when: str = "") -> dict[str, Any] | None:
    """
    Record that a client did not renew.

    Deliberately not the same as losing a deal. A client who ran a year and
    left is a different animal from a prospect who said no, and folding them
    together would flatter the loss column and hide the churn.
    """
    business = db.get_business(business_id)
    if not business:
        return None
    day = (_parse(when) or _today()).isoformat()
    db.update_business(business_id, {"churned_at": day})
    db.log_activity(business_id, "churned", why or "Did not renew")
    return db.get_business(business_id)


def unchurn(business_id: int) -> dict[str, Any] | None:
    """Undo a churn, for when somebody marked the wrong client."""
    if not db.get_business(business_id):
        return None
    db.update_business(business_id, {"churned_at": None})
    db.log_activity(business_id, "churned", "Marked as still a client")
    return db.get_business(business_id)
