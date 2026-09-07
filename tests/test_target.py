"""
The number to hit, and where deals actually go.

A dashboard of absolutes reports; it does not tell you anything. $41,000 of
closed won is either a triumph or a disaster and the page had no way to say
which.
"""
from __future__ import annotations

from datetime import date

import crm
import db
import target


def test_no_target_until_somebody_sets_one(client):
    assert target.progress()["set"] is False


def test_a_target_is_kept_and_read_back(client):
    target.set_target(150000, "quarter")
    assert target.get() == {"amount": 150000, "period": "quarter"}


def test_an_absurd_target_is_refused(client):
    import pytest
    for silly in (-1, 2_000_000_000):
        with pytest.raises(ValueError):
            target.set_target(silly, "quarter")
    with pytest.raises(ValueError):
        target.set_target(1000, "fortnight")


def test_the_quarter_is_the_calendar_quarter():
    assert target.window("quarter", date(2026, 8, 14)) == (date(2026, 7, 1), date(2026, 9, 30))
    assert target.window("quarter", date(2026, 1, 2)) == (date(2026, 1, 1), date(2026, 3, 31))
    assert target.window("quarter", date(2026, 12, 31)) == (date(2026, 10, 1), date(2026, 12, 31))


def test_the_month_and_year_windows():
    assert target.window("month", date(2026, 2, 9)) == (date(2026, 2, 1), date(2026, 2, 28))
    assert target.window("year", date(2026, 6, 6)) == (date(2026, 1, 1), date(2026, 12, 31))


def _won(value, when=None):
    fields = {"name": f"Won {value}", "source": "csv", "status": "won", "deal_value": value}
    if when:
        fields["won_at"] = when
    business_id, _ = db.upsert_business(fields)
    return business_id


def test_only_what_was_won_inside_the_period_counts(client):
    target.set_target(100000, "year")
    _won(40000, f"{date.today().year}-06-15T10:00:00+00:00")
    _won(25000, f"{date.today().year - 1}-06-15T10:00:00+00:00")   # last year
    assert target.progress()["won"] == 40000


def test_a_win_with_no_date_still_counts_from_when_it_was_touched(client):
    """Records won before the date existed are better counted than dropped."""
    target.set_target(100000, "year")
    _won(9000)
    assert target.progress()["won"] == 9000


def test_the_pace_says_what_is_needed_from_here(client):
    target.set_target(100000, "year")
    _won(20000, f"{date.today().year}-01-15T10:00:00+00:00")
    progress = target.progress()
    assert progress["short"] == 80000
    assert progress["pct"] == 20
    if progress["weeks_left"]:
        assert progress["needed_weekly"] == 80000 / progress["weeks_left"]


def test_being_ahead_and_behind_are_both_reported(client):
    target.set_target(100000, "year", )
    behind = target.progress(today=date(date.today().year, 12, 30))
    assert behind["ahead_by"] < 0
    _won(100000, f"{date.today().year}-01-05T10:00:00+00:00")
    ahead = target.progress(today=date(date.today().year, 1, 10))
    assert ahead["ahead_by"] > 0


def test_the_dashboard_shows_the_target(client):
    target.set_target(150000, "quarter")
    _won(41000)
    body = client.get("/").text
    assert "of $150,000 this quarter" in body
    assert "a week from here" in body


def test_the_endpoint_sets_it(client):
    assert client.post("/api/target", json={"amount": 90000, "period": "month"}).status_code == 200
    assert target.get()["amount"] == 90000


def test_the_endpoint_refuses_nonsense(client):
    assert client.post("/api/target", json={"amount": -5}).status_code == 400
    assert client.post("/api/target",
                       json={"amount": 100, "period": "fortnight"}).status_code == 400


# ---------- Conversion ----------

def _walk(stages):
    business_id, _ = db.upsert_business({"name": f"Deal {id(stages)}", "source": "csv",
                                         "status": "new"})
    for stage in stages:
        crm.move(business_id, stage)
    return business_id


def test_conversion_is_a_real_cohort(client):
    for _ in range(10):
        _walk(["qualified"])
    for _ in range(4):
        _walk(["qualified", "contacted"])
    rates = {c["key"]: c for c in crm.conversion()["stages"]}
    assert rates["qualified"]["reached"] == 14
    assert rates["qualified"]["advanced"] == 4
    assert round(rates["qualified"]["rate"]) == 29


def test_a_rate_can_never_exceed_a_hundred(client):
    """
    The first attempt at this printed 200%, because a deal that skips a stage
    made the stage after it look bigger than the stage before.
    """
    _walk(["qualified", "won"])          # skips contacted and replied
    _walk(["qualified", "contacted"])
    for stage in crm.conversion()["stages"]:
        if stage["rate"] is not None:
            assert 0 <= stage["rate"] <= 100, stage


def test_it_says_what_it_is_counted_from(client):
    """Businesses imported straight into a stage never moved and cannot count."""
    db.upsert_business({"name": "Imported", "source": "csv", "status": "contacted"})
    assert crm.conversion()["based_on"] == 0
    _walk(["qualified"])
    assert crm.conversion()["based_on"] == 1


def test_time_in_a_stage_is_measured_only_once_it_is_left(client):
    """An unfinished wait counted as finished makes a stall look fast."""
    business_id = _walk(["qualified"])
    assert db.stage_durations("qualified") == []
    crm.move(business_id, "contacted")
    assert len(db.stage_durations("qualified")) == 1


def test_the_old_activity_log_is_recovered(client):
    business_id, _ = db.upsert_business({"name": "Old Deal", "source": "csv", "status": "new"})
    db.log_activity(business_id, "stage", "Moved from Qualified to Contacted.")
    with db.tx() as conn:
        conn.execute("DELETE FROM stage_moves")
    assert db.backfill_moves() == 1
    assert db.reached("contacted") == [business_id]


def test_recovery_does_not_run_twice(client):
    business_id = _walk(["qualified"])
    assert db.backfill_moves() == 0        # there are already rows
