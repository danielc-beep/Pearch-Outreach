"""
Moving a business through the pipeline.

Status is the field that carries the whole workflow, so it has to be
changeable at any point by hand, and to move itself where the system already
knows what happened.
"""
from __future__ import annotations

import pytest

import db
import outreach


def _first(client):
    return client.get("/api/businesses").json()["businesses"][0]


@pytest.mark.parametrize("status", db.STATUSES)
def test_every_status_can_be_set_by_hand_at_any_time(client, sample_run, status):
    business = _first(client)
    response = client.post(f"/api/businesses/{business['id']}", json={"status": status})
    assert response.status_code == 200
    assert db.get_business(business["id"])["status"] == status


def test_status_can_be_changed_repeatedly_and_backwards(client, sample_run):
    """A prospect can go back a step — people mis-click, and deals cool off."""
    business = _first(client)
    for status in ["qualified", "contacted", "replied", "researching", "new"]:
        client.post(f"/api/businesses/{business['id']}", json={"status": status})
        assert db.get_business(business["id"])["status"] == status


def test_an_unknown_status_is_refused(client, sample_run):
    business = _first(client)
    response = client.post(f"/api/businesses/{business['id']}", json={"status": "warm"})
    assert response.status_code == 400
    assert db.get_business(business["id"])["status"] != "warm"


def test_sending_moves_a_business_to_contacted(client, sample_run, monkeypatch):
    """The system already knows the email went — it should not need telling."""
    business = db.list_businesses(has_email=True)[0][0]
    message = outreach.draft_message(business["id"], use_ai=False)
    outreach.approve_message(message["id"])

    monkeypatch.setattr(outreach, "SEND_ENABLED", True)
    monkeypatch.setattr(outreach, "RESEND_API_KEY", "re_test")
    monkeypatch.setattr(outreach, "_send_via_resend",
                        lambda *a, **kw: {"ok": True, "id": "msg_123"}, raising=False)

    import httpx
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={"id": "msg_123"}))
    original = httpx.Client
    monkeypatch.setattr(outreach.httpx, "Client",
                        lambda *a, **kw: original(*a, **{**kw, "transport": transport}))

    result = outreach.send_message(message["id"], "https://example.test")
    assert result["sent"] is True
    refreshed = db.get_business(business["id"])
    assert refreshed["status"] == "contacted"
    assert refreshed["last_contacted_at"]


def test_logging_a_reply_moves_them_to_replied(client, sample_run):
    business = _first(client)
    client.post(f"/api/businesses/{business['id']}", json={"status": "contacted"})

    response = client.post(f"/api/businesses/{business['id']}/replied",
                           json={"note": "Asked for pricing"})
    assert response.status_code == 200
    assert db.get_business(business["id"])["status"] == "replied"
    timeline = db.list_activities(business["id"])
    assert any("Asked for pricing" in a["detail"] for a in timeline)


@pytest.mark.parametrize("settled", ["won", "lost"])
def test_a_reply_never_drags_a_settled_deal_backwards(client, sample_run, settled):
    business = _first(client)
    client.post(f"/api/businesses/{business['id']}", json={"status": settled})
    client.post(f"/api/businesses/{business['id']}/replied", json={"note": "thanks"})

    assert db.get_business(business["id"])["status"] == settled
    assert any(a["kind"] == "replied" for a in db.list_activities(business["id"]))


def test_the_list_lets_status_be_changed_without_opening_a_record(client, sample_run):
    body = client.get("/businesses").text
    assert "js-row-status" in body
    assert body.count('class="js-row-status"') >= 1


def test_logging_a_reply_for_an_unknown_business_is_a_404(client):
    assert client.post("/api/businesses/999999/replied", json={"note": ""}).status_code == 404
