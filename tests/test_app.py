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
