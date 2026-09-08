"""
Replies coming back, and the three distinctions that keep Replied honest.

Nothing here touches the network: receive() takes a dict, which is all a
webhook ever hands it.
"""
from __future__ import annotations

import db
import replies


def _business(email="bob@acme.com.au", domain="acme.com.au", status="contacted", **extra):
    return db.insert_business({"name": "Acme Plumbing", "email": email, "domain": domain,
                               "status": status, "suburb": "Newcastle",
                               "masthead": "newcastleherald.com.au", **extra})


def _in(from_email="Bob <bob@acme.com.au>", subject="Re: your email", text="Yes, let's talk."):
    return replies.receive({"from": from_email, "subject": subject, "text": text})


# ---------- What kind of reply it is ----------

def test_a_person_answering_is_a_reply(client):
    assert replies.classify("Re: your email", "Sounds good, Thursday works.") == "human"


def test_an_out_of_office_is_not_a_reply(client):
    """
    The classic false positive, and the one that would corrupt the stage.

    "Undeliverable" used to be on this list, which had the old model wrong in
    both directions: a dead mailbox was filed as somebody who would be back on
    Monday, and nothing stopped the sequence emailing it twice more. It is a
    bounce now — see the bounce tests below.
    """
    for subject in ("Out of Office: Re: your email", "Automatic reply: hello",
                    "AutoReply: away", "On leave until Monday"):
        assert replies.classify(subject, "") == "auto", subject


def test_an_out_of_office_body_is_caught_too(client):
    assert replies.classify(
        "Re: your email", "I am currently out of the office until 14 March.") == "auto"


def test_asking_to_be_left_alone_is_an_opt_out(client):
    for text in ("Please remove me from your list.", "unsubscribe",
                 "Do not contact me again.", "Take me off this mailing list"):
        assert replies.classify("Re: hi", text) == "optout", text


def test_an_opt_out_inside_an_auto_reply_is_still_an_opt_out(client):
    assert replies.classify(
        "Out of Office", "I'm away until Monday. Also please remove me from your list."
    ) == "optout"


# ---------- Who it came from ----------

def test_the_address_matches_first(client):
    business_id = _business()
    got, how = replies.match("bob@acme.com.au")
    assert got["id"] == business_id and how == "address"


def test_a_contact_at_the_business_matches_too(client):
    business_id = _business(email="")
    db.add_contact(business_id, {"name": "Jo", "email": "jo@acme.com.au"})
    got, how = replies.match("jo@acme.com.au")
    assert got["id"] == business_id and how == "address"


def test_a_colleague_at_the_same_domain_matches_on_the_domain(client):
    business_id = _business(last_contacted_at=db.now())
    got, how = replies.match("sandra@acme.com.au")
    assert got["id"] == business_id and how == "domain"


def test_a_free_mail_domain_never_matches_on_the_domain(client):
    """Nobody's company domain is gmail.com."""
    _business(email="someone@gmail.com", domain="", last_contacted_at=db.now())
    got, _ = replies.match("stranger@gmail.com")
    assert got is None


def test_an_unknown_sender_matches_nothing(client):
    _business()
    assert replies.match("nobody@elsewhere.com.au") == (None, "")


# ---------- What it does to the pipeline ----------

def test_a_reply_moves_the_business(client):
    business_id = _business()
    result = _in()
    assert result["acted"] == "replied"
    assert db.get_business(business_id)["status"] == "replied"


def test_an_auto_reply_moves_nothing(client):
    business_id = _business()
    result = _in(subject="Out of Office: Re: your email", text="Back on the 14th.")
    assert result["kind"] == "auto" and result["acted"] == "logged"
    assert db.get_business(business_id)["status"] == "contacted", "still just contacted"


def test_an_opt_out_suppresses_and_rules_them_out(client):
    business_id = _business()
    result = _in(text="Please take me off your list.")
    assert result["acted"] == "suppressed"
    business = db.get_business(business_id)
    assert business["status"] == "disqualified"
    assert business["do_not_contact"] == 1
    assert db.is_suppressed("bob@acme.com.au")


def test_an_opt_out_never_counts_as_a_reply(client):
    _business()
    _in(text="unsubscribe")
    assert db.list_businesses(status="replied", limit=1)[1] == 0


def test_a_won_deal_is_not_dragged_backwards_by_a_reply(client):
    business_id = _business(status="won")
    _in()
    assert db.get_business(business_id)["status"] == "won"


# ---------- Nothing is dropped ----------

def test_an_unmatched_reply_is_kept_for_a_person(client):
    result = _in(from_email="stranger@nowhere.com.au")
    assert result["stored"] and result["business_id"] is None
    assert db.count_unmatched_inbound() == 1


def test_a_reply_with_no_sender_is_reported_not_stored(client):
    result = replies.receive({"subject": "Re: hi", "text": "hello"})
    assert result["stored"] is False


def test_the_dashboard_counts_replies_nobody_could_place(client):
    _in(from_email="stranger@nowhere.com.au")
    body = client.get("/").text
    assert "replies nobody could place" in body


def test_attaching_by_hand_does_what_arrival_would_have(client):
    business_id = _business()
    result = _in(from_email="stranger@nowhere.com.au")
    replies.attach(result["id"], business_id)
    assert db.get_business(business_id)["status"] == "replied"
    assert db.count_unmatched_inbound() == 0


def test_attaching_an_opt_out_still_suppresses(client):
    """Whoever attaches it has read it; it should not need deciding twice."""
    business_id = _business()
    result = _in(from_email="stranger@nowhere.com.au", text="Please unsubscribe me.")
    replies.attach(result["id"], business_id)
    assert db.get_business(business_id)["do_not_contact"] == 1


def test_dismissing_leaves_the_pipeline_alone(client):
    business_id = _business()
    result = _in(from_email="spam@elsewhere.com")
    replies.dismiss(result["id"])
    assert db.count_unmatched_inbound() == 0
    assert db.get_business(business_id)["status"] == "contacted"


# ---------- Pasting one in ----------

def test_pasting_a_reply_works_with_no_webhook_at_all(client):
    business_id = _business()
    replies.log_by_hand(business_id, "Happy to chat next week.")
    assert db.get_business(business_id)["status"] == "replied"


def test_a_pasted_opt_out_is_read_the_same_way(client):
    business_id = _business()
    replies.log_by_hand(business_id, "Take me off your list please.")
    assert db.get_business(business_id)["status"] == "disqualified"


def test_the_paste_endpoint_answers(client):
    business_id = _business()
    got = client.post(f"/api/businesses/{business_id}/reply",
                      json={"text": "Yes please."}).json()
    assert got["status"] == "replied"


# ---------- The webhook ----------

def test_the_webhook_is_shut_without_a_secret(client):
    import app
    from unittest.mock import patch
    with patch.object(app, "INBOUND_SECRET", ""):
        assert client.post("/api/inbound/mail", json={"from": "a@b.com"}).status_code == 503


def test_the_webhook_refuses_a_wrong_secret(client):
    import app
    from unittest.mock import patch
    with patch.object(app, "INBOUND_SECRET", "right"):
        r = client.post("/api/inbound/mail", json={"from": "a@b.com"},
                        headers={"X-Pearch-Secret": "wrong"})
    assert r.status_code == 401
    assert db.count_unmatched_inbound() == 0, "nothing stored on a refused call"


def test_the_webhook_takes_the_secret_and_the_mail(client):
    import app
    from unittest.mock import patch
    business_id = _business()
    with patch.object(app, "INBOUND_SECRET", "right"):
        got = client.post("/api/inbound/mail",
                          json={"from": "bob@acme.com.au", "subject": "Re: hi",
                                "text": "Yes please"},
                          headers={"X-Pearch-Secret": "right"}).json()
    assert got["acted"] == "replied"
    assert db.get_business(business_id)["status"] == "replied"


def test_it_reads_a_provider_that_wraps_the_mail_in_data(client):
    business_id = _business()
    result = replies.receive({"type": "email.received",
                              "data": {"from": "bob@acme.com.au", "subject": "Re: hi",
                                       "text": "Sounds good"}})
    assert result["acted"] == "replied"
    assert db.get_business(business_id)["status"] == "replied"


def test_it_reads_a_provider_that_nests_the_sender(client):
    business_id = _business()
    result = replies.receive({"from": {"address": "bob@acme.com.au", "name": "Bob"},
                              "subject": "Re: hi", "text": "Yes"})
    assert result["business_id"] == business_id


# ---------- Bounces ----------
# A bounce arrives from a mail system, so nothing about it looks like the
# prospect it is about. Left alone it collected the pitch, a nudge and a last
# word — three sends to a mailbox that does not exist.

HARD = ("Your message to bob@acme.com.au could not be delivered.\n"
        "550 5.1.1 The email account that you tried to reach does not exist.")


def _bounce(text=HARD, subject="Delivery Status Notification (Failure)",
            sender="MAILER-DAEMON@googlemail.com"):
    return replies.receive({"from": sender, "subject": subject, "text": text})


def test_a_bounce_is_told_apart_from_an_out_of_office(client):
    """They need opposite treatment: one is back Monday, the other never."""
    assert replies.classify("Delivery Status Notification (Failure)",
                            "550 5.1.1 does not exist", "MAILER-DAEMON@x.com") == "bounce"
    assert replies.classify("Out of Office", "Back on the 14th.") == "auto"


def test_a_full_mailbox_is_not_a_dead_one(client):
    assert replies.classify("Undeliverable", "mailbox full", "postmaster@x.com") == "soft_bounce"
    assert replies.classify("Undeliverable", "over quota", "postmaster@x.com") == "soft_bounce"


def test_an_unreadable_failure_notice_counts_as_dead(client):
    """Wrong in the safe direction: one prospect lost beats three sends to nothing."""
    assert replies.classify("Message not delivered", "something went wrong") == "bounce"


def test_a_bounce_finds_the_business_it_is_about_not_the_one_it_is_from(client):
    business_id = _business()
    result = _bounce()
    assert result["business_id"] == business_id
    assert result["matched_on"] == "bounced address"


def test_it_reads_the_failed_address_from_a_provider_field(client):
    business_id = _business()
    result = replies.receive({"from": "bounces@resend.dev", "subject": "Undeliverable",
                              "original_recipient": "bob@acme.com.au",
                              "text": "Delivery failed. 550 no such user"})
    assert result["business_id"] == business_id


def test_a_postmaster_address_in_the_body_is_not_mistaken_for_the_prospect(client):
    _business()
    result = replies.receive({
        "from": "MAILER-DAEMON@x.com", "subject": "Undeliverable",
        "text": "postmaster@x.com reports: bob@acme.com.au 550 5.1.1 user unknown"})
    assert db.get_inbound(result["id"])["from_email"] == "bob@acme.com.au"


def test_a_hard_bounce_takes_the_address_out_of_sending(client):
    _business()
    assert _bounce()["acted"] == "bounced"
    assert db.is_suppressed("bob@acme.com.au")


def test_a_hard_bounce_stops_the_follow_up_sequence(client):
    """The whole point. It used to collect two more sends."""
    import followup
    business_id = _business(masthead="newcastleherald.com.au", rating=4.7)
    message_id = db.insert_message({"business_id": business_id, "to_email": "bob@acme.com.au",
                                    "subject": "S", "body": "B", "status": "sent", "step": 1})
    with db.tx() as conn:
        conn.execute("UPDATE messages SET sent_at = datetime('now', '-9 days') WHERE id = ?",
                     (message_id,))
    assert len(followup.due()) == 1
    _bounce()
    assert followup.due() == []


def test_a_bounced_business_is_not_treated_as_a_refusal(client):
    """A dead mailbox is not somebody saying no, and somebody should go looking."""
    business_id = _business()
    _bounce()
    business = db.get_business(business_id)
    assert business["do_not_contact"] == 0
    assert business["status"] == "contacted", "not ruled out"
    assert [b["name"] for b in db.bounced()] == ["Acme Plumbing"]


def test_a_soft_bounce_does_not_throw_the_prospect_away(client):
    business_id = _business()
    result = _bounce(text="bob@acme.com.au mailbox full, try again later",
                     subject="Undeliverable: your email")
    assert result["kind"] == "soft_bounce" and result["acted"] == "logged"
    assert not db.is_suppressed("bob@acme.com.au")
    assert db.get_business(business_id)["status"] == "contacted"


def test_an_opt_out_still_rules_them_out(client):
    """The two must not have collapsed into each other."""
    business_id = _business()
    replies.receive({"from": "bob@acme.com.au", "subject": "Re: hi",
                     "text": "Please remove me from your list."})
    assert db.get_business(business_id)["do_not_contact"] == 1
    assert db.get_business(business_id)["status"] == "disqualified"


def test_the_dashboard_asks_somebody_to_find_a_new_address(client):
    _business(website="https://acme.com.au")
    _bounce()
    body = client.get("/").text
    assert "addresses that bounced" in body
    assert 'href="/addresses?bounced=1"' in body


def test_the_bounced_screen_lists_them(client):
    _business(website="https://acme.com.au")
    _bounce()
    body = client.get("/addresses?bounced=1").text
    assert "Acme Plumbing" in body
    assert "came back" in body
