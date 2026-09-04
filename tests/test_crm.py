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

def test_the_shares_add_up_to_the_whole(client):
    _seed({"new": 22, "researching": 9, "qualified": 14, "contacted": 18,
           "replied": 5, "won": 3, "lost": 4, "disqualified": 7})
    chart = crm.overview()
    assert chart["total"] == 82
    assert round(sum(s["pct"] for s in chart["stages"]), 6) == 100.0


def test_an_empty_stage_keeps_its_row(client):
    """A pipeline with nothing at Replied is telling you something."""
    _seed({"new": 3})
    chart = crm.overview()
    replied = next(s for s in chart["stages"] if s["key"] == "replied")
    assert replied["count"] == 0
    assert replied["path"] == ""          # nothing to draw
    assert len(chart["stages"]) == len(crm.STAGES)


def test_one_stage_holding_everything_draws_a_ring(client):
    """An arc from a point back to the same point draws nothing at all."""
    _seed({"new": 6})
    only = next(s for s in crm.overview()["stages"] if s["key"] == "new")
    assert only["path"] == "ring"


def test_an_empty_database_draws_nothing_and_says_so(client):
    chart = crm.overview()
    assert chart["total"] == 0
    assert all(s["path"] == "" for s in chart["stages"])
    assert "Nothing in the pipeline yet" in client.get("/crm").text


def test_every_drawn_segment_is_a_closed_path(client):
    _seed({"new": 4, "contacted": 4, "won": 2})
    for stage in crm.overview()["stages"]:
        if stage["path"] and stage["path"] != "ring":
            assert stage["path"].startswith("M ") and stage["path"].endswith(" Z")
            assert stage["path"].count("A ") == 2      # outer edge and inner


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

def test_the_page_shows_every_stage_with_its_count(client):
    _seed({"new": 2, "won": 1})
    body = client.get("/crm").text
    for stage in crm.STAGES:
        assert stage["label"] in body, stage["label"]
    assert "Nobody has looked at them yet" in body


def test_picking_a_stage_opens_only_that_stage(client):
    _seed({"new": 2, "contacted": 3})
    body = client.get("/crm?stage=contacted").text
    assert "3 at contacted" in body
    assert "Contacted Co 0" in body
    assert "New Co 0" not in body


def test_the_stage_offers_the_step_after_it(client):
    _seed({"contacted": 1})
    assert "Move to Replied" in client.get("/crm?stage=contacted").text


def test_a_finished_stage_offers_no_next_step(client):
    _seed({"won": 1})
    body = client.get("/crm?stage=won").text
    # The class name also appears in the page's own script, so match the
    # button, not the string.
    assert "js-next" not in body.split("<script>")[0]
    assert "Move to" not in body.split("<script>")[0]
    assert 'class="js-stage"' in body   # but you can still put it back


def test_an_unknown_stage_shows_the_pipeline_rather_than_failing(client):
    _seed({"new": 1})
    response = client.get("/crm?stage=nonsense")
    assert response.status_code == 200
    assert "Where everything is" in response.text


def test_the_campaigns_page_is_gone(client):
    assert client.get("/campaigns").status_code == 404
