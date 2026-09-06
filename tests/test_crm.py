"""
The pipeline as a pipeline.

The database could always be filtered by status, but a filter answers "show me
the contacted ones" and never "where is everything sitting". These cover the
shape, the drill-through, and moving a business along.
"""
from __future__ import annotations

import pytest

import crm
import db


def _seed(mix: dict[str, int]) -> None:
    for status, n in mix.items():
        for i in range(n):
            db.upsert_business({
                "name": f"{status.title()} Co {i}", "industry": "Plumber",
                "suburb": "Newcastle", "state": "NSW", "rating": 4.6,
                "review_count": 40, "source": "csv", "status": status,
                "masthead": "newcastleherald.com.au",
                "email": f"{status}{i}@example.com.au"})


# ---------- The stages themselves ----------

def test_every_status_the_database_uses_has_a_stage():
    """A status with no stage is invisible on the page that shows everything."""
    assert {s["key"] for s in crm.STAGES} == set(db.STATUSES)


def test_the_working_stages_form_one_unbroken_chain():
    step = crm.STAGES[0]
    walked = [step["key"]]
    while step["next"]:
        step = crm.BY_KEY[step["next"]]
        walked.append(step["key"])
    assert walked == ["new", "researching", "qualified", "contacted", "replied", "won"]


def test_the_endings_lead_nowhere():
    for ending in ("won", "lost", "disqualified"):
        assert crm.BY_KEY[ending]["next"] == ""
        assert crm.BY_KEY[ending]["next_label"] == ""


def test_each_stage_knows_the_name_of_the_next_one():
    """The "Move to …" button reads this, and an empty one says "Move to "."""
    assert crm.BY_KEY["qualified"]["next_label"] == "Contacted"
    assert crm.BY_KEY["replied"]["next_label"] == "Won"


def test_the_five_working_stages_are_an_ordinal_ramp():
    """
    Swap two of them and the meaning changes, so they take one hue in
    lightness order rather than five unrelated colours. Checked as: the blue
    channel climbs with progress, and no two share a colour.
    """
    ramp = [crm.BY_KEY[k]["colour"] for k in
            ("new", "researching", "qualified", "contacted", "replied")]
    assert len(set(ramp)) == 5
    brightness = [sum(int(c[i:i + 2], 16) for i in (1, 3, 5)) for c in ramp]
    assert brightness == sorted(brightness), ramp


def test_the_endings_do_not_borrow_a_stage_colour():
    ramp = {crm.BY_KEY[k]["colour"] for k in
            ("new", "researching", "qualified", "contacted", "replied")}
    for ending in ("won", "lost", "disqualified"):
        assert crm.BY_KEY[ending]["colour"] not in ramp


# ---------- The shape ----------

def test_the_bar_shares_add_up_to_the_live_pipeline(client):
    _seed({"new": 22, "researching": 9, "qualified": 14, "contacted": 18,
           "replied": 5, "won": 3, "lost": 4, "disqualified": 7})
    f = crm.funnel()
    assert f["total"] == 82
    # Lost and ruled out are not stages on the way anywhere, so the bar is
    # sized against what is still live and reports them separately.
    assert f["live"] == 71
    assert round(sum(s["pct"] for s in f["stages"]), 6) == 100.0
    assert {e["key"]: e["count"] for e in f["endings"]} == {"lost": 4, "disqualified": 7}


def test_a_pipeline_that_is_almost_all_new_still_reads(client):
    """
    The real database is 417 of 421 at New. As a pie that is one flat colour
    with slivers; as a bar every stage still holds a segment with its number.
    """
    _seed({"new": 417, "researching": 1, "qualified": 2, "contacted": 1})
    f = crm.funnel()
    by_key = {s["key"]: s for s in f["stages"]}
    assert round(by_key["new"]["pct"]) == 99
    assert by_key["researching"]["count"] == 1
    assert round(sum(s["pct"] for s in f["stages"]), 6) == 100.0


def test_an_empty_stage_keeps_its_place(client):
    """A pipeline with nothing at Replied is telling you something."""
    _seed({"new": 3})
    keys = [s["key"] for s in crm.funnel()["stages"]]
    assert keys == ["new", "researching", "qualified", "contacted", "replied", "won"]


def test_an_empty_database_says_so(client):
    assert crm.funnel()["total"] == 0
    assert "Nothing in the pipeline yet" in client.get("/crm").text


# ---------- Columns ----------

def test_a_column_holds_a_page_and_says_what_is_left(client):
    _seed({"new": 60})
    col = crm.column("new")
    assert len(col["cards"]) == crm.PAGE
    assert col["total"] == 60
    assert col["more"] == 60 - crm.PAGE


def test_the_next_page_carries_on_where_the_first_stopped(client):
    _seed({"new": 30})
    first = {c["id"] for c in crm.column("new")["cards"]}
    second = crm.column("new", offset=crm.PAGE)
    assert not (first & {c["id"] for c in second["cards"]})
    assert second["more"] == 0


def test_a_card_carries_how_long_it_has_sat_there(client):
    _seed({"contacted": 1})
    card = crm.column("contacted")["cards"][0]
    assert card["days"] == 0
    assert card["cold"] is False


def test_something_left_too_long_is_marked_cold(client):
    import db as database
    _seed({"contacted": 1})
    business = database.list_businesses(limit=1)[0][0]
    # Landed at this stage three weeks ago; the threshold is a fortnight.
    with database.tx() as conn:
        conn.execute("INSERT INTO activities (created_at, business_id, kind, detail) "
                     "VALUES (datetime('now', '-21 days'), ?, 'stage', 'moved')",
                     (business["id"],))
    card = crm.column("contacted")["cards"][0]
    assert card["days"] >= 21
    assert card["cold"] is True
    assert next(s for s in crm.funnel()["stages"] if s["key"] == "contacted")["cold"] == 1


def test_a_stage_with_no_deadline_never_goes_cold(client):
    """New has no threshold — an unworked list is a backlog, not a failure."""
    assert crm.BY_KEY["new"]["stale_after"] == 0
    _seed({"new": 1})
    assert crm.column("new")["cards"][0]["cold"] is False


def test_the_board_is_every_stage_in_working_order(client):
    _seed({"new": 1})
    assert [c["key"] for c in crm.board()] == [s["key"] for s in crm.STAGES]


def test_a_filter_narrows_every_column(client):
    _seed({"new": 2})
    assert crm.column("new", masthead="newcastleherald.com.au")["total"] == 2
    # A real key that no seeded business carries — "theexaminer.com.au" would
    # also return nothing, but only because there is no such masthead.
    assert "examiner.com.au" in __import__("mastheads").BY_SITE
    assert crm.column("new", masthead="examiner.com.au")["total"] == 0


# ---------- Moving along ----------

def test_moving_a_business_changes_its_stage(client):
    _seed({"qualified": 1})
    business = db.list_businesses(limit=1)[0][0]
    result = crm.move(business["id"], "contacted")
    assert result["changed"] is True
    assert db.get_business(business["id"])["status"] == "contacted"


def test_the_move_is_written_into_its_history(client):
    _seed({"qualified": 1})
    business = db.list_businesses(limit=1)[0][0]
    crm.move(business["id"], "contacted")
    notes = [a["detail"] for a in db.list_activities(business["id"])]
    assert any("Qualified to Contacted" in n for n in notes), notes


def test_moving_it_where_it_already_is_does_nothing(client):
    _seed({"qualified": 1})
    business = db.list_businesses(limit=1)[0][0]
    assert crm.move(business["id"], "qualified")["changed"] is False
    assert db.list_activities(business["id"]) == []


def test_an_invented_stage_is_refused(client):
    _seed({"new": 1})
    business = db.list_businesses(limit=1)[0][0]
    with pytest.raises(ValueError):
        crm.move(business["id"], "nearly-sold")
    assert db.get_business(business["id"])["status"] == "new"


def test_the_api_refuses_it_too(client):
    _seed({"new": 1})
    business = db.list_businesses(limit=1)[0][0]
    assert client.post(f"/api/crm/{business['id']}/stage",
                       json={"stage": "nearly-sold"}).status_code == 400


def test_the_api_moves_it(client):
    _seed({"new": 1})
    business = db.list_businesses(limit=1)[0][0]
    response = client.post(f"/api/crm/{business['id']}/stage", json={"stage": "researching"})
    assert response.status_code == 200
    assert db.get_business(business["id"])["status"] == "researching"


# ---------- The page ----------

def test_the_board_shows_a_column_for_every_stage(client):
    _seed({"new": 2, "won": 1})
    body = client.get("/crm").text
    for stage in crm.STAGES:
        assert f'id="col-{stage["key"]}"' in body, stage["key"]


def test_the_cards_are_on_the_page_under_their_stage(client):
    _seed({"new": 1, "contacted": 1})
    body = client.get("/crm").text
    assert "New Co 0" in body and "Contacted Co 0" in body


def test_a_card_can_be_dragged_and_has_a_button_too(client):
    """Drag is the fast path; the arrow and the arrow keys are the other two."""
    _seed({"qualified": 1})
    body = client.get("/crm").text
    assert 'draggable="true"' in body
    assert 'class="deal-go js-advance" data-to="contacted"' in body


def test_a_finished_stage_offers_no_next_step(client):
    _seed({"won": 1})
    markup = client.get("/crm").text.split("<script>")[0]
    won = markup.split('id="col-won"')[1].split("</section>")[0]
    assert "js-advance" not in won


def test_a_big_column_loads_a_page_at_a_time(client):
    _seed({"new": 60})
    body = client.get("/crm").text
    assert body.count('class="deal ') + body.count('class="deal is-cold') <= crm.PAGE + 2
    assert "Show 24 more" in body


def test_the_column_endpoint_returns_the_next_page(client):
    _seed({"new": 40})
    response = client.get("/api/crm/column?stage=new&offset=24")
    assert response.status_code == 200
    data = response.json()
    assert len(data["cards"]) == 16
    assert data["more"] == 0
    assert set(data["cards"][0]) >= {"id", "name", "days", "cold"}


def test_the_column_endpoint_refuses_an_invented_stage(client):
    assert client.get("/api/crm/column?stage=nonsense").status_code == 400


def test_the_campaigns_page_is_gone(client):
    assert client.get("/campaigns").status_code == 404
