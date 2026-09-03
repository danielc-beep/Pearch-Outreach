"""
Editing a draft before it goes out.

Claude's draft is a starting point. A draft nobody can correct is worse than
no draft, because the alternative is sending something slightly wrong.
"""
from __future__ import annotations

import pytest

import db
import outreach


@pytest.fixture
def draft(sample_run):
    business = db.list_businesses(has_email=True)[0][0]
    return outreach.draft_message(business["id"], use_ai=False)


def test_a_draft_can_be_rewritten(client, draft):
    response = client.post(f"/api/messages/{draft['id']}", json={
        "subject": "Zhen Hair Studio and AI search answers",
        "body": "Hi Zhen,\n\nRewritten by a human.\n\nDaniel",
    })
    assert response.status_code == 200
    saved = db.get_message(draft["id"])
    assert saved["subject"] == "Zhen Hair Studio and AI search answers"
    assert "Rewritten by a human" in saved["body"]
    assert saved["status"] == "draft"


def test_editing_an_approved_message_withdraws_the_approval(client, draft):
    """The approval was for the text that has just been replaced."""
    outreach.approve_message(draft["id"])
    assert db.get_message(draft["id"])["status"] == "approved"

    client.post(f"/api/messages/{draft['id']}", json={"subject": "New", "body": "New body"})
    assert db.get_message(draft["id"])["status"] == "draft"

    timeline = db.list_activities(int(draft["business_id"]))
    assert any("needs approving again" in a["detail"] for a in timeline)


def test_a_sent_message_cannot_be_rewritten(client, draft):
    """It is a record of what actually went out."""
    db.update_message(draft["id"], {"status": "sent", "sent_at": db.now()})
    response = client.post(f"/api/messages/{draft['id']}",
                           json={"subject": "Revised", "body": "Revised"})
    assert response.status_code == 400
    assert "already been sent" in response.json()["detail"]
    assert db.get_message(draft["id"])["subject"] != "Revised"


@pytest.mark.parametrize("payload,expected", [
    ({"subject": "   ", "body": "fine"}, "subject cannot be empty"),
    ({"subject": "fine", "body": "  \n "}, "body cannot be empty"),
], ids=["blank-subject", "blank-body"])
def test_an_empty_draft_is_refused(client, draft, payload, expected):
    response = client.post(f"/api/messages/{draft['id']}", json=payload)
    assert response.status_code == 400
    assert expected in response.json()["detail"]


def test_whitespace_around_an_edit_is_trimmed(client, draft):
    client.post(f"/api/messages/{draft['id']}",
                json={"subject": "  Trimmed  ", "body": "  Body text  "})
    saved = db.get_message(draft["id"])
    assert saved["subject"] == "Trimmed"
    assert saved["body"] == "Body text"


def test_editing_an_unknown_message_is_a_400(client):
    response = client.post("/api/messages/999999", json={"subject": "x", "body": "y"})
    assert response.status_code == 400


# The class name also appears in the page's own script, so these assert on the
# rendered button element rather than on the name appearing anywhere.
EDIT_BUTTON = '<button class="btn btn-sm js-edit-btn">Edit</button>'


def test_the_page_offers_an_edit_control_for_a_draft(client, draft):
    body = client.get(f"/businesses/{draft['business_id']}").text
    assert EDIT_BUTTON in body
    assert 'class="js-body-input"' in body
    assert "Save changes" in body


def test_a_sent_message_gets_no_edit_control(client, draft):
    db.update_message(draft["id"], {"status": "sent", "sent_at": db.now()})
    body = client.get(f"/businesses/{draft['business_id']}").text
    assert EDIT_BUTTON not in body
