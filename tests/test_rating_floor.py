"""
The Google rating floor.

Every outreach email opens by congratulating the business on its rating, so a
business we would not congratulate has no business being in the list. The
floor is applied at prospecting time, before anything is enriched or stored.
"""
from __future__ import annotations

import db
import prospect
from config import MIN_PROSPECT_RATING


def test_the_floor_is_four_stars():
    assert MIN_PROSPECT_RATING == 4.0


def test_what_clears_the_floor_and_what_does_not():
    assert prospect.meets_rating_floor({"rating": 4.0})
    assert prospect.meets_rating_floor({"rating": 4.9})
    assert not prospect.meets_rating_floor({"rating": 3.9})
    # No rating is not evidence of a good one.
    assert not prospect.meets_rating_floor({"name": "Unlisted Co"})
    assert not prospect.meets_rating_floor({"rating": None})
    assert not prospect.meets_rating_floor({"rating": "not a number"})


def test_a_csv_the_user_chose_is_exempt():
    # Their own list, often with no Google rating in it. Applying the
    # discovery floor there would silently throw the import away.
    assert prospect.meets_rating_floor({"name": "From my CSV"}, "csv")
    assert not prospect.meets_rating_floor({"name": "From a search"}, "google_places")


def test_a_run_never_stores_a_business_under_the_floor(client):
    prospect.run("sample", {"industry": "dentist", "location": "Bendigo VIC", "limit": 10},
                 enrich=False)
    rows, _ = db.list_businesses(limit=999)
    assert rows, "the run should have stored something"
    assert all(float(b["rating"]) >= 4.0 for b in rows)


def test_the_run_reports_what_it_dropped(monkeypatch):
    assert "below_rating" in prospect.run(
        "sample", {"industry": "dentist", "location": "Bendigo VIC", "limit": 3},
        enrich=False)


# ---------- Working what is already stored ----------

def _seed_a_spread():
    for name, rating in (("Five Star Plumbing", 4.8), ("Solid Sparkies", 4.1),
                         ("Just Under Co", 3.9), ("No Rating Listed", None)):
        db.upsert_business({"name": name, "industry": "Trades", "suburb": "Newcastle",
                            "rating": rating, "review_count": 40, "source": "google_places"})


def test_the_list_can_be_filtered_to_the_floor(client):
    _seed_a_spread()
    assert db.list_businesses(min_rating=4.0, limit=99)[1] == 2
    assert db.count_below_rating(4.0) == 2


def test_the_purge_clears_records_found_before_the_floor_existed(client):
    _seed_a_spread()
    removed = client.post("/api/businesses/purge-low-rated").json()["removed"]
    assert removed == 2
    assert db.count_below_rating(4.0) == 0
    # The ones that cleared it are untouched.
    assert db.list_businesses(limit=99)[1] == 2


def test_the_purge_route_is_not_swallowed_by_the_id_route(client):
    # A literal path declared after /{business_id} is matched as an id and
    # rejected with a 422. This is that regression.
    assert client.post("/api/businesses/purge-low-rated").status_code == 200


def test_the_csv_export_honours_the_filter(client):
    _seed_a_spread()
    body = client.get("/api/export.csv?min_rating=4").text
    assert "Five Star Plumbing" in body
    assert "Just Under Co" not in body


def test_a_blank_rating_box_is_not_a_filter(client):
    _seed_a_spread()
    # An untouched number box submits "", which must mean "no filter".
    assert client.get("/businesses?min_rating=").status_code == 200
    assert db.list_businesses(min_rating=0.0, limit=99)[1] == 4


# ---------- The floor at the two gates that come after prospecting ----------
# Filtering at discovery is not the same as enforcing the rule. A CSV import
# is exempt by design, and rows that predate the floor are still in the
# database, so both later gates check it for themselves.

def _seed_contactable():
    """A spread that is otherwise ready to be reviewed and emailed."""
    ids = {}
    for name, rating in (("Five Star Plumbing", 4.8), ("Solid Sparkies", 4.0),
                         ("Just Under Co", 3.9), ("No Rating Listed", None)):
        business_id, _ = db.upsert_business({
            "name": name, "industry": "Trades", "suburb": "Newcastle",
            "rating": rating, "review_count": 40, "source": "csv",
            "email": f"hello@{name.split()[0].lower()}.com.au",
            "website": f"https://{name.split()[0].lower()}.com.au",
        })
        ids[name] = business_id
    return ids


def test_the_review_queue_leaves_out_anything_under_the_floor(client):
    import review
    _seed_contactable()
    queued = {db.get_business(i)["name"] for i in review.queue_ids()}
    assert queued == {"Five Star Plumbing", "Solid Sparkies"}


def test_a_bulk_draft_run_will_not_write_to_them(client):
    import outreach
    _seed_contactable()
    result = outreach.draft_batch(limit=10, use_ai=False)
    written = {b["name"] for b in result["businesses"]}
    assert "Just Under Co" not in written
    assert "No Rating Listed" not in written
    assert "Five Star Plumbing" in written


def test_a_csv_import_still_lands_but_cannot_be_emailed(client):
    """
    The exemption at import is deliberate — a list someone chose should not be
    silently thrown away — so the send gate is what holds the line.
    """
    import outreach
    ids = _seed_contactable()
    assert db.get_business(ids["Just Under Co"])          # it is in the database
    message = outreach.draft_message(ids["Just Under Co"], use_ai=False)
    problems = outreach.preflight(db.get_message(message["id"]))
    assert any("rated 3.9" in p and "4-star floor" in p for p in problems), problems


def test_an_unrated_business_cannot_be_emailed_either(client):
    import outreach
    ids = _seed_contactable()
    message = outreach.draft_message(ids["No Rating Listed"], use_ai=False)
    problems = outreach.preflight(db.get_message(message["id"]))
    assert any("no Google rating" in p for p in problems), problems


def test_a_business_on_the_floor_exactly_is_clear_of_it(client):
    import outreach
    ids = _seed_contactable()
    message = outreach.draft_message(ids["Solid Sparkies"], use_ai=False)
    problems = outreach.preflight(db.get_message(message["id"]))
    assert not any("floor" in p for p in problems), problems
