"""
The Adpoint booking number.

ACM invoices out of Adpoint, so a won deal with no booking number against it
is money agreed and nothing to bill — a different kind of missing from an
empty field, and worth its own line on the board.
"""
from __future__ import annotations

import db
import renewals


def _client(name="Acme Plumbing", **extra):
    return db.insert_business({"name": name, "status": "won", "deal_value": 9000,
                               "won_at": "2026-01-10", "suburb": "Newcastle",
                               "masthead": "newcastleherald.com.au", **extra})


def test_a_new_sale_starts_without_one(client):
    _client()
    assert db.clients_without_booking() == 1


def test_saving_it_clears_the_gap(client):
    business_id = _client()
    db.update_business(business_id, {"booking_ref": "AP-118342"})
    assert db.clients_without_booking() == 0
    assert db.get_business(business_id)["booking_ref"] == "AP-118342"


def test_whitespace_is_not_a_booking_number(client):
    business_id = _client()
    db.update_business(business_id, {"booking_ref": "   "})
    assert db.clients_without_booking() == 1


def test_a_client_who_left_is_not_chased_for_one(client):
    business_id = _client()
    renewals.go_live(business_id, "2026-02-01", 12)
    renewals.churn(business_id, "Budget cut")
    assert db.clients_without_booking() == 0


def test_a_prospect_is_not_chased_for_one(client):
    """Only a sale needs a booking; nothing has been agreed before that."""
    db.insert_business({"name": "Just a prospect", "status": "qualified"})
    assert db.clients_without_booking() == 0


# ---------- On the pages ----------

def test_the_client_card_carries_the_field(client):
    business_id = _client()
    body = client.get(f"/businesses/{business_id}").text
    assert 'id="booking-form"' in body
    assert "invoices out of Adpoint" in body


def test_a_prospect_page_does_not(client):
    business_id = db.insert_business({"name": "Prospect", "status": "new"})
    assert 'id="booking-form"' not in client.get(f"/businesses/{business_id}").text


def test_the_endpoint_saves_it_trimmed(client):
    business_id = _client()
    got = client.post(f"/api/businesses/{business_id}/booking",
                      json={"booking_ref": "  AP-118342  "}).json()
    assert got["booking_ref"] == "AP-118342"


def test_it_can_be_cleared(client):
    business_id = _client(booking_ref="AP-1")
    client.post(f"/api/businesses/{business_id}/booking", json={"booking_ref": ""})
    assert db.get_business(business_id)["booking_ref"] is None


def test_anything_adpoint_calls_a_reference_is_accepted(client):
    """Their references have changed shape before; a validator that rejects a
    real number is worse than a field that takes a typo somebody can see."""
    business_id = _client()
    for ref in ("AP-118342", "118342", "ACM/2026/0042", "BK 99-11"):
        got = client.post(f"/api/businesses/{business_id}/booking",
                          json={"booking_ref": ref}).json()
        assert got["booking_ref"] == ref


def test_the_client_book_shows_the_number_or_a_way_to_add_it(client):
    booked = _client("Booked Co", booking_ref="AP-118342")
    renewals.go_live(booked, "2026-02-01", 12)
    unbooked = _client("Unbooked Co")
    renewals.go_live(unbooked, "2026-03-01", 12)
    body = client.get("/revenue").text
    assert "Adpoint AP-118342" in body
    assert "No Adpoint number" in body
    assert f'href="/businesses/{unbooked}#booking"' in body


def test_the_book_counts_the_ones_without(client):
    _client("One")
    _client("Two", booking_ref="AP-2")
    assert renewals.book()["unbooked"] == 1


def test_the_dashboard_asks_for_them(client):
    _client()
    body = client.get("/").text
    assert "sales with no Adpoint number" in body


def test_a_jump_to_a_card_clears_the_sticky_nav():
    """
    The topbar sticks, so a link to #booking landed the heading underneath the
    nav — the thing you were sent to see hidden behind the thing that sent you.
    """
    from pathlib import Path
    css = (Path(__file__).resolve().parent.parent / "static" / "app.css").read_text()
    assert "#booking, #content, #reports, #renewals, #target { scroll-margin-top: 96px; }" in css
