"""
Enrichment batching: every business ends up settled, batch or no batch.

The complaint this comes from: "Find missing emails" ran, timed out, and
left the businesses it had plainly visited still marked as unenriched.
"""
from __future__ import annotations

import time
from unittest.mock import patch

import db
import prospect


def _seed(n=6):
    for i in range(n):
        db.upsert_business({
            "name": f"Business {i}", "website": f"https://business{i}.com.au",
            "suburb": "Newcastle", "industry": "Trades", "rating": 4.5,
            "review_count": 30, "source": "google_places",
        })
    return db.list_businesses(limit=99)[0]


def test_every_business_is_stamped_even_when_nothing_is_found(client):
    """
    Enriched_at is what takes a business out of the queue for next time.
    Without it the same businesses are visited again on every run, which is
    how one batch turned into thirty minutes of the same sites.
    """
    _seed(4)
    with patch.object(prospect, "enrich_record",
                      side_effect=lambda b: {**b, "enriched_at": db.now(),
                                             "_enrich_note": "No email published"}):
        result = prospect.enrich_missing(limit=10)
    assert result["checked"] == 4
    assert result["found"] == 0
    for row in db.list_businesses(limit=99)[0]:
        assert row["enriched_at"], row["name"]
    # And a second run has nothing left to do.
    assert prospect.enrich_missing(limit=10)["checked"] == 0


def test_a_result_is_written_as_soon_as_it_arrives(client):
    """
    Not after the whole batch. A run that is cut off halfway must still have
    recorded the half it finished.
    """
    _seed(3)
    written_during_run = []

    def slow(business):
        # Whatever has already been written is visible to this thread.
        written_during_run.append(
            db.list_businesses(has_email=True, limit=99)[1])
        time.sleep(0.05)
        return {**business, "email": f"hi@{business['domain']}", "enriched_at": db.now()}

    with patch.object(prospect, "enrich_record", side_effect=slow):
        prospect.enrich_missing(limit=10)

    assert db.list_businesses(has_email=True, limit=99)[1] == 3


def test_one_failing_site_does_not_fail_the_batch(client):
    _seed(4)
    rows = db.list_businesses(limit=99)[0]
    bad = rows[0]["id"]

    def flaky(business):
        if int(business["id"]) == bad:
            raise RuntimeError("connection reset")
        return {**business, "email": f"hi@{business['domain']}", "enriched_at": db.now()}

    with patch.object(prospect, "enrich_record", side_effect=flaky):
        result = prospect.enrich_missing(limit=10)

    assert result["checked"] == 3
    # The one that blew up is still marked, so it is not retried forever.
    assert db.get_business(bad)["enriched_at"]


def test_the_batch_gives_up_rather_than_hanging(client):
    """A request that never returns records nothing and tells you nothing."""
    _seed(3)

    def glacial(business):
        time.sleep(30)
        return business

    with patch.object(prospect, "BATCH_BUDGET", 0.3), \
         patch.object(prospect, "enrich_record", side_effect=glacial):
        started = time.monotonic()
        result = prospect.enrich_missing(limit=3)
        elapsed = time.monotonic() - started

    assert elapsed < 5, "the budget should have ended it"
    assert result["timed_out"] == 3
    assert result["checked"] == 0


def test_remaining_counts_down_so_the_caller_keeps_looping(client):
    """
    The client stops when remaining stops falling. An unfinished business is
    already inside `outstanding`, so counting it again would make remaining
    grow and read as a stall.
    """
    _seed(8)
    with patch.object(prospect, "enrich_record",
                      side_effect=lambda b: {**b, "enriched_at": db.now()}):
        first = prospect.enrich_missing(limit=3)
        second = prospect.enrich_missing(limit=3)
    assert first["remaining"] == 5
    assert second["remaining"] == 2
    assert second["remaining"] < first["remaining"]
