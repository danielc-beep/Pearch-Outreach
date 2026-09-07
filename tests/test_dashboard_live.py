"""
A dashboard with time in it.

A number on its own is a point, and a point cannot show a direction. These
cover the daily history behind each figure, the line drawn from it, and the
two signals that the app is doing something right now.
"""
from __future__ import annotations

import db


def test_a_day_is_written_once_and_rewritten(client):
    """One row a day, not one row a look — the table is the calendar's size."""
    db.record_today({"businesses": 10, "pipeline_value": 5000})
    db.record_today({"businesses": 12, "pipeline_value": 6000})
    rows = db.history(14)
    assert len(rows) == 1
    assert rows[0]["businesses"] == 12 and rows[0]["pipeline_value"] == 6000


def test_a_partial_write_leaves_the_rest_alone(client):
    db.record_today({"businesses": 10, "won_value": 4000})
    db.record_today({"businesses": 11})
    assert db.history(1)[0]["won_value"] == 4000


def _backfill(days):
    with db.tx() as conn:
        for i, value in enumerate(days):
            conn.execute(
                "INSERT OR REPLACE INTO metrics_daily "
                "(day, businesses, updated_at) VALUES (?, ?, ?)",
                (f"2026-08-{i + 1:02d}", value, db.now()))


def test_the_trend_is_the_series_and_what_it_did(client):
    _backfill([10, 14, 19, 25])
    trend = db.trend()
    assert trend["series"]["businesses"] == [10, 14, 19, 25]
    assert trend["change"]["businesses"] == 15


def test_one_day_of_history_reports_no_change_rather_than_none(client):
    """Nought would read as flat. There is no change to report yet."""
    _backfill([10])
    assert db.trend()["change"]["businesses"] is None


def test_the_window_holds_only_the_days_asked_for(client):
    _backfill(list(range(1, 21)))
    assert len(db.trend(14)["series"]["businesses"]) == 14


# ---------- The line ----------

def test_a_rising_series_draws_upward():
    """SVG y grows downward, so a rising line has to end nearer nought."""
    points = db.spark([1, 5, 9]).split()
    first_y = float(points[0].split(",")[1])
    last_y = float(points[-1].split(",")[1])
    assert last_y < first_y


def test_a_flat_series_draws_a_flat_line_rather_than_dividing_by_zero():
    points = db.spark([7, 7, 7]).split()
    heights = {p.split(",")[1] for p in points}
    assert len(heights) == 1


def test_one_reading_is_not_a_trend():
    assert db.spark([5]) == ""
    assert db.spark([]) == ""


def test_the_line_spans_the_whole_box():
    points = db.spark([1, 2, 3, 4]).split()
    assert points[0].startswith("0.0,")
    assert points[-1].startswith("100.0,")


# ---------- Work in flight ----------

def test_a_running_job_is_reported(client):
    db.start_run("google_places", {"industry": "plumber", "location": "Bega NSW"})
    running = db.running_now()
    assert len(running) == 1
    assert running[0]["query"]["location"] == "Bega NSW"


def test_a_finished_job_is_not(client):
    run_id = db.start_run("sample", {"industry": "plumber"})
    db.finish_run(run_id, found=3, new=3, dupes=0)
    assert db.running_now() == []


def test_a_job_that_died_hours_ago_is_not_still_running(client):
    """Otherwise the strip says "searching" for ever."""
    db.start_run("sample", {"industry": "plumber"})
    with db.tx() as conn:
        conn.execute("UPDATE prospecting_runs SET started_at = datetime('now', '-2 hours')")
    assert db.running_now() == []


# ---------- On the page ----------

def test_the_dashboard_carries_the_history_and_the_work(client):
    data = client.get("/api/dashboard").json()
    assert "trend" in data and "series" in data["trend"]
    assert "running" in data


def test_looking_at_the_dashboard_records_the_day(client):
    db.upsert_business({"name": "Somebody", "source": "csv"})
    client.get("/api/dashboard")
    assert db.history(1)[0]["businesses"] == 1


def test_the_page_draws_a_line_once_there_is_history(client):
    _backfill([10, 20, 30])
    body = client.get("/").text
    assert "<polyline" in body


def test_the_page_draws_no_line_on_the_first_day(client):
    body = client.get("/").text
    assert "<polyline" not in body


def test_only_one_thing_on_the_dashboard_is_boxed(client):
    """
    Twelve bordered rectangles is a form. One means "this is the thing to do".
    """
    body = client.get("/").text.split("{% endblock %}")[0]
    assert body.count('class="next-up"') <= 1
    assert 'class="card"' not in body.split("<script>")[0]
