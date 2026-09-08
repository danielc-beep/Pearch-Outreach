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


# ---------- Which trades are working ----------
# "Best of the list" ranked by fit score, and with scoring fixed almost
# everything scores in the nineties — so it ranked almost nothing.

def _trade(name, industry, status, value=None):
    fields = {"name": name, "industry": industry, "source": "csv", "status": status,
              "suburb": "Bathurst", "rating": 4.6, "review_count": 30}
    if value:
        fields["deal_value"] = value
    business_id, _ = db.upsert_business(fields)
    return business_id


def test_a_trade_is_measured_by_what_came_of_it(client):
    for i in range(6):
        _trade(f"P{i}", "Plumber", "contacted")
    _trade("P6", "Plumber", "replied")
    _trade("P7", "Plumber", "won", 9000)
    rows = {r["industry"]: r for r in db.industry_performance()}
    plumber = rows["Plumber"]
    assert plumber["prospects"] == 8
    assert plumber["reached_out"] == 8          # contacted, replied and won all count
    assert plumber["replied"] == 2              # a win replied first
    assert plumber["won"] == 1
    assert plumber["revenue"] == 9000


def test_a_rate_needs_a_base_worth_quoting(client):
    """Two replies out of three is three data points, not a 67% reply rate."""
    for i in range(3):
        _trade(f"S{i}", "Solicitor", "replied")
    solicitor = {r["industry"]: r for r in db.industry_performance()}["Solicitor"]
    assert solicitor["reply_rate"] is None
    assert solicitor["thin"] is True
    assert solicitor["replied"] == 3            # the count is still shown


def test_the_rate_appears_once_the_base_is_there(client):
    for i in range(5):
        _trade(f"D{i}", "Dentist", "contacted")
    _trade("D5", "Dentist", "replied")
    dentist = {r["industry"]: r for r in db.industry_performance()}["Dentist"]
    assert dentist["reply_rate"] is not None
    assert round(dentist["reply_rate"]) == 17   # 1 of 6


def test_unprospected_trades_do_not_crowd_out_working_ones(client):
    _trade("Idle", "Veterinarian", "new")
    _trade("Earner", "Plumber", "won", 9000)
    order = [r["industry"] for r in db.industry_performance()]
    assert order.index("Plumber") < order.index("Veterinarian")


def test_a_business_with_no_industry_is_still_counted(client):
    _trade("Nameless", "", "contacted")
    assert "Unsorted" in {r["industry"] for r in db.industry_performance()}


def test_the_dashboard_shows_the_trades_not_the_fit_ranking(client):
    _trade("Earner", "Plumber", "won", 9000)
    body = client.get("/").text
    assert "Which trades are working" in body
    assert "Best of the list" not in body
    assert "Plumber" in body


def test_the_empty_state_says_what_fills_it(client):
    assert "Nothing pitched yet" in client.get("/").text


# ---------- What an unpriced deal counts as ----------
# A pipeline number is worth having only if you know how much of it somebody
# actually agreed to, so the stand-in figure is always reported apart from the
# deals that carry a real one.

def test_it_starts_at_nothing_so_the_pipeline_is_not_a_guess(client):
    import revenue
    assert revenue.default_value() == 0.0


def test_setting_it_changes_every_figure_that_leans_on_it(client):
    """It is read live, so saving is enough — there is nothing to redeploy."""
    import crm
    import db
    import revenue
    for _ in range(3):
        db.insert_business({"name": "Open", "status": "qualified"})
    assert crm.summary()["pipeline"] == 0
    revenue.set_default_value(9000)
    assert crm.summary()["pipeline"] == 27000
    assert db.revenue(revenue.default_value())["pipeline"]["unpriced"] == 3


def test_a_priced_deal_is_never_overwritten_by_the_default(client):
    import db
    import revenue
    db.insert_business({"name": "Priced", "status": "qualified", "deal_value": 4000})
    db.insert_business({"name": "Not priced", "status": "qualified"})
    revenue.set_default_value(9000)
    money = db.revenue(revenue.default_value())["pipeline"]
    assert money["set_total"] == 4000, "what was actually agreed"
    assert money["total"] == 13000, "plus one stand-in"


def test_a_silly_figure_is_refused(client):
    import pytest
    import revenue
    for bad in (-1, 20_000_000):
        with pytest.raises(ValueError):
            revenue.set_default_value(bad)


def test_it_suggests_the_middle_of_what_you_have_already_priced(client):
    """Better than a number this app invented, because it is the business's own."""
    import db
    import revenue
    for value in (8000, 9000, 30000):
        db.insert_business({"name": f"Won {value}", "status": "won", "deal_value": value})
    assert db.median_deal_value() == 9000
    assert revenue.suggested_value() == 9000


def test_it_suggests_nothing_when_nothing_has_been_priced(client):
    import revenue
    assert revenue.suggested_value() == 0.0


def test_the_page_says_how_much_of_the_pipeline_is_a_guess(client):
    import db
    db.insert_business({"name": "Priced", "status": "qualified", "deal_value": 4000})
    db.insert_business({"name": "Not priced", "status": "qualified"})
    body = client.get("/revenue").text
    assert "open deals have no price on them" in body
    assert 'id="dv-edit"' in body


def test_the_endpoint_sets_it(client):
    import revenue
    got = client.post("/api/deal-value", json={"amount": 9500}).json()
    assert got["amount"] == 9500
    assert revenue.default_value() == 9500
    assert client.post("/api/deal-value", json={"amount": -5}).status_code == 400
