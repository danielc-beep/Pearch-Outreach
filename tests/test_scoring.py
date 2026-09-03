from config import region_for_postcode
from scoring import band, score_business


def test_a_complete_prospect_scores_top():
    score, reasons = score_business({
        "email": "info@acme.com.au", "website": "https://acme.com.au",
        "industry": "mortgage broker", "region": "Hunter", "phone": "02 4000 0000",
        "address": "1 Hunter St", "rating": 4.6, "review_count": 40, "facebook": "fb",
    })
    assert score == 100
    assert any("Contactable" in r for r in reasons)


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
