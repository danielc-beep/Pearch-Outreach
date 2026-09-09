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


# ---------- The trades a sweep offers ----------

def test_there_are_forty_trades_and_no_repeats():
    import prospect
    assert len(prospect.TERRITORY_INDUSTRIES) == 40
    assert len(set(prospect.TERRITORY_INDUSTRIES)) == 40


def test_the_flat_list_is_the_groups_flattened():
    """The page renders groups; territory_step takes one trade at a time."""
    import prospect
    flat = [t for _, trades in prospect.TERRITORY_GROUPS for t in trades]
    assert prospect.TERRITORY_INDUSTRIES == flat


def test_every_trade_scores_on_industry_fit():
    """
    The ICP is matched as a substring of whatever the source calls the
    business, so family names do not match trade names: "building" is not in
    "Builder", "accounting" is not in "Accountant", "dental" is not in
    "Dentist". Ten of the original twelve sweep trades scored zero here.
    """
    import prospect, scoring
    missed = [t for t in prospect.TERRITORY_INDUSTRIES
              if not scoring._matches_icp_industry(t)]
    assert missed == [], f"these trades score nothing on industry fit: {missed}"


def test_a_trade_we_do_not_sell_to_still_scores_nothing():
    """The floor has to mean something — a wider ICP is not an empty one."""
    import scoring
    for outside in ("nightclub", "tattoo parlour", "pawnbroker", "casino"):
        assert not scoring._matches_icp_industry(outside), outside


def test_the_page_offers_every_trade_grouped(client):
    body = client.get("/prospect").text
    import prospect
    for name, _ in prospect.TERRITORY_GROUPS:
        assert name in body, name
    for trade in prospect.TERRITORY_INDUSTRIES:
        assert f'value="{trade}"' in body, trade


def test_a_sweep_still_takes_one_trade_at_a_time(client, monkeypatch):
    """Forty ticked is forty requests, not one request that outlives a proxy."""
    import prospect
    seen = []
    monkeypatch.setattr(prospect, "run",
                        lambda source_key, query, **kw: seen.append(query) or
                        {"found": 0, "new": 0, "dupes": 0, "businesses": []})
    prospect.territory_step("newcastleherald.com.au", "panel beater", enrich=False)
    assert len(seen) == 1
    assert seen[0]["industry"] == "panel beater"


# ---------- Searching for one business by name ----------
# A name and a trade are alternatives, not a pair: one asks for a business you
# already have in mind, the other for everyone doing a job in a town.

def test_a_name_finds_that_business(client):
    import sources.seed as seed
    got = seed.search({"name": "Coastal Plumbing", "location": "Newcastle NSW"})
    assert [b["name"] for b in got] == ["Coastal Plumbing"]


def test_a_name_asks_for_one_not_forty(client):
    import sources.seed as seed
    got = seed.search({"name": "Coastal Plumbing", "location": "Newcastle NSW", "limit": 40})
    assert len(got) == 1, "one business needs one result"


def test_a_trade_still_sweeps_the_town(client):
    import sources.seed as seed
    got = seed.search({"industry": "plumber", "location": "Newcastle NSW", "limit": 4})
    assert len(got) == 4
    assert len({b["name"] for b in got}) == 4


def test_neither_a_name_nor_a_trade_is_refused(client):
    """Google would answer a bare suburb with whatever it felt like."""
    from unittest.mock import patch
    import pytest
    import sources.google_places as places
    with patch.object(places, "available", lambda: (True, "")):
        with pytest.raises(ValueError, match="name or a business type"):
            places.search({"location": "Newcastle NSW"})


def test_a_named_search_does_not_ask_google_for_a_category(client):
    """
    "Bakers Delight in Newcastle" reads as a filter and comes back as every
    bakery in town. The name goes in as a plain phrase.
    """
    from unittest.mock import MagicMock, patch
    import sources.google_places as places
    sent = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"places": []}

    def capture(url, headers=None, json=None, **kw):
        sent.update(json or {})
        return FakeResponse()

    with patch.object(places, "available", lambda: (True, "")):
        with patch.object(places.httpx, "Client") as client_cls:
            client_cls.return_value.__enter__.return_value.post = capture
            places.search({"name": "Bakers Delight", "location": "Newcastle NSW"})
    assert sent["textQuery"] == "Bakers Delight Newcastle NSW"
    assert " in " not in sent["textQuery"]


def test_a_trade_search_still_reads_as_a_category(client):
    from unittest.mock import patch
    import sources.google_places as places
    sent = {}

    class FakeResponse:
        status_code = 200

        def json(self):
            return {"places": []}

    def capture(url, headers=None, json=None, **kw):
        sent.update(json or {})
        return FakeResponse()

    with patch.object(places, "available", lambda: (True, "")):
        with patch.object(places.httpx, "Client") as client_cls:
            client_cls.return_value.__enter__.return_value.post = capture
            places.search({"industry": "plumbers", "location": "Newcastle NSW"})
    assert sent["textQuery"] == "plumbers in Newcastle NSW"


def test_the_search_bar_offers_both(client):
    body = client.get("/prospect").text
    assert 'id="hs-business"' in body and 'id="hs-industry"' in body
    bar = body.split('class="searchbar"')[1].split("</form>")[0]
    # Either one will do, so neither can demand itself. Location still can:
    # a search always needs somewhere to search.
    for field in ("hs-business", "hs-industry"):
        tag = bar.split(f'id="{field}"')[1].split(">")[0]
        assert "required" not in tag, tag
    assert "required" in bar.split('id="hs-location"')[1].split(">")[0]


def test_the_run_endpoint_takes_a_name(client):
    got = client.post("/api/prospect/run",
                      json={"source": "sample", "name": "Coastal Plumbing",
                            "location": "Newcastle NSW", "enrich": False}).json()
    assert got["found"] == 1
    assert got["businesses"][0]["name"] == "Coastal Plumbing"
