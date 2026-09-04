"""
The pre-send safety net.

This is commercial email going out under ACM's name to real businesses. The
checks here are the difference between a tool and a liability, so they are
split deliberately: preflight refuses, warnings only tell you.
"""
from __future__ import annotations

from unittest.mock import patch

import httpx
import pytest

import db
import outreach


def _business(email="hello@wisebuygroup.com.au", name="Wisebuy Home Loans"):
    business_id, _ = db.upsert_business({
        "name": name, "suburb": "Cooks Hill", "region": "Newcastle", "state": "NSW",
        "email": email, "rating": 4.9, "review_count": 134, "industry": "Mortgage Broker",
        "masthead": "newcastleherald.com.au", "source": "google_places",
    })
    return business_id


def _approved(email="hello@wisebuygroup.com.au", name="Wisebuy Home Loans"):
    message = outreach.draft_message(_business(email, name), use_ai=False)
    outreach.approve_message(int(message["id"]))
    return db.get_message(int(message["id"]))


# ---------- The preview ----------

def test_the_preview_is_the_whole_assembled_email(client):
    message = _approved()
    preview = outreach.preview(int(message["id"]), "https://acm.example.com")
    assert preview["from"] == "ACM AEO Team <AEOteam@austcommunitymedia.com.au>"
    assert preview["reply_to"] == "danielc@austcommunitymedia.com.au"
    assert preview["masthead"] == "the Newcastle Herald"
    # The parts nobody sees until they go out.
    assert "The Newcastle Herald · Australian Community Media" in preview["body"]
    assert "https://acm.example.com/unsubscribe?email=" in preview["body"]
    assert "<p style=" in preview["html"]


def test_the_preview_carries_the_verdicts_too(client):
    preview = outreach.preview(int(_approved()["id"]))
    assert isinstance(preview["problems"], list)
    assert isinstance(preview["warnings"], list)


def test_an_unknown_message_has_no_preview(client):
    assert outreach.preview(999999) is None


def test_the_preview_route(client):
    message = _approved()
    assert client.get(f"/api/messages/{message['id']}/preview").status_code == 200
    assert client.get("/api/messages/999999/preview").status_code == 404


# ---------- Warnings: said, not enforced ----------

@pytest.mark.parametrize("email", ["info@a.com.au", "admin@a.com.au", "accounts@a.com.au",
                                   "enquiries@a.com.au", "sales@a.com.au"])
def test_a_shared_address_is_flagged_but_allowed(client, email):
    message = _approved(email)
    assert any("shared address" in w for w in outreach.warnings(message))
    # And it is not a blocker: for a small business info@ is often all there is.
    assert not any("shared address" in p for p in outreach.preflight(message))


def test_a_personal_address_is_not_flagged(client):
    assert outreach.warnings(_approved("sarah@wisebuygroup.com.au")) == []


def test_a_second_email_to_the_same_firm_is_flagged(client):
    """A sweep of five trades finds two partners at one firm often enough."""
    first = _approved("sarah@samefirm.com.au", "Same Firm Partners")
    db.update_message(int(first["id"]), {"status": "sent", "sent_at": db.now()})

    second = _approved("james@samefirm.com.au", "Same Firm Advisory")
    notes = outreach.warnings(second)
    assert any("Already emailed" in n and "sarah@samefirm.com.au" in n for n in notes)


def test_a_different_firm_is_not_flagged_as_a_duplicate(client):
    first = _approved("sarah@firmone.com.au", "Firm One")
    db.update_message(int(first["id"]), {"status": "sent", "sent_at": db.now()})
    assert outreach.warnings(_approved("james@firmtwo.com.au", "Firm Two")) == []


def test_an_old_send_to_the_same_firm_is_not_flagged(client):
    first = _approved("sarah@samefirm.com.au", "Same Firm Partners")
    db.update_message(int(first["id"]),
                      {"status": "sent", "sent_at": "2020-01-01T00:00:00"})
    assert outreach.warnings(_approved("james@samefirm.com.au", "Same Firm Advisory")) == []


def test_a_message_does_not_flag_itself(client):
    message = _approved("sarah@samefirm.com.au")
    db.update_message(int(message["id"]), {"status": "sent", "sent_at": db.now()})
    assert outreach.warnings(db.get_message(int(message["id"]))) == []


# ---------- Blockers ----------

def test_an_unattended_address_is_refused(client):
    for email in ("noreply@a.com.au", "no-reply@a.com.au", "postmaster@a.com.au"):
        message = _approved(email)
        assert any("unattended" in p for p in outreach.preflight(message)), email


# ---------- Sending the approved queue ----------

def _live(monkeypatch, ok=True):
    """Sending switched on, with the provider mocked."""
    monkeypatch.setattr(outreach, "SEND_ENABLED", True)
    monkeypatch.setattr(outreach, "RESEND_API_KEY", "re_test")
    original = httpx.Client

    def handler(request):
        return (httpx.Response(200, json={"id": "msg_1"}) if ok
                else httpx.Response(500, json={"message": "provider down"}))
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(outreach.httpx, "Client",
                        lambda *a, **kw: original(*a, **{**kw, "transport": transport}))


def test_the_batch_sends_the_approved_queue(client, monkeypatch):
    _live(monkeypatch)
    for i in range(3):
        _approved(f"sarah{i}@firm{i}.com.au", f"Firm {i}")
    result = outreach.send_approved(limit=10)
    assert result["sent"] == 3
    assert result["failed"] == 0
    assert len(db.list_messages(status="sent", limit=99)) == 3


def test_the_batch_never_goes_past_the_daily_cap(client, monkeypatch):
    _live(monkeypatch)
    monkeypatch.setattr(outreach, "DAILY_SEND_CAP", 2)
    for i in range(5):
        _approved(f"sarah{i}@firm{i}.com.au", f"Firm {i}")
    result = outreach.send_approved(limit=10)
    assert result["sent"] == 2
    assert result["capped"] is True
    # The rest are untouched and still approved.
    assert len(db.list_messages(status="approved", limit=99)) == 3


def test_nothing_is_sent_once_the_cap_is_already_reached(client, monkeypatch):
    _live(monkeypatch)
    monkeypatch.setattr(outreach, "DAILY_SEND_CAP", 1)
    _approved("sarah0@firm0.com.au", "Firm 0")
    _approved("sarah1@firm1.com.au", "Firm 1")
    outreach.send_approved(limit=10)
    again = outreach.send_approved(limit=10)
    assert again["sent"] == 0
    assert "cap" in again.get("note", "").lower()


def test_one_failure_does_not_stop_the_batch(client, monkeypatch):
    _live(monkeypatch)
    _approved("sarah@firmone.com.au", "Firm One")
    blocked = _approved("sarah@firmtwo.com.au", "Firm Two")
    db.suppress("sarah@firmtwo.com.au", "asked to stop")
    _approved("sarah@firmthree.com.au", "Firm Three")

    result = outreach.send_approved(limit=10)
    assert result["sent"] == 2
    assert result["failed"] == 1
    assert any("Firm Two" in p for p in result["problems"])
    assert db.get_message(int(blocked["id"]))["status"] != "sent"


def test_sending_disabled_means_nothing_leaves(client):
    _approved()
    result = outreach.send_approved(limit=10)
    assert result["sent"] == 0
    assert result["failed"] == 1


def test_the_batch_route_caps_its_own_size(client, monkeypatch):
    _live(monkeypatch)
    for i in range(3):
        _approved(f"sarah{i}@firm{i}.com.au", f"Firm {i}")
    body = client.post("/api/messages/send-approved", json={"limit": 500}).json()
    assert body["sent"] == 3
    assert body["daily_cap"] == outreach.DAILY_SEND_CAP
