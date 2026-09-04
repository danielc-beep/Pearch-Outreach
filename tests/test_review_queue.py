"""
The review queue, bulk drafting, and territory sweeps.

The three of them are one workflow: a sweep fills the database, a bulk draft
fills the queue, and the queue empties into the outbox. These cover the rules
that decide what appears where.
"""
from __future__ import annotations

from unittest.mock import MagicMock, patch

import anthropic

import db
import mastheads
import outreach
import prospect
import review


def _seed(client, n=6, location="Newcastle NSW"):
    prospect.run("sample", {"industry": "dentist", "location": location, "limit": n},
                 enrich=False)
    return db.list_businesses(limit=99)[0]


# ---------- What is waiting for a decision ----------

def test_the_queue_holds_contactable_undecided_businesses(client):
    rows = _seed(client, 5)
    assert len(review.queue_ids()) == len(rows)


def test_a_business_with_no_email_is_not_in_the_queue(client):
    rows = _seed(client, 3)
    db.update_business(rows[0]["id"], {"email": ""})
    assert rows[0]["id"] not in review.queue_ids()


def test_do_not_contact_is_not_in_the_queue(client):
    rows = _seed(client, 3)
    db.update_business(rows[0]["id"], {"do_not_contact": 1})
    assert rows[0]["id"] not in review.queue_ids()


def test_a_settled_business_is_not_in_the_queue(client):
    rows = _seed(client, 4)
    for status in ("contacted", "won", "lost", "disqualified"):
        db.update_business(rows[0]["id"], {"status": status})
        assert rows[0]["id"] not in review.queue_ids(), status


def test_an_approved_draft_takes_a_business_out_of_the_queue(client):
    rows = _seed(client, 3)
    business_id = rows[0]["id"]
    message = outreach.draft_message(business_id, use_ai=False)
    assert business_id in review.queue_ids()      # a draft alone is not enough
    outreach.approve_message(int(message["id"]))
    assert business_id not in review.queue_ids()


def test_the_queue_is_best_fit_first(client):
    _seed(client, 6)
    scores = [db.get_business(i)["fit_score"] for i in review.queue_ids()]
    assert scores == sorted(scores, reverse=True)


def test_the_queue_honours_the_filters(client):
    _seed(client, 4, "Newcastle NSW")
    _seed(client, 4, "Bendigo VIC")
    newcastle = review.queue_ids(masthead="newcastleherald.com.au")
    assert newcastle and len(newcastle) < len(review.queue_ids())


# ---------- The card ----------

def test_a_card_carries_what_the_decision_needs(client):
    rows = _seed(client, 2)
    card = review.card(rows[0]["id"])
    assert card["business"]["name"] == rows[0]["name"]
    assert card["masthead"]
    assert card["reasons"]
    assert card["draft"] is None
    outreach.draft_message(rows[0]["id"], use_ai=False)
    assert review.card(rows[0]["id"])["draft"]["status"] == "draft"


def test_an_unknown_business_has_no_card(client):
    assert review.card(999999) is None


# ---------- Decisions ----------

def test_approve_qualifies_the_business_and_its_draft(client):
    rows = _seed(client, 2)
    business_id = rows[0]["id"]
    outreach.draft_message(business_id, use_ai=False)
    result = review.decide(business_id, "approve")
    assert result["status"] == "qualified"
    assert result["message"]["status"] == "approved"
    assert business_id not in review.queue_ids()


def test_approve_without_a_draft_still_qualifies(client):
    """Nothing to approve yet is not a reason to refuse the decision."""
    rows = _seed(client, 2)
    result = review.decide(rows[0]["id"], "approve")
    assert result["status"] == "qualified"
    assert result["message"] is None


def test_reject_disqualifies_and_does_not_come_back(client):
    rows = _seed(client, 2)
    review.decide(rows[0]["id"], "reject")
    assert db.get_business(rows[0]["id"])["status"] == "disqualified"
    assert rows[0]["id"] not in review.queue_ids()


def test_skip_changes_nothing_and_comes_back(client):
    rows = _seed(client, 2)
    before = db.get_business(rows[0]["id"])["status"]
    review.decide(rows[0]["id"], "skip")
    assert db.get_business(rows[0]["id"])["status"] == before
    assert rows[0]["id"] in review.queue_ids()


def test_an_unknown_decision_is_refused(client):
    rows = _seed(client, 2)
    for bad in ("maybe", "", "APPROVE"):
        try:
            review.decide(rows[0]["id"], bad)
            assert False, bad
        except ValueError:
            pass


def test_the_decision_route(client):
    rows = _seed(client, 2)
    ok = client.post(f"/api/review/decide/{rows[0]['id']}", json={"decision": "approve"})
    assert ok.status_code == 200
    bad = client.post(f"/api/review/decide/{rows[1]['id']}", json={"decision": "nope"})
    assert bad.status_code == 400


def test_the_queue_page_and_its_api(client):
    _seed(client, 3)
    assert client.get("/review").status_code == 200
    body = client.get("/api/review/queue").json()
    assert body["total"] == 3 and len(body["ids"]) == 3
    assert client.get(f"/api/review/card/{body['ids'][0]}").status_code == 200
    assert client.get("/api/review/card/999999").status_code == 404


# ---------- Bulk drafting ----------

def test_a_batch_drafts_what_has_nothing(client):
    _seed(client, 5)
    result = outreach.draft_batch(limit=3, use_ai=False)
    assert result["drafted"] == 3
    assert result["remaining"] == 2


def test_a_batch_never_writes_a_second_draft_for_the_same_business(client):
    _seed(client, 3)
    outreach.draft_batch(limit=99, use_ai=False)
    again = outreach.draft_batch(limit=99, use_ai=False)
    assert again["drafted"] == 0
    for row in db.list_businesses(limit=99)[0]:
        assert len(db.list_messages(business_id=row["id"])) == 1


def test_a_batch_reports_who_it_could_not_draft_for_and_carries_on(client):
    rows = _seed(client, 4)
    db.suppress(rows[0]["email"], "asked to stop")
    result = outreach.draft_batch(limit=99, use_ai=False)
    assert result["drafted"] == 3
    assert result["skipped"] == 1
    assert any("suppression" in p for p in result["problems"])


def test_a_batch_honours_the_filters(client):
    _seed(client, 3, "Newcastle NSW")
    _seed(client, 3, "Bendigo VIC")
    result = outreach.draft_batch(limit=99, use_ai=False,
                                  masthead="newcastleherald.com.au")
    assert result["drafted"] == 3
    for message in db.list_messages(limit=99):
        business = db.get_business(message["business_id"])
        assert business["masthead"] == "newcastleherald.com.au"


def test_the_batch_route_caps_its_own_size(client):
    # The sample source dedupes, so count what actually landed rather than
    # assuming the number asked for.
    _seed(client, 30)
    waiting = len(review.queue_ids())
    assert waiting > 20, "need more than the cap for this to prove anything"
    result = client.post("/api/drafts/batch", json={"limit": 500, "use_ai": False}).json()
    assert result["drafted"] == 20                     # capped, not `waiting`
    assert result["remaining"] == waiting - 20


def test_composing_never_writes(client):
    rows = _seed(client, 2)
    outreach.compose(rows[0]["id"], use_ai=False)
    assert db.list_messages(business_id=rows[0]["id"]) == []


# ---------- Territory sweeps ----------

def test_a_masthead_knows_its_home_town():
    assert mastheads.home_location("newcastleherald.com.au") == "Newcastle NSW"
    assert mastheads.home_location("illawarramercury.com.au") == "Wollongong NSW"
    assert mastheads.home_location("examiner.com.au") == "Launceston TAS"


def test_a_national_title_has_no_patch_to_sweep():
    assert mastheads.home_location("theland.com.au") == ""
    assert "theland.com.au" not in {t["site"] for t in mastheads.with_a_patch()}


def test_a_step_searches_the_patch_and_stamps_the_masthead(client):
    result = prospect.territory_step("newcastleherald.com.au", "dentist",
                                     source_key="sample", limit=5, enrich=False)
    assert result["industry"] == "dentist"
    assert result["location"] == "Newcastle NSW"
    assert result["new"] == 5
    for row in db.list_businesses(limit=99)[0]:
        assert row["masthead"] == "newcastleherald.com.au"


def test_a_masthead_with_no_patch_is_refused(client):
    try:
        prospect.territory_step("theland.com.au", "dentist", source_key="sample")
        assert False, "should refuse"
    except ValueError as e:
        assert "no local patch" in str(e)


def test_the_territory_route(client):
    ok = client.post("/api/territory/step", json={
        "masthead": "newcastleherald.com.au", "industry": "dentist",
        "source": "sample", "limit": 4, "enrich": False})
    assert ok.status_code == 200 and ok.json()["new"] == 4
    bad = client.post("/api/territory/step", json={
        "masthead": "theland.com.au", "industry": "dentist", "source": "sample"})
    assert bad.status_code == 400
