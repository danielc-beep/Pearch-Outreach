"""
The client book and the year's money.

The dates carry these: signed is when the money was agreed, live is when the
term starts, and the app is wrong in an expensive way if it confuses them.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

import pytest

import db
import renewals
import revenue


def _client(name="Acme", won="2026-01-10", value=9000, **extra):
    return db.insert_business({"name": name, "status": "won", "deal_value": value,
                               "won_at": won, "masthead": "newcastleherald.com.au",
                               **extra})


# ---------- Counting months ----------

def test_a_year_later_is_the_same_day(client):
    assert renewals.add_months(date(2026, 3, 14), 12) == date(2027, 3, 14)


def test_a_short_month_clamps_rather_than_overflowing(client):
    """31 January plus a month is 28 February, not 3 March."""
    assert renewals.add_months(date(2026, 1, 31), 1) == date(2026, 2, 28)
    assert renewals.add_months(date(2026, 8, 31), 1) == date(2026, 9, 30)


def test_a_leap_day_lands_on_the_28th(client):
    assert renewals.add_months(date(2024, 2, 29), 12) == date(2025, 2, 28)


# ---------- The term starts when the content goes live ----------

def test_the_term_runs_from_go_live_not_from_the_signature(client):
    """Two weeks of writing sit between them, and billing the wrong end costs a year."""
    business_id = _client(won="2026-01-10")
    renewals.go_live(business_id, "2026-02-01", 12)
    card = renewals.book()["clients"][0]
    assert card["live_at"] == "2026-02-01"
    assert card["due"] == "2027-02-01", "a year from live, not from signed"


def test_a_won_deal_with_no_go_live_has_no_renewal_date(client):
    _client()
    card = renewals.book()["clients"][0]
    assert card["state"] == "not_live"
    assert card["due"] is None


def test_the_ones_with_no_go_live_sort_to_the_top(client):
    """Somebody has to publish their content; it is the most urgent row."""
    live = _client("Live one")
    renewals.go_live(live, "2026-01-01", 12)
    _client("Waiting", won="2026-06-01")
    assert renewals.book()["clients"][0]["name"] == "Waiting"


def test_going_live_does_not_invent_a_second_contract(client):
    """The first row was written from the deal before anyone knew the date."""
    business_id = _client()
    db.backfill_contracts()
    renewals.go_live(business_id, "2026-02-01", 12)
    contracts = db.list_contracts(business_id)
    assert len(contracts) == 1, "corrected, not duplicated"
    assert contracts[0]["started_at"] == "2026-02-01"


def test_a_silly_term_is_refused(client):
    business_id = _client()
    for months in (0, 61):
        with pytest.raises(ValueError):
            renewals.go_live(business_id, "2026-02-01", months)


# ---------- Where each client stands ----------

def test_the_states_run_from_urgent_to_quiet(client):
    """One axis, so the page can shade it along one ramp."""
    assert [s["key"] for s in renewals.STATES] == \
        ["not_live", "overdue", "imminent", "soon", "running"]


def test_a_client_moves_through_the_states_as_the_year_runs_out(client):
    business_id = _client()
    renewals.go_live(business_id, "2026-01-01", 12)
    business = db.get_business(business_id)
    contract = db.latest_contract(business_id)
    expected = [
        (date(2026, 2, 1), "running"),      # eleven months to go
        (date(2026, 11, 1), "soon"),        # two months
        (date(2026, 12, 15), "imminent"),   # a fortnight
        (date(2027, 2, 1), "overdue"),      # a month past
    ]
    for today, want in expected:
        assert renewals.state_of(business, contract, today)["key"] == want, today


def test_the_thresholds_are_where_they_say_they_are(client):
    business_id = _client()
    renewals.go_live(business_id, "2026-01-01", 12)
    business, contract = db.get_business(business_id), db.latest_contract(business_id)

    def on(day):
        return renewals.state_of(business, contract, day)["key"]

    due = date(2027, 1, 1)
    assert on(renewals.add_months(due, 0)) == "imminent", "the day itself is not overdue"
    assert on(date(2026, 12, 2)) == "imminent", "30 days out"
    assert on(date(2026, 12, 1)) == "soon", "31 days out"
    assert on(date(2026, 10, 3)) == "soon", "90 days out"
    assert on(date(2026, 10, 2)) == "running", "91 days out"


def test_the_page_totals_what_is_up_inside_three_months(client):
    soon = _client("Soon", value=12000)
    renewals.go_live(soon, renewals.add_months(renewals._today(), -11).isoformat(), 12)
    later = _client("Later", value=5000)
    renewals.go_live(later, renewals._today().isoformat(), 12)
    book = renewals.book()
    assert book["at_risk"] == 1
    assert book["at_risk_value"] == 12000


# ---------- Renewing ----------

def test_a_renewal_starts_where_the_last_term_ended(client):
    """Renewing early does not shorten what they bought."""
    business_id = _client()
    renewals.go_live(business_id, "2026-01-01", 12)
    renewals.renew(business_id, 12, 11000, signed_at="2026-11-15")
    card = renewals.book()["clients"][0]
    assert card["due"] == "2028-01-01", "the second year runs from the end of the first"
    assert card["renewals"] == 1


def test_a_renewal_is_revenue_in_the_month_it_was_signed(client):
    business_id = _client(won="2026-01-10")
    renewals.go_live(business_id, "2026-01-01", 12)
    renewals.renew(business_id, 12, 11000, signed_at="2026-11-15")
    november = db.revenue_by_month(2026)[10]
    assert november["renewal"] == 11000
    assert november["new"] == 0


def test_renewing_something_that_never_went_live_is_refused(client):
    business_id = _client()
    with pytest.raises(ValueError, match="no go-live date"):
        renewals.renew(business_id)


def test_a_client_who_leaves_is_not_a_lost_deal(client):
    """A year served and not renewed is a different animal from a prospect saying no."""
    business_id = _client()
    renewals.go_live(business_id, "2026-01-01", 12)
    renewals.churn(business_id, "Budget cut")
    assert db.get_business(business_id)["status"] == "won", "they did buy"
    assert db.get_business(business_id)["churned_at"]
    assert renewals.book()["total"] == 0
    assert renewals.book()["churned"] == 1


def test_a_churn_can_be_undone(client):
    business_id = _client()
    renewals.go_live(business_id, "2026-01-01", 12)
    renewals.churn(business_id)
    renewals.unchurn(business_id)
    assert renewals.book()["total"] == 1


# ---------- The year against last year ----------

def test_revenue_lands_in_the_month_it_was_signed(client):
    business_id = _client(won="2026-03-02")
    db.backfill_contracts()
    renewals.go_live(business_id, "2026-04-15", 12)
    data = revenue.year(2026)
    assert data["months"][2]["total"] == 9000, "March, when it was signed"
    assert data["months"][3]["total"] == 0, "not April, when it went live"


def test_this_year_is_compared_with_the_same_stretch_of_last_year(client):
    """Eight months against eight, never against last year's full twelve."""
    _client("Early", won="2025-02-01", value=10000)
    _client("Late", won="2025-11-01", value=50000)
    _client("Now", won="2026-02-01", value=12000)
    db.backfill_contracts()
    data = revenue.year(2026, today=datetime(2026, 6, 15, tzinfo=timezone.utc))
    assert data["last_to_date"] == 10000, "November is not in the first six months"
    assert data["last_total"] == 60000
    assert data["to_date"] == 12000
    assert round(data["change"]) == 20


def test_growth_from_nothing_is_not_a_percentage(client):
    _client("First", won="2026-02-01", value=9000)
    db.backfill_contracts()
    data = revenue.year(2026)
    assert data["change"] is None, "a first year is not infinite growth"
    assert data["has_last_year"] is False


def test_every_month_is_drawn_even_the_empty_ones(client):
    _client(won="2026-03-01")
    db.backfill_contracts()
    data = revenue.year(2026)
    assert len(data["months"]) == 12
    assert len(revenue.chart(data)["bars"]) == 12


def test_new_business_and_renewals_are_counted_apart(client):
    business_id = _client(won="2026-01-10", value=9000)
    renewals.go_live(business_id, "2026-01-15", 12)
    renewals.renew(business_id, 12, 11000, signed_at="2026-12-20")
    data = revenue.year(2026)
    assert data["new"] == 9000
    assert data["renewal"] == 11000
    assert data["total"] == 20000


def test_the_ledger_is_filled_in_from_deals_that_predate_it(client):
    """Otherwise a database full of won business reports a year with nothing in it."""
    _client(won="2025-05-05", value=7000)
    assert db.backfill_contracts() == 1
    assert db.backfill_contracts() == 0, "and only once"
    assert revenue.year(2025)["total"] == 7000


def test_the_chart_never_scales_to_nothing(client):
    chart = revenue.chart(revenue.year(2026))
    assert chart["top"] > 0
    assert all(bar["new_h"] >= 0 for bar in chart["bars"])


# ---------- On the page ----------

def test_the_page_shows_the_year_and_the_book(client):
    business_id = _client(won="2026-02-01", value=9000)
    renewals.go_live(business_id, "2026-02-14", 12)
    body = client.get("/revenue").text
    assert "Acme" in body
    assert "2026" in body and "2025" in body
    assert "undefined" not in body


def test_the_obvious_url_lands_on_the_renewals_half(client):
    got = client.get("/renewals", follow_redirects=False)
    assert got.status_code == 308
    assert got.headers["location"] == "/revenue#renewals"


def test_setting_go_live_from_the_page(client):
    business_id = _client()
    got = client.post(f"/api/renewals/{business_id}/live",
                      json={"when": "2026-03-01", "months": 12, "value": 9000}).json()
    assert got["live_at"] == "2026-03-01"


def test_a_bad_term_is_a_message_not_a_crash(client):
    business_id = _client()
    got = client.post(f"/api/renewals/{business_id}/live",
                      json={"when": "2026-03-01", "months": 0})
    assert got.status_code == 400
    assert "between 1 and 60" in got.json()["detail"]


def test_a_backfilled_contract_does_not_make_a_client_look_live(client):
    """
    The backfill had to put something in started_at for deals that predate the
    ledger and used the day they signed. Trusting that alone reported clients
    as running a term nobody had started, dated a year from the wrong end.
    """
    business_id = _client(won="2026-01-10")
    db.backfill_contracts()
    assert db.latest_contract(business_id) is not None, "the row exists"
    card = renewals.book()["clients"][0]
    assert card["state"] == "not_live"
    assert card["due"] is None
