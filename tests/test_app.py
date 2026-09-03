def test_every_page_renders(client, sample_run):
    business_id = client.get("/api/businesses").json()["businesses"][0]["id"]
    for path in ("/", "/businesses", "/prospect", "/campaigns", "/outbox",
                 "/suppressions", "/unsubscribe", "/health",
                 f"/businesses/{business_id}", f"/prospect?run={sample_run['run_id']}"):
        response = client.get(path)
        assert response.status_code == 200, f"{path} returned {response.status_code}"


def test_home_shows_an_empty_state_before_anything_is_prospected(client):
    assert "database is empty" in client.get("/").text


def test_prospecting_through_the_api(client):
    response = client.post("/api/prospect/run", json={
        "source": "sample", "industry": "dentists", "location": "Bendigo VIC",
        "limit": 5, "enrich": False,
    })
    assert response.status_code == 200
    assert response.json()["new"] == 5


def test_unknown_source_is_a_400(client):
    response = client.post("/api/prospect/run", json={"source": "nope"})
    assert response.status_code == 400


def test_filters_narrow_the_list(client, sample_run):
    total = client.get("/api/businesses").json()["total"]
    contactable = client.get("/api/businesses?min_score=90").json()["total"]
    assert 0 < contactable <= total


def test_status_update_and_validation(client, sample_run):
    business_id = client.get("/api/businesses").json()["businesses"][0]["id"]
    ok = client.post(f"/api/businesses/{business_id}", json={"status": "qualified"})
    assert ok.status_code == 200 and ok.json()["status"] == "qualified"
    bad = client.post(f"/api/businesses/{business_id}", json={"status": "banana"})
    assert bad.status_code == 400
    assert client.post("/api/businesses/999999", json={"status": "new"}).status_code == 404


def test_draft_approve_send_flow(client, sample_run):
    business = next(b for b in client.get("/api/businesses").json()["businesses"] if b["email"])
    draft = client.post(f"/api/businesses/{business['id']}/draft", json={"use_ai": False})
    assert draft.status_code == 200
    message_id = draft.json()["id"]
    assert client.post(f"/api/messages/{message_id}/approve").json()["status"] == "approved"
    sent = client.post(f"/api/messages/{message_id}/send").json()
    assert sent["sent"] is False and sent["problems"]


def test_csv_export_has_a_header_and_a_row_per_business(client, sample_run):
    body = client.get("/api/export.csv").text.strip().splitlines()
    assert body[0].startswith("id,name,website,email")
    assert len(body) == 1 + client.get("/api/businesses").json()["total"]


def test_unsubscribe_page_suppresses(client, sample_run):
    business = next(b for b in client.get("/api/businesses").json()["businesses"] if b["email"])
    response = client.post("/unsubscribe", data={"email": business["email"]})
    assert response.status_code == 200
    assert "off the list" in response.text
    assert client.get("/api/businesses").json()["businesses"]


def test_demo_seed_fills_an_empty_database(monkeypatch):
    """A free instance boots with no disk — the demo seed must fill it."""
    import importlib
    import config, demo, db as db_module
    monkeypatch.setenv("PEARCH_DEMO_SEED", "1")
    importlib.reload(config)
    importlib.reload(demo)

    assert db_module.stats()["total"] == 0
    added = demo.seed_if_empty()
    assert added > 0
    stats = db_module.stats()
    assert stats["total"] == added
    assert stats["with_email"] > 0
    assert stats["by_status"]["qualified"] > 0     # pipeline isn't one flat column
    assert len(stats["top_regions"]) > 1           # more than one region represented

    # Running again must not duplicate anything.
    assert demo.seed_if_empty() == 0
    assert db_module.stats()["total"] == added

    monkeypatch.delenv("PEARCH_DEMO_SEED", raising=False)
    importlib.reload(config)
    importlib.reload(demo)


def test_demo_seed_is_off_by_default(sample_run):
    """Without the flag it must never touch the database."""
    import demo
    before = __import__("db").stats()["total"]
    assert demo.seed_if_empty() == 0
    assert __import__("db").stats()["total"] == before


def test_health_reports_which_integrations_are_live(client):
    """The read-out that settles 'the key looks set but the app disagrees'."""
    payload = client.get("/health").json()
    assert payload["status"] == "ok"
    assert payload["sources"]["sample"] is True
    assert payload["sources"]["google_places"] is False   # no key in the test env
    assert set(payload["configured"]) == {"password", "anthropic", "sending"}


def test_health_never_leaks_a_key(client, monkeypatch):
    import importlib
    import config, app as app_module
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "AIzaSuperSecretValue")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret")
    importlib.reload(config)

    body = client.get("/health").text
    assert "AIzaSuperSecretValue" not in body
    assert "sk-ant-secret" not in body

    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    importlib.reload(config)
    importlib.reload(app_module)


def test_an_api_key_pasted_with_a_trailing_newline_still_counts(monkeypatch):
    """Render's env editor is a textarea; a stray newline must not disable a source."""
    import importlib
    import config, sources.google_places as gp
    monkeypatch.setenv("GOOGLE_PLACES_API_KEY", "AIzaKey\n")
    importlib.reload(config)
    importlib.reload(gp)
    assert gp.available()[0] is True
    assert gp.GOOGLE_PLACES_API_KEY == "AIzaKey"

    monkeypatch.delenv("GOOGLE_PLACES_API_KEY", raising=False)
    importlib.reload(config)
    importlib.reload(gp)


def test_the_filter_form_url_works_with_every_box_left_blank(client, sample_run):
    """
    An HTML form submits every field it holds, so untouched boxes arrive as "".
    This is the exact URL the database filter form produces — it 422'd when
    min_score was typed as an int.
    """
    response = client.get("/businesses?q=wisebuy&status=&region=&industry=Home+loans"
                          "&has_email=&min_score=&sort=score")
    assert response.status_code == 200


def test_blank_and_junk_numbers_mean_no_filter(client, sample_run):
    total = client.get("/api/businesses").json()["total"]
    for value in ("", "abc", "  "):
        assert client.get(f"/api/businesses?min_score={value}").json()["total"] == total
    assert client.get("/businesses?page_no=").status_code == 200


def test_a_real_min_score_still_filters(client, sample_run):
    everything = client.get("/api/businesses").json()["total"]
    filtered = client.get("/api/businesses?min_score=95").json()["total"]
    assert filtered < everything


def test_the_export_survives_a_blank_min_score(client, sample_run):
    response = client.get("/api/export.csv?min_score=&q=&sort=score")
    assert response.status_code == 200
    assert response.text.startswith("id,name,website,email")


def test_the_form_redisplays_what_was_typed(client, sample_run):
    """A blank Min fit box must come back blank, not helpfully filled with a 0."""
    import re

    def min_fit_input(url: str) -> str:
        body = client.get(url).text
        match = re.search(r'<input id="f-score".*?/>', body, re.S)
        assert match, "the min fit input should be on the page"
        return match.group(0)

    assert 'value=""' in min_fit_input("/businesses?min_score=")
    assert 'value="75"' in min_fit_input("/businesses?min_score=75")


def test_the_sample_source_is_off_unless_switched_on(monkeypatch):
    """Fictional businesses must not be reachable on a live database."""
    import importlib
    import config, sources.seed as seed
    monkeypatch.setenv("PEARCH_ENABLE_SAMPLE_SOURCE", "0")
    monkeypatch.setenv("PEARCH_DEMO_SEED", "0")
    importlib.reload(config)
    importlib.reload(seed)
    available, reason = seed.available()
    assert available is False
    assert "PEARCH_ENABLE_SAMPLE_SOURCE" in reason

    monkeypatch.setenv("PEARCH_ENABLE_SAMPLE_SOURCE", "1")
    importlib.reload(config)
    importlib.reload(seed)
    assert seed.available()[0] is True


def test_search_covers_the_trade_as_google_names_it(client):
    """Google files a broker under category "Mortgage broker"; scoring calls it
    "Home loans". Searching either word must find them."""
    import db as db_module
    db_module.insert_business({
        "name": "Wisebuy Home Loans", "domain": "wisebuygroup.com.au",
        "industry": "Home loans", "category": "Mortgage broker",
        "suburb": "Cooks Hill", "region": "Newcastle", "fit_score": 100,
    })
    for term in ("wisebuy", "mortgage broker", "home loans", "cooks hill"):
        assert client.get(f"/api/businesses?q={term}").json()["total"] == 1, term


def test_the_industry_filter_matches_either_vocabulary(client):
    import db as db_module
    db_module.insert_business({
        "name": "Wisebuy Home Loans", "domain": "wisebuygroup.com.au",
        "industry": "Home loans", "category": "Mortgage broker", "fit_score": 100,
    })
    for label in ("Home loans", "Mortgage broker"):
        assert client.get(f"/api/businesses?industry={label}").json()["total"] == 1, label
    assert client.get("/api/businesses?industry=Dental").json()["total"] == 0


def test_an_empty_result_explains_which_half_failed(client):
    """The exact confusion from the field: a good search, an excluding filter."""
    import db as db_module
    db_module.insert_business({
        "name": "Wisebuy Home Loans", "domain": "wisebuygroup.com.au",
        "industry": "Home loans", "category": "Mortgage broker", "fit_score": 100,
    })
    body = client.get("/businesses?q=wisebuy&industry=Dental").text
    assert "the other filters exclude" in body
    assert "Search “wisebuy” on its own" in body

    # A search that genuinely matches nothing keeps the plain message.
    plain = client.get("/businesses?q=zzzznothing").text
    assert "Nothing matches those filters" in plain


def test_sample_records_can_be_purged_without_touching_real_ones(client):
    """Fictional businesses left over from sample runs must be removable."""
    import db as db_module
    db_module.insert_business({"name": "Riverbank Mortgage Brokers & Co",
                               "domain": "riverbank-mortgage-brokers-co.example.com.au",
                               "source": "sample", "fit_score": 95})
    db_module.insert_business({"name": "Orphaned sample", "domain": "old.example.com.au",
                               "source": "google_places", "fit_score": 50})
    db_module.insert_business({"name": "Wisebuy Home Loans", "domain": "wisebuygroup.com.au",
                               "source": "google_places", "fit_score": 100})

    assert db_module.count_sample_businesses() == 2      # caught by source and by domain
    result = client.post("/api/sample/purge").json()
    assert result["removed"] == 2
    assert result["remaining"] == 1
    assert [b["name"] for b in client.get("/api/businesses").json()["businesses"]] \
        == ["Wisebuy Home Loans"]


def test_purging_with_nothing_to_purge_is_harmless(client, sample_run):
    import db as db_module
    db_module.get_conn().execute("UPDATE businesses SET source = 'google_places', "
                                 "domain = replace(domain, '.example.com.au', '.com.au')")
    db_module.get_conn().commit()
    before = client.get("/api/businesses").json()["total"]
    assert client.post("/api/sample/purge").json()["removed"] == 0
    assert client.get("/api/businesses").json()["total"] == before


def test_a_single_business_can_be_deleted(client, sample_run):
    business = client.get("/api/businesses").json()["businesses"][0]
    before = client.get("/api/businesses").json()["total"]
    assert client.post(f"/api/businesses/{business['id']}/delete").status_code == 200
    assert client.get("/api/businesses").json()["total"] == before - 1
    assert client.post(f"/api/businesses/{business['id']}/delete").status_code == 404


def test_the_database_page_offers_to_remove_sample_records(client):
    import db as db_module
    db_module.insert_business({"name": "Fake Co", "domain": "fake.example.com.au",
                               "source": "sample", "fit_score": 90})
    body = client.get("/businesses").text
    assert "1 fictional business in the database" in body
    assert "btn-purge-sample" in body


def test_verification_flags_a_website_that_does_not_resolve(client, monkeypatch):
    """A fabricated domain has no DNS record — that is how it gets caught."""
    import db as db_module
    import enrich
    db_module.insert_business({"name": "Real Co", "domain": "wisebuygroup.com.au",
                               "website": "https://wisebuygroup.com.au", "fit_score": 90})
    db_module.insert_business({"name": "Invented Co", "domain": "riverbank.example.com.au",
                               "website": "https://riverbank.example.com.au", "fit_score": 90})

    monkeypatch.setattr(enrich, "website_is_live",
                        lambda url: "unreachable" if "example.com.au" in url else "live")

    result = client.post("/api/websites/verify").json()
    assert result == {"checked": 2, "live": 1, "unreachable": 1, "remaining": 0}

    dead = client.get("/api/businesses?").json()["businesses"]
    by_name = {b["name"]: b["website_status"] for b in dead}
    assert by_name["Real Co"] == "live"
    assert by_name["Invented Co"] == "unreachable"


def test_the_page_warns_about_unreachable_websites(client, monkeypatch):
    import db as db_module
    import enrich
    db_module.insert_business({"name": "Invented Co", "domain": "x.example.com.au",
                               "website": "https://x.example.com.au", "fit_score": 90})
    monkeypatch.setattr(enrich, "website_is_live", lambda url: "unreachable")
    client.post("/api/websites/verify")

    body = client.get("/businesses").text
    assert "1 website didn't respond" in body
    assert "site dead" in body


def test_verification_skips_what_it_has_already_checked(client, monkeypatch):
    import db as db_module
    import enrich
    db_module.insert_business({"name": "Co", "domain": "a.com.au",
                               "website": "https://a.com.au", "fit_score": 50})
    monkeypatch.setattr(enrich, "website_is_live", lambda url: "live")

    assert client.post("/api/websites/verify").json()["checked"] == 1
    assert client.post("/api/websites/verify").json()["checked"] == 0      # already known
    assert client.post("/api/websites/verify?recheck=true").json()["checked"] == 1


def test_verification_is_batched_so_it_cannot_outlive_the_proxy(client, monkeypatch):
    """
    A few hundred sites take minutes; a hosting proxy cuts the request off long
    before that and answers with HTML, which is what broke the page. Each call
    does a bounded batch and says how many are left.
    """
    import db as db_module
    import enrich
    for i in range(12):
        db_module.insert_business({"name": f"Co {i}", "domain": f"co{i}.com.au",
                                   "website": f"https://co{i}.com.au", "fit_score": 50})
    monkeypatch.setattr(enrich, "website_is_live", lambda url: "live")

    first = client.post("/api/websites/verify?limit=5").json()
    assert first["checked"] == 5 and first["remaining"] == 7

    second = client.post("/api/websites/verify?limit=5").json()
    assert second["checked"] == 5 and second["remaining"] == 2

    third = client.post("/api/websites/verify?limit=5").json()
    assert third["checked"] == 2 and third["remaining"] == 0

    # Every record ends up checked, and a further call is a no-op.
    assert client.post("/api/websites/verify?limit=5").json()["checked"] == 0
    rows = client.get("/api/businesses?limit=50").json()["businesses"]
    assert all(b["website_status"] == "live" for b in rows)


def test_a_batch_never_stalls_on_already_checked_records(client, monkeypatch):
    """The old query took the first N businesses then filtered, so once the head
    of the list was checked it did nothing while unchecked records sat below."""
    import db as db_module
    import enrich
    for i in range(6):
        db_module.insert_business({"name": f"Done {i}", "domain": f"d{i}.com.au",
                                   "website": f"https://d{i}.com.au",
                                   "website_status": "live", "fit_score": 50})
    db_module.insert_business({"name": "Unchecked", "domain": "new.com.au",
                               "website": "https://new.com.au", "fit_score": 50})
    monkeypatch.setattr(enrich, "website_is_live", lambda url: "live")

    result = client.post("/api/websites/verify?limit=3").json()
    assert result["checked"] == 1          # finds the one that needs it
    assert result["remaining"] == 0


def test_enrichment_reports_what_is_left(client, sample_run):
    result = client.post("/api/enrich/missing?limit=1").json()
    assert "remaining" in result
    assert result["checked"] <= 1


def test_enrichment_always_terminates(client, monkeypatch):
    """
    The batch must make progress even when it finds nothing.

    Selecting candidates on "has no email" cannot terminate: a visit that finds
    nothing leaves the record unchanged, so the same businesses return in every
    batch for ever. This is the regression test for that loop.
    """
    import db as db_module
    import enrich

    for i in range(8):
        db_module.insert_business({"name": f"No Email Co {i}", "domain": f"ne{i}.com.au",
                                   "website": f"https://ne{i}.com.au", "fit_score": 60})
    # Every site refuses — the worst case, and the one that used to spin.
    monkeypatch.setattr(enrich, "enrich_from_website",
                        lambda url: {"enrich_error": "could not fetch site",
                                     "website_status": "unreachable"})

    seen = []
    for _ in range(10):
        result = client.post("/api/enrich/missing?limit=3").json()
        seen.append((result["checked"], result["remaining"]))
        if not result["remaining"]:
            break

    assert seen == [(3, 5), (3, 2), (2, 0)], seen
    assert client.post("/api/enrich/missing").json()["checked"] == 0


def test_a_failed_visit_is_still_recorded_as_attempted(client, monkeypatch):
    import db as db_module
    import enrich
    db_module.insert_business({"name": "Blocked Co", "domain": "b.com.au",
                               "website": "https://b.com.au", "fit_score": 60})
    monkeypatch.setattr(enrich, "enrich_from_website",
                        lambda url: {"enrich_error": "could not fetch site"})

    client.post("/api/enrich/missing")
    business = client.get("/api/businesses").json()["businesses"][0]
    assert business["enriched_at"], "an attempted visit must be stamped"


def test_highest_scoring_businesses_are_enriched_first(client, monkeypatch):
    """The most useful addresses should arrive in the first batch."""
    import db as db_module
    import enrich
    db_module.insert_business({"name": "Low", "domain": "low.com.au",
                               "website": "https://low.com.au", "fit_score": 10})
    db_module.insert_business({"name": "High", "domain": "high.com.au",
                               "website": "https://high.com.au", "fit_score": 95})
    monkeypatch.setattr(enrich, "enrich_from_website", lambda url: {})

    result = client.post("/api/enrich/missing?limit=1").json()
    assert result["businesses"][0]["name"] == "High"
