import db
import prospect
import sources


def test_sample_source_is_always_available():
    keys = {s.key: s.available for s in sources.all_sources()}
    assert keys["sample"] is True
    assert keys["csv"] is True


def test_run_stores_scored_businesses(sample_run):
    assert sample_run["new"] == 8
    assert sample_run["duplicates"] == 0
    rows, total = db.list_businesses()
    assert total == 8
    assert all(r["fit_score"] > 0 for r in rows)
    assert all(r["source"] == "sample" for r in rows)


def test_rerunning_the_same_query_dedupes(sample_run):
    again = prospect.run(
        "sample",
        {"industry": "mortgage broker", "location": "Newcastle NSW", "limit": 8},
        enrich=False,
    )
    assert again["new"] == 0
    assert again["duplicates"] == 8
    assert db.list_businesses()[1] == 8


def test_run_records_which_businesses_it_touched(sample_run):
    run = db.get_run(sample_run["run_id"])
    assert len(run["result_ids"]) == 8
    assert len(db.businesses_by_ids(run["result_ids"])) == 8


def test_csv_import_maps_loose_headers():
    result = prospect.run("csv", {"csv": (
        "Business Name,Web,E-mail,Suburb,State,Postcode,Sector\n"
        "Hunter Legal Co,hunterlegal.com.au,info@hunterlegal.com.au,Newcastle,NSW,2300,Legal\n"
    )}, enrich=False)
    assert result["new"] == 1
    business = db.list_businesses()[0][0]
    assert business["name"] == "Hunter Legal Co"
    assert business["domain"] == "hunterlegal.com.au"
    assert business["region"] == "Newcastle"
    assert business["industry"] == "Legal"


def test_merging_never_blanks_a_known_field():
    first, created = db.upsert_business(
        {"name": "Acme", "domain": "acme.com.au", "email": "info@acme.com.au"})
    assert created
    second, created_again = db.upsert_business(
        {"name": "Acme", "domain": "acme.com.au", "phone": "02 4000 0000"})
    assert (second, created_again) == (first, False)
    stored = db.get_business(first)
    assert stored["email"] == "info@acme.com.au"
    assert stored["phone"] == "02 4000 0000"


def test_unavailable_source_explains_itself():
    info = sources.get_source("google_places")
    if not info.available:
        assert "GOOGLE_PLACES_API_KEY" in info.unavailable_reason
