from config import region_for_postcode
from scoring import band, score_business


PERFECT = {
    "email": "info@acme.com.au", "website": "https://acme.com.au",
    "industry": "mortgage broker", "region": "Hunter", "phone": "02 4000 0000",
    "address": "1 Hunter St", "rating": 4.9, "review_count": 300, "facebook": "fb",
}


def test_the_best_possible_prospect_scores_a_hundred():
    score, reasons = score_business(PERFECT)
    assert score == 100
    assert any("Contactable" in r for r in reasons)


def test_a_merely_good_one_does_not():
    """
    Everything on file, but 4.6 stars and forty reviews rather than 4.9 and
    three hundred. Under the old card those were flat awards and this scored
    the same hundred as the best business in the database.
    """
    score, _ = score_business({**PERFECT, "rating": 4.6, "review_count": 40})
    assert 88 <= score < 100


def test_no_website_is_penalised():
    score, reasons = score_business({"name": "Anon"})
    assert score == 0
    assert any("No website" in r for r in reasons)


def test_do_not_contact_zeroes_the_score():
    score, _ = score_business({
        "email": "a@b.com", "website": "x", "industry": "legal",
        "region": "Hunter", "do_not_contact": 1,
    })
    assert score == 0


def test_bands():
    assert band(90) == "hot"
    assert band(60) == "warm"
    assert band(40) == "cool"
    assert band(5) == "cold"


def test_postcode_to_region():
    assert region_for_postcode("2300") == ("NSW", "Newcastle")
    assert region_for_postcode(3550) == ("VIC", "Bendigo")
    assert region_for_postcode("nope") == (None, None)


# ---------- What the scorecard is measured against ----------

def test_the_rating_award_is_graded_not_a_gate():
    """
    Prospecting refuses anything under four stars, so "four stars or better"
    awarded the same points to every record in the database — a signal that
    never varies is not a signal.
    """
    # With a website, or the no-website penalty floors every one of them at
    # zero and the grading is invisible.
    seen = {score_business({"website": "https://x", "rating": r})[0]
            for r in (4.0, 4.3, 4.6, 4.9)}
    assert len(seen) == 4, seen


def test_the_review_award_is_graded_too():
    seen = {score_business({"website": "https://x", "review_count": n})[0]
            for n in (6, 20, 60, 150, 400)}
    assert len(seen) == 5, seen


def test_more_reviews_never_scores_less():
    scores = [score_business({"website": "https://x", "review_count": n})[0]
              for n in (5, 15, 40, 100, 200, 900)]
    assert scores == sorted(scores), scores


def test_a_business_in_an_acm_town_is_recognised():
    """
    The old region list held ten heartland names and missed sixty of the
    sixty-seven towns an ACM masthead covers. Albury scored zero for being in
    an ACM region while sitting under the Border Mail.
    """
    for town in ("Albury", "Bathurst", "Burnie", "Bega", "Warrnambool", "Orange"):
        _, reasons = score_business({"website": "https://x", "suburb": town})
        assert any("ACM masthead" in r for r in reasons), town


def test_a_stored_alignment_counts_even_when_the_town_does_not():
    _, reasons = score_business({"website": "https://x", "suburb": "Nowhereville",
                                 "masthead": "newcastleherald.com.au"})
    assert any("ACM masthead" in r for r in reasons)


def test_somewhere_no_masthead_covers_scores_nothing_for_it():
    _, reasons = score_business({"website": "https://x", "suburb": "Surry Hills"})
    assert not any("ACM masthead" in r for r in reasons)


def test_a_website_we_checked_and_could_not_reach_is_worth_less():
    live = score_business({"website": "https://x", "website_status": "live"})[0]
    dead = score_business({"website": "https://x", "website_status": "unreachable"})[0]
    unchecked = score_business({"website": "https://x"})[0]
    assert dead < unchecked == live


def test_the_score_never_leaves_nought_to_a_hundred():
    assert score_business({**PERFECT, "do_not_contact": 1})[0] == 0
    assert score_business(PERFECT)[0] <= 100


# ---------- Getting the fix onto the records already stored ----------
# A scorecard is written once, when a business is found. Change the scorecard
# and every ranking in the app stays sorted by the old rules, silently.

def _stored(business_id):
    import db
    return int(db.get_business(business_id)["fit_score"])


def test_a_rescore_corrects_a_stored_score(client):
    import db, prospect
    business_id, _ = db.upsert_business({
        "name": "Bathurst Plumbing", "industry": "Plumber", "suburb": "Bathurst",
        "state": "NSW", "website": "https://x.com.au", "email": "a@x.com.au",
        "rating": 4.9, "review_count": 300, "source": "csv"})
    # Whatever it was found with, put it back to a wrong number.
    db.update_business(business_id, {"fit_score": 12, "score_reasons": []})
    result = prospect.rescore_all()
    assert result["changed"] == 1
    assert _stored(business_id) > 12


def test_the_reasons_are_rewritten_with_the_score(client):
    import db, prospect
    business_id, _ = db.upsert_business({
        "name": "Albury Dental", "industry": "Dentist", "suburb": "Albury",
        "website": "https://x.com.au", "email": "a@x.com.au", "rating": 4.8,
        "review_count": 220, "source": "csv"})
    db.update_business(business_id, {"fit_score": 1, "score_reasons": ["+1 nonsense"]})
    prospect.rescore_all()
    reasons = db.get_business(business_id)["score_reasons"]
    assert not any("nonsense" in r for r in reasons)
    assert any("ACM masthead" in r for r in reasons)


def test_a_run_that_changes_nothing_writes_nothing(client):
    import prospect
    prospect.run("sample", {"industry": "dentist", "location": "Newcastle NSW",
                            "limit": 6}, enrich=False)
    prospect.rescore_all()                      # settle everything first
    again = prospect.rescore_all()
    assert again["changed"] == 0
    assert again["checked"] > 0


def test_it_reports_what_moved_most(client):
    import db, prospect
    for i in range(3):
        business_id, _ = db.upsert_business({
            "name": f"Mover {i}", "industry": "Plumber", "suburb": "Bathurst",
            "website": "https://x.com.au", "email": f"a{i}@x.com.au",
            "rating": 4.9, "review_count": 300, "source": "csv"})
        db.update_business(business_id, {"fit_score": i, "score_reasons": []})
    result = prospect.rescore_all()
    assert len(result["biggest"]) == 3
    assert result["biggest"][0]["moved"] > 0
    assert set(result["biggest"][0]) >= {"id", "name", "was", "now", "moved"}


def test_scores_are_stale_until_a_rescore_runs(client):
    import prospect, scoring
    assert prospect.scores_are_stale() is True
    prospect.rescore_all()
    assert prospect.scores_are_stale() is False
    import db
    assert db.get_setting("scoring_version") == scoring.VERSION


def test_a_new_scorecard_makes_them_stale_again(client, monkeypatch):
    import prospect, scoring
    prospect.rescore_all()
    monkeypatch.setattr(scoring, "VERSION", "2027-01-01")
    assert prospect.scores_are_stale() is True


def test_every_business_is_covered_however_many_there_are(client):
    """Paged, so a database of thousands is not one query and one big list."""
    import db, prospect
    for i in range(25):
        business_id, _ = db.upsert_business({
            "name": f"Batch {i}", "industry": "Plumber", "suburb": "Bathurst",
            "website": "https://x.com.au", "email": f"b{i}@x.com.au",
            "rating": 4.7, "review_count": 60, "source": "csv"})
        db.update_business(business_id, {"fit_score": 0, "score_reasons": []})
    result = prospect.rescore_all(batch=4)
    assert result["checked"] == 25
    assert result["changed"] == 25


def test_editing_the_card_makes_the_scores_stale_by_itself(monkeypatch):
    """
    A version string you have to remember to bump is one that eventually does
    not get bumped, and then the scores are quietly wrong again.
    """
    import scoring
    before = scoring.fingerprint()
    monkeypatch.setitem(scoring.WEIGHTS, "email", (25, "Contactable"))
    assert scoring.fingerprint() != before


def test_editing_the_icp_does_the_same(monkeypatch):
    import scoring
    from config import ICP
    before = scoring.fingerprint()
    monkeypatch.setitem(ICP, "industries", list(ICP["industries"]) + ["hot air balloon"])
    assert scoring.fingerprint() != before


def test_reordering_the_icp_does_not(monkeypatch):
    """Same card, different order — rescoring every record for that is waste."""
    import scoring
    from config import ICP
    before = scoring.fingerprint()
    monkeypatch.setitem(ICP, "industries", list(reversed(ICP["industries"])))
    assert scoring.fingerprint() == before
