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
