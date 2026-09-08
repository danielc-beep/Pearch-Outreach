"""
The masthead board, and a CRM that means one masthead when you pick one.

The board's job is to show the empty titles as loudly as the busy ones, so
most of these are about what happens with no data rather than with some.
"""
from __future__ import annotations

import coverage
import crm
import db
import mastheads


def _seed(site, statuses, value=None):
    for status in statuses:
        db.insert_business({"name": f"B{status}", "status": status, "masthead": site,
                            "email": "a@b.com", "deal_value": value,
                            "rating": 4.6, "review_count": 40})


# ---------- The coverage board ----------

def test_every_masthead_is_on_the_board_even_with_no_data(client):
    board = coverage.board()
    assert board["totals"]["titles"] == len(mastheads.TITLES) == 78
    assert board["totals"]["untouched"] == 78


def test_a_masthead_moves_through_the_bands_as_its_book_does(client):
    def band_of(site):
        return next(t["band"] for g in coverage.board()["groups"]
                    for t in g["titles"] if t["site"] == site)

    site = "canberratimes.com.au"
    assert band_of(site) == "untouched"
    _seed(site, ["new"])
    assert band_of(site) == "found"
    _seed(site, ["contacted"])
    assert band_of(site) == "working"
    _seed(site, ["won"], 9000)
    assert band_of(site) == "earning"


def test_the_bands_are_one_ordered_scale(client):
    """Ordinal, so the page can shade them along a single ramp."""
    assert [b["key"] for b in coverage.BANDS] == \
        ["untouched", "found", "working", "earning"]


def test_records_under_a_title_we_do_not_publish_are_counted_not_hidden(client):
    """They are real records that need fixing, so the board says how many."""
    _seed("nothing-we-own.com.au", ["new", "new"])
    assert coverage.board()["totals"]["unaligned"] == 2


def test_the_revenue_on_the_board_is_the_revenue_in_the_database(client):
    _seed("canberratimes.com.au", ["won"], 9000)
    _seed("examiner.com.au", ["won"], 5000)
    assert coverage.board()["totals"]["revenue"] == 14000
    assert db.revenue(0)["won"]["total"] == 14000


def test_it_only_suggests_sweeping_patches_a_sweep_can_reach(client):
    """A national title has no home town, so a territory run cannot work it."""
    for title in coverage.next_to_sweep(20):
        assert title["patch"], title["name"]
        assert title["prospects"] == 0


def test_a_swept_masthead_stops_being_suggested(client):
    first = coverage.next_to_sweep(1)[0]
    _seed(first["site"], ["new"])
    assert first["site"] not in [t["site"] for t in coverage.next_to_sweep(20)]


def test_the_page_renders_all_of_them(client):
    _seed("canberratimes.com.au", ["won"], 9000)
    body = client.get("/mastheads").text
    assert "The Canberra Times" in body
    assert body.count('class="cov-tile') == 78
    assert "undefined" not in body


# ---------- A CRM that means one masthead ----------

def test_the_funnel_follows_the_filter(client):
    _seed("canberratimes.com.au", ["new", "new"])
    _seed("examiner.com.au", ["new"])
    counts = {s["key"]: s["count"] for s in crm.funnel(masthead="canberratimes.com.au")["stages"]}
    assert counts["new"] == 2
    assert {s["key"]: s["count"] for s in crm.funnel()["stages"]}["new"] == 3


def test_the_money_follows_the_filter(client):
    _seed("canberratimes.com.au", ["won"], 9000)
    _seed("examiner.com.au", ["won"], 5000)
    assert crm.summary(masthead="canberratimes.com.au")["won"] == 9000
    assert crm.summary()["won"] == 14000


def test_the_cold_count_follows_the_filter(client):
    _seed("canberratimes.com.au", ["contacted"])
    _seed("examiner.com.au", ["contacted"])
    with db.tx() as conn:
        conn.execute("UPDATE businesses SET created_at = datetime('now', '-40 days')")
    assert db.count_stale_in_stage("contacted", 14, masthead="canberratimes.com.au") == 1
    assert db.count_stale_in_stage("contacted", 14) == 2


def test_the_board_names_the_masthead_it_is_showing(client):
    _seed("canberratimes.com.au", ["new"])
    body = client.get("/crm?masthead=canberratimes.com.au").text
    assert "The Canberra Times" in body
    assert "Every masthead" in body, "a way back to the overview"


def test_an_empty_filter_does_not_claim_an_empty_database(client):
    """It used to send you off prospecting when you had just picked wrong."""
    _seed("canberratimes.com.au", ["new"])
    body = client.get("/crm?masthead=examiner.com.au").text
    assert "Nothing in the pipeline yet" not in body
    assert "There may be plenty under everything else" in body


def test_the_unfiltered_board_is_still_the_whole_book(client):
    _seed("canberratimes.com.au", ["new"])
    _seed("examiner.com.au", ["new"])
    assert crm.summary()["total"] == 2


# ---------- The nav ----------

def test_prospect_sits_next_to_the_dashboard(client):
    body = client.get("/").text
    order = [body.index(f'href="{path}"') for path in ("/", "/prospect", "/review")]
    assert order == sorted(order), "Dashboard, then Prospect, then the rest"
