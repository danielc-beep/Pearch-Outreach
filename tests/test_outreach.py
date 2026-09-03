import db
import outreach
import pytest


def _first_with_email():
    rows, _ = db.list_businesses(has_email=True)
    return rows[0]


def test_draft_merges_the_template(sample_run):
    business = _first_with_email()
    message = outreach.draft_message(business["id"], use_ai=False)
    assert business["name"] in message["subject"]
    assert message["status"] == "draft"
    assert message["to_email"] == business["email"]
    assert "{business_name}" not in message["body"]


def test_draft_refuses_without_an_address(sample_run):
    rows, _ = db.list_businesses(has_email=False)
    with pytest.raises(ValueError, match="no email"):
        outreach.draft_message(rows[0]["id"], use_ai=False)


def test_draft_refuses_do_not_contact(sample_run):
    business = _first_with_email()
    db.update_business(business["id"], {"do_not_contact": 1})
    with pytest.raises(ValueError, match="do-not-contact"):
        outreach.draft_message(business["id"], use_ai=False)


def test_draft_refuses_a_suppressed_address(sample_run):
    business = _first_with_email()
    db.suppress(business["email"], "test")
    with pytest.raises(ValueError, match="suppression"):
        outreach.draft_message(business["id"], use_ai=False)


def test_sending_is_blocked_until_approved_and_enabled(sample_run):
    business = _first_with_email()
    message = outreach.draft_message(business["id"], use_ai=False)
    problems = outreach.preflight(message)
    assert any("approved" in p for p in problems)

    outreach.approve_message(message["id"])
    result = outreach.send_message(message["id"], "http://localhost")
    assert result["sent"] is False
    assert any("disabled" in p or "RESEND" in p for p in result["problems"])


def test_unsubscribe_suppresses_and_flags(sample_run):
    business = _first_with_email()
    flagged = outreach.unsubscribe(business["email"])
    assert flagged == 1
    assert db.is_suppressed(business["email"])
    assert db.get_business(business["id"])["do_not_contact"] == 1


def test_footer_carries_identity_and_unsubscribe():
    footer = outreach._footer("a@b.com", "https://outreach.pearch.com.au")
    assert "Unsubscribe" in footer
    assert "unsubscribe?email=a@b.com" in footer
