"""
The second and third emails, and every reason not to send one.

Most of these are about when a follow-up must NOT happen. A sequence that
chases someone who already answered is worse than no sequence at all, so the
stopping conditions get the coverage.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import anthropic
import pytest

import db
import followup
import outreach
import replies


def _sent(days_ago=10, status="contacted", email="bob@acme.com.au", **extra):
    """A business emailed once, `days_ago` days back."""
    business_id = db.insert_business({
        "name": "Acme Plumbing", "email": email, "domain": "acme.com.au",
        "status": status, "suburb": "Newcastle", "rating": 4.7, "review_count": 60,
        "masthead": "newcastleherald.com.au", **extra})
    message_id = db.insert_message({
        "business_id": business_id, "to_email": email, "subject": "Acme in Google's AI",
        "body": "The first email.", "status": "sent", "step": 1})
    with db.tx() as conn:
        conn.execute("UPDATE messages SET sent_at = datetime('now', ?) WHERE id = ?",
                     (f"-{days_ago} days", message_id))
    return business_id, message_id


def _steps():
    return {s["step"]: s["after_days"] for s in followup.schedule()}


# ---------- Who is owed one ----------

def test_someone_emailed_and_ignored_is_owed_a_nudge(client):
    business_id, _ = _sent(days_ago=6)
    owed = followup.due()
    assert [b["id"] for b in owed] == [business_id]
    assert owed[0]["step"] == 2


def test_nobody_is_owed_one_inside_the_waiting_period(client):
    _sent(days_ago=1)
    assert followup.due() == []


def test_the_third_touch_comes_after_the_second(client):
    business_id, _ = _sent(days_ago=20)
    db.insert_message({"business_id": business_id, "to_email": "bob@acme.com.au",
                       "subject": "Re: hi", "body": "The nudge.", "status": "sent", "step": 2})
    with db.tx() as conn:
        conn.execute("UPDATE messages SET sent_at = datetime('now', '-14 days') WHERE step = 2")
    owed = followup.due()
    assert owed and owed[0]["step"] == 3


def test_it_stops_after_the_last_step(client):
    business_id, _ = _sent(days_ago=60)
    for step in (2, 3):
        db.insert_message({"business_id": business_id, "to_email": "bob@acme.com.au",
                           "subject": "x", "body": "y", "status": "sent", "step": step})
    with db.tx() as conn:
        conn.execute("UPDATE messages SET sent_at = datetime('now', '-30 days')")
    assert followup.due() == [], "three emails is enough"


# ---------- Every reason to stop ----------

def test_a_reply_stops_the_sequence_even_if_nobody_moved_the_card(client):
    """The stage can be stale; the inbox cannot."""
    business_id, _ = _sent(days_ago=10)
    replies.receive({"from": "bob@acme.com.au", "subject": "Re: hi", "text": "Yes please"})
    db.update_business(business_id, {"status": "contacted"})   # as if someone dragged it back
    assert followup.due() == []


def test_an_out_of_office_does_not_stop_the_sequence(client):
    """It is not a person answering, so they are still owed a real second email."""
    _sent(days_ago=10)
    replies.receive({"from": "bob@acme.com.au", "subject": "Out of Office",
                     "text": "Back on the 14th."})
    assert len(followup.due()) == 1


def test_an_opt_out_stops_it(client):
    _sent(days_ago=10)
    replies.receive({"from": "bob@acme.com.au", "subject": "Re: hi",
                     "text": "Please remove me from your list."})
    assert followup.due() == []


def test_do_not_contact_stops_it(client):
    business_id, _ = _sent(days_ago=10)
    db.update_business(business_id, {"do_not_contact": 1})
    assert followup.due() == []


def test_a_suppressed_address_stops_it(client):
    _sent(days_ago=10)
    db.suppress("bob@acme.com.au", "asked")
    assert followup.due() == []


def test_a_won_or_lost_business_is_left_alone(client):
    for status in ("won", "lost", "disqualified", "replied"):
        db.reset_db()
        _sent(days_ago=10, status=status)
        assert followup.due() == [], status


def test_one_already_drafted_is_not_drafted_again(client):
    business_id, _ = _sent(days_ago=10)
    db.insert_message({"business_id": business_id, "to_email": "bob@acme.com.au",
                       "subject": "Re: hi", "body": "waiting", "status": "draft", "step": 2})
    assert followup.due() == []


def test_a_business_never_emailed_is_not_owed_a_follow_up(client):
    db.insert_business({"name": "Never", "email": "a@b.com", "status": "contacted"})
    assert followup.due() == []


# ---------- What gets written ----------

def test_the_draft_replies_to_the_first_email(client):
    business_id, _ = _sent(days_ago=10)
    payload = followup.compose(followup.due()[0], 2, use_ai=False)
    assert payload["subject"].lower().startswith("re:")
    assert payload["status"] == "draft"
    assert payload["step"] == 2


def test_the_fallback_names_the_part_of_google(client):
    _sent(days_ago=10)
    payload = followup.compose(followup.due()[0], 2, use_ai=False)
    assert outreach.names_the_surface(payload["body"]), payload["body"]


def test_the_last_one_says_it_is_the_last_one(client):
    business_id, _ = _sent(days_ago=30)
    db.insert_message({"business_id": business_id, "to_email": "bob@acme.com.au",
                       "subject": "x", "body": "y", "status": "sent", "step": 2})
    with db.tx() as conn:
        conn.execute("UPDATE messages SET sent_at = datetime('now', '-20 days')")
    payload = followup.compose(followup.due()[0], 3, use_ai=False)
    assert "last" in payload["body"].lower()


def test_drafting_writes_to_the_outbox_and_sends_nothing(client):
    business_id, _ = _sent(days_ago=10)
    result = followup.draft_due(use_ai=False)
    assert len(result["drafted"]) == 1
    drafts = db.list_messages(business_id=business_id, status="draft")
    assert len(drafts) == 1 and drafts[0]["step"] == 2
    assert not db.list_messages(business_id=business_id, status="sent") or \
        len(db.list_messages(business_id=business_id, status="sent")) == 1, "only the original"


def test_a_business_that_cannot_be_written_to_is_skipped_not_crashed(client):
    _sent(days_ago=10)
    db.suppress("bob@acme.com.au", "asked")
    # Suppression already takes them out of due(); this is the belt to that
    # brace — compose refuses even if one slips through.
    business = {"id": 1, "name": "Acme", "email": "bob@acme.com.au", "last_to": "bob@acme.com.au"}
    with pytest.raises(ValueError):
        followup.compose(business, 2, use_ai=False)


# ---------- Claude writes it when it can ----------

@patch.object(followup, "ANTHROPIC_API_KEY", "sk-ant-test")
def test_claude_is_shown_the_email_that_went_unanswered(client):
    _sent(days_ago=10)
    with patch.object(anthropic, "Anthropic") as client_cls:
        client_cls.return_value.messages.parse.return_value = MagicMock(
            stop_reason="end_turn",
            parsed_output=followup.Followup(subject="Re: Acme", body="A short nudge."))
        followup.compose(followup.due()[0], 2)
        prompt = client_cls.return_value.messages.parse.call_args.kwargs["messages"][0]["content"]
    assert "The first email." in prompt, "it must know what was already said"
    assert "congratulate them on their Google" in prompt


@patch.object(followup, "ANTHROPIC_API_KEY", "sk-ant-test")
def test_an_outage_falls_back_to_the_plain_nudge(client):
    _sent(days_ago=10)
    with patch.object(anthropic, "Anthropic") as client_cls:
        client_cls.return_value.messages.parse.side_effect = \
            anthropic.APIConnectionError(request=MagicMock())
        payload = followup.compose(followup.due()[0], 2)
    assert payload["body"], "a plain second email beats no second email"
    assert payload["subject"].lower().startswith("re:")


# ---------- Fitting in with what was already there ----------

def test_a_follow_up_does_not_warn_about_emailing_the_same_firm_twice(client):
    """The duplicate guard is right for a fresh pitch and wrong for a follow-up."""
    business_id, _ = _sent(days_ago=10)
    followup.draft_due(use_ai=False)
    draft = db.list_messages(business_id=business_id, status="draft")[0]
    notes = outreach.warnings(draft)
    assert not any("second pitch" in n for n in notes), notes


def test_a_fresh_pitch_still_warns(client):
    """The guard has to still work for what it was built for."""
    _sent(days_ago=2)
    # Two records at one firm: the second has no website of its own, which is
    # how a sweep actually turns up a partner at the same practice.
    other = db.insert_business({"name": "Acme Legal", "email": "jo@acme.com.au",
                                "status": "qualified",
                                "rating": 4.8, "masthead": "newcastleherald.com.au"})
    message_id = db.insert_message({"business_id": other, "to_email": "jo@acme.com.au",
                                    "subject": "Hello", "body": "A pitch.", "status": "draft"})
    notes = outreach.warnings(db.get_message(message_id))
    assert any("second pitch" in n for n in notes), notes


def test_the_dashboard_counts_who_is_owed_one(client):
    _sent(days_ago=10)
    body = client.get("/").text
    assert "owed another email" in body


def test_the_page_lists_them(client):
    _sent(days_ago=10)
    body = client.get("/followups").text
    assert "Acme Plumbing" in body
    assert "undefined" not in body


# ---------- The schedule ----------

def test_the_schedule_can_be_changed(client):
    followup.set_schedule({2: 3, 3: 9})
    assert _steps() == {2: 3, 3: 9}


def test_a_nonsense_schedule_is_refused(client):
    for bad in ({2: 0}, {2: 400}, {9: 5}):
        with pytest.raises(ValueError):
            followup.set_schedule(bad)


def test_a_shorter_schedule_pulls_people_forward(client):
    _sent(days_ago=3)
    assert followup.due() == []
    followup.set_schedule({2: 2, 3: 9})
    assert len(followup.due()) == 1
