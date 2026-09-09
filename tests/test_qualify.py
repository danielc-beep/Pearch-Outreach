"""
The bar a business clears without anybody looking at it.

Four gates, checked together. The queue is for judgement calls, and these are
the tests that a business which is not one never reaches it — and, just as
importantly, that a business somebody has already decided about is never
qualified back out of that decision by a rule.
"""
from __future__ import annotations

import db
import mastheads
import qualify
import review
from config import AUTO_QUALIFY_MIN_RATING, AUTO_QUALIFY_MIN_SCORE

SITE = next(iter(mastheads.BY_SITE))


def _business(**over):
    """A business that clears every gate, before whatever the test breaks."""
    record = {"name": "Clears It", "domain": "clears.com.au",
              "website": "https://clears.com.au", "website_status": "live",
              "email": "hello@clears.com.au", "rating": 4.8, "review_count": 60,
              "fit_score": 82, "status": "new", "masthead": SITE,
              "suburb": "Bathurst", "state": "NSW"}
    record.update(over)
    record.setdefault("domain", f"{abs(hash(str(over))) % 10**8}.com.au")
    return db.insert_business(record)


def test_a_business_that_clears_every_gate_qualifies_itself():
    bid = _business()
    assert qualify.qualify_one(bid) is True
    assert db.get_business(bid)["status"] == "qualified"


def test_clearing_three_gates_is_not_clearing_the_bar():
    """Each gate on its own is enough to keep a business in front of a person."""
    short = {
        "rating": {"rating": AUTO_QUALIFY_MIN_RATING},        # at the bar, not above it
        "no rating at all": {"rating": None},
        "website": {"website_status": "blocked"},
        "unchecked website": {"website_status": ""},
        "email": {"email": ""},
        "score": {"fit_score": AUTO_QUALIFY_MIN_SCORE},       # at the bar, not above it
    }
    for label, broken in short.items():
        bid = _business(domain=f"{label.replace(' ', '')}.com.au", **broken)
        assert qualify.qualify_one(bid) is False, label
        assert db.get_business(bid)["status"] == "new", label
        assert qualify.unmet(db.get_business(bid)), label


def test_a_decision_somebody_made_is_never_undone_by_the_rule():
    """
    The one that matters. A business rejected in review clears every gate on
    paper — that is usually why somebody had to look at it — and a rule that
    qualified it back would quietly undo the decision and email them.
    """
    for settled in ("disqualified", "contacted", "replied", "won", "lost", "qualified"):
        bid = _business(domain=f"{settled}.com.au", status=settled)
        assert qualify.qualify_one(bid) is False, settled
        assert db.get_business(bid)["status"] == settled, settled


def test_do_not_contact_is_never_qualified():
    bid = _business(do_not_contact=1)
    assert qualify.qualify_one(bid) is False
    assert db.get_business(bid)["status"] == "new"


def test_the_sweep_and_the_single_record_check_agree():
    """
    The gates are written twice — once in Python for one record, once in SQL
    so the backlog moves in a single statement. Two copies of a rule is a rule
    that drifts, so every case runs through both.
    """
    cases = [
        {},                                      # clears it
        {"rating": 3.9},
        {"rating": None},
        {"website_status": "unreachable"},
        {"email": ""},
        {"fit_score": 12},
        {"do_not_contact": 1},
        {"status": "disqualified"},
    ]
    ids = {}
    for i, case in enumerate(cases):
        ids[i] = _business(domain=f"agree{i}.com.au", **case)

    by_python = {i for i, bid in ids.items()
                 if qualify.clears_the_bar(db.get_business(bid))}
    moved = {b["id"] for b in qualify.sweep()["businesses"]}
    by_sql = {i for i, bid in ids.items() if bid in moved}
    assert by_python == by_sql == {0}


def test_the_sweep_moves_the_backlog_in_one_go():
    for i in range(12):
        _business(domain=f"backlog{i}.com.au")
    assert qualify.waiting() == 12
    assert qualify.sweep()["qualified"] == 12
    assert qualify.waiting() == 0
    # And running it again finds nothing to do rather than churning the rows.
    assert qualify.sweep()["qualified"] == 0


def test_a_qualified_business_leaves_the_review_queue():
    bid = _business()
    assert bid in review.queue_ids()
    qualify.qualify_one(bid)
    assert bid not in review.queue_ids()


def test_but_its_letter_still_gets_written(monkeypatch):
    """
    Leaving the review queue must not strand a business: drafting asks for
    everyone contactable, not just everyone awaiting a decision. Without this
    an auto-qualified business would never get an email at all.
    """
    import outreach
    bid = _business()
    qualify.qualify_one(bid)
    targets, _ = db.list_businesses(contactable=True, needs_draft=True, limit=50)
    assert bid in [int(t["id"]) for t in targets]

    monkeypatch.setattr(outreach, "compose", lambda business_id, **kw: {
        "business_id": business_id, "to_email": "hello@clears.com.au",
        "subject": "s", "body": "b" * 300, "masthead": SITE})
    assert outreach.draft_batch(limit=5, use_ai=False)["drafted"] == 1


def test_the_reviewer_is_told_what_the_rule_would_take(client):
    _business()
    body = client.get("/admin/review").text
    assert "need nobody" in body
    assert f"score above {AUTO_QUALIFY_MIN_SCORE}" in body

    assert client.post("/api/qualify/sweep").json()["qualified"] == 1
    assert "need nobody" not in client.get("/admin/review").text


def test_the_outbox_reads_the_letters_the_queue_no_longer_sees(client, monkeypatch):
    """
    The review queue was the only place a draft got checked in bulk. A
    business that skips it must still have its letter read somewhere, or
    auto-qualifying would mean auto-sending unread email.
    """
    import outreach
    bid = _business()
    qualify.qualify_one(bid)
    monkeypatch.setattr(outreach, "compose", lambda business_id, **kw: {
        "business_id": business_id, "to_email": "hello@clears.com.au",
        "subject": "About your rating",
        "body": ("We put together a piece for the "
                 f"{mastheads.name_for(SITE)}. " * 12),
        "masthead": SITE})
    outreach.draft_batch(limit=5, use_ai=False)

    assert bid in review.drafts_awaiting_approval()
    got = client.get("/api/messages/clean").json()
    assert got["clean_count"] + got["flagged_count"] == 1


def test_the_draft_button_counts_what_it_would_actually_draft(client):
    """
    The count and the run have to ask the same question. The button lives on
    the review page but drafts for everyone contactable, so counting only the
    queue would promise thirteen and write twenty-seven.
    """
    for i in range(4):
        _business(domain=f"countme{i}.com.au")
    qualify.sweep()
    queued = len(review.queue_ids())
    assert queued == 0
    body = client.get("/admin/review").text
    assert "Draft the 4 without one" in body
