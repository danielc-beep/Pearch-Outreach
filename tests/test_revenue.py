"""
Money on the dashboard.

Two numbers a sales manager asks for before any other: what is in the
pipeline, and what has been won. Both are sums of what somebody said each
business is worth, split by the stage it is standing at.
"""
from __future__ import annotations

import db


def _worth(name, status, value=None, **extra):
    fields = {"name": name, "industry": "Plumber", "suburb": "Bathurst",
              "source": "csv", "status": status, "rating": 4.6, "review_count": 30}
    if value is not None:
        fields["deal_value"] = value
    fields.update(extra)
    business_id, _ = db.upsert_business(fields)
    return business_id


def test_the_pipeline_is_the_open_stages(client):
    _worth("Qualified Co", "qualified", 5000)
    _worth("Contacted Co", "contacted", 7500)
    _worth("Replied Co", "replied", 10000)
    _worth("Still New Co", "new", 99999)        # not in the pipeline yet
    _worth("Won Co", "won", 8000)               # won is its own number
    money = db.revenue()
    assert money["pipeline"]["total"] == 22500
    assert money["pipeline"]["count"] == 3


def test_closed_won_is_its_own_number(client):
    _worth("Won One", "won", 9000)
    _worth("Won Two", "won", 6000)
    _worth("Lost One", "lost", 20000)           # lost is not won
    money = db.revenue()
    assert money["won"]["total"] == 15000
    assert money["won"]["count"] == 2


def test_a_business_nobody_has_priced_is_counted_separately(client):
    """
    Null is not nought. A pipeline number is worth having only if you know
    how much of it somebody actually agreed to.
    """
    _worth("Priced Co", "contacted", 5000)
    _worth("Unpriced Co", "contacted")
    money = db.revenue()
    assert money["pipeline"]["priced"] == 1
    assert money["pipeline"]["unpriced"] == 1
    assert money["pipeline"]["set_total"] == 5000
    assert money["pipeline"]["total"] == 5000   # nothing invented


def test_a_house_rate_fills_in_the_unpriced_and_says_so(client):
    _worth("Priced Co", "contacted", 5000)
    _worth("Unpriced Co", "contacted")
    money = db.revenue(default_value=7500)
    assert money["pipeline"]["set_total"] == 5000
    assert money["pipeline"]["total"] == 12500
    assert money["pipeline"]["unpriced"] == 1   # and how many were filled in


def test_an_empty_database_is_nought_not_an_error(client):
    money = db.revenue(default_value=7500)
    assert money["pipeline"]["total"] == 0
    assert money["won"]["total"] == 0


# ---------- Setting a value ----------

def test_a_value_can_be_set_on_a_business(client):
    business_id = _worth("Bathurst Plumbing", "contacted")
    assert client.post(f"/api/businesses/{business_id}",
                       json={"deal_value": 7500}).status_code == 200
    assert db.get_business(business_id)["deal_value"] == 7500


def test_a_negative_or_absurd_value_is_refused(client):
    business_id = _worth("Bathurst Plumbing", "contacted")
    for silly in (-1, 10_000_001):
        assert client.post(f"/api/businesses/{business_id}",
                           json={"deal_value": silly}).status_code == 400
    assert db.get_business(business_id)["deal_value"] is None


def test_the_dashboard_shows_both_numbers(client):
    _worth("Contacted Co", "contacted", 7500)
    _worth("Won Co", "won", 9000)
    body = client.get("/").text
    assert "Pipeline revenue" in body
    assert "7500" in body and "9000" in body


def test_the_endpoint_carries_them_for_the_live_refresh(client):
    _worth("Contacted Co", "contacted", 7500)
    money = client.get("/api/dashboard").json()["revenue"]
    assert money["pipeline"]["total"] == 7500


def test_the_crm_card_shows_the_value(client):
    _worth("Contacted Co", "contacted", 7500, masthead="westernadvocate.com.au",
           email="sarah@x.com.au", website="https://x.com.au")
    body = client.get("/crm").text
    assert "$7,500" in body


def test_a_card_with_no_value_offers_to_take_one(client):
    _worth("Contacted Co", "contacted", masthead="westernadvocate.com.au",
           email="sarah@x.com.au", website="https://x.com.au")
    assert "add $" in client.get("/crm").text
