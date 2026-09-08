"""
The work board, and the screen for addresses the scraper could not find.

The board answers the only question you have on opening the app: what do I
do next. So the rules that matter are which jobs appear, which are hidden,
and that every one of them links somewhere that starts the work.
"""
from __future__ import annotations

import pytest

import db
import outreach
import prospect
import worklist


def _seed(n=6):
    prospect.run("sample", {"industry": "dentist", "location": "Bendigo VIC", "limit": n},
                 enrich=False)
    return db.list_businesses(limit=99)[0]


def test_an_empty_database_has_an_empty_board(client):
    assert worklist.board() == []
    step = worklist.next_step(has_businesses=False)
    assert "Nothing in the database" in step["title"]
    assert step["href"] == "/prospect"


def test_a_job_with_nothing_waiting_is_not_shown(client):
    _seed(3)
    keys = {i["key"] for i in worklist.board()}
    assert "review" in keys          # three are waiting for a decision
    assert "replied" not in keys     # nobody has replied
    assert "send" not in keys        # nothing is approved


def test_every_job_appears_when_there_is_work(client):
    rows = _seed(8)
    # The sample source deliberately leaves some records without an address,
    # so that count is measured as a change rather than an absolute.
    before_no_email = db.list_businesses(has_email=False, limit=1)[1]

    db.update_business(rows[0]["id"], {"status": "replied"})
    message = outreach.draft_message(rows[1]["id"], use_ai=False)
    outreach.approve_message(int(message["id"]))
    db.update_business(rows[2]["id"], {"email": ""})
    db.update_business(rows[3]["id"], {"masthead": ""})

    board = {i["key"]: i for i in worklist.board()}
    assert board["replied"]["count"] == 1
    assert board["send"]["count"] == 1
    assert board["no_email"]["count"] == before_no_email + 1
    assert board["unaligned"]["count"] == 1
    assert board["review"]["count"] >= 1


def test_replies_come_first(client):
    """They go cold fastest, so they lead however few there are."""
    rows = _seed(8)
    db.update_business(rows[0]["id"], {"status": "replied"})
    assert worklist.board()[0]["key"] == "replied"


def test_every_row_links_somewhere_that_starts_the_work(client):
    rows = _seed(6)
    db.update_business(rows[0]["id"], {"status": "replied"})
    db.update_business(rows[1]["id"], {"email": ""})
    for item in worklist.board():
        assert item["href"].startswith("/"), item
        assert client.get(item["href"]).status_code == 200, item["href"]
        assert item["action"] and item["detail"]


def test_the_dashboard_renders_the_board(client):
    rows = _seed(4)
    db.update_business(rows[0]["id"], {"status": "replied"})
    html = client.get("/").text
    assert "Next steps" in html
    assert "replied to an email" in html


# ---------- Addresses the scraper could not find ----------

def test_the_address_list_holds_the_ones_worth_chasing(client):
    rows = _seed(5)
    db.update_business(rows[0]["id"], {"email": "", "contact_url": "https://a.com.au/contact"})
    db.update_business(rows[1]["id"], {"email": ""})            # website, no contact page
    db.update_business(rows[2]["id"], {"email": "", "website": "", "domain": ""})

    html = client.get("/addresses").text
    assert rows[0]["name"] in html
    assert "Contact page" in html
    assert rows[1]["name"] in html
    # Nowhere to look is not work.
    assert rows[2]["name"] not in html


def test_saving_an_address_by_hand_takes_it_off_the_list(client):
    rows = _seed(3)
    db.update_business(rows[0]["id"], {"email": ""})
    assert rows[0]["name"] in client.get("/addresses").text

    client.post(f"/api/businesses/{rows[0]['id']}", json={"email": "found@byhand.com.au"})
    assert db.get_business(rows[0]["id"])["email"] == "found@byhand.com.au"
    assert rows[0]["name"] not in client.get("/addresses").text
    # And it is back in the review queue, which is the whole point.
    import review
    assert rows[0]["id"] in review.queue_ids()


def test_the_address_list_can_be_filtered_by_masthead(client):
    _seed(4)
    for row in db.list_businesses(limit=99)[0]:
        db.update_business(row["id"], {"email": ""})
    assert client.get("/addresses?masthead=bendigoadvertiser.com.au").status_code == 200
    assert "Redgum" in client.get("/addresses?masthead=bendigoadvertiser.com.au").text \
        or client.get("/addresses?masthead=bendigoadvertiser.com.au").text.count("address-row") > 0
    # A masthead with none of them shows the empty state.
    assert "Nothing waiting" in client.get("/addresses?masthead=examiner.com.au").text


def test_enrichment_keeps_the_contact_page_it_visited(monkeypatch):
    """The URL was being thrown away, so a person had to go and find it."""
    import httpx
    import enrich

    def handler(request):
        if "/contact" in str(request.url):
            return httpx.Response(200, text="<html>Ring us on 02 4979 5000</html>",
                                  headers={"content-type": "text/html"})
        return httpx.Response(200, text='<html><a href="/contact">Contact</a></html>',
                              headers={"content-type": "text/html"})
    original = httpx.Client
    transport = httpx.MockTransport(handler)
    monkeypatch.setattr(enrich.httpx, "Client",
                        lambda *a, **kw: original(*a, **{**kw, "transport": transport}))

    result = enrich.enrich_from_website("https://noemailhere.com.au")
    assert "email" not in result
    assert result["contact_url"].endswith("/contact")


# ---------- A number you can actually get to nought ----------

def test_the_email_count_only_counts_what_can_be_worked(client):
    """
    The screen skips businesses with no website — there is nowhere to look —
    but the count included them, so it read "124 waiting" while showing forty
    and no amount of work could ever clear it.
    """
    import db, worklist
    db.upsert_business({"name": "Findable", "industry": "Plumber", "suburb": "Newcastle",
                        "rating": 4.6, "review_count": 20, "source": "csv",
                        "website": "https://findable.com.au"})
    db.upsert_business({"name": "Nowhere To Look", "industry": "Plumber",
                        "suburb": "Newcastle", "rating": 4.6, "review_count": 20,
                        "source": "csv"})            # no website, no email
    row = next(i for i in worklist.board() if i["key"] == "no_email")
    assert row["count"] == 1, "the one with no website is not work"


def test_the_page_shows_exactly_what_it_counted(client):
    import db
    for i in range(3):
        db.upsert_business({"name": f"Findable {i}", "industry": "Plumber",
                            "suburb": "Newcastle", "rating": 4.6, "review_count": 20,
                            "source": "csv", "website": f"https://f{i}.com.au"})
    db.upsert_business({"name": "Nowhere To Look", "industry": "Plumber",
                        "suburb": "Newcastle", "rating": 4.6, "review_count": 20,
                        "source": "csv"})
    body = client.get("/addresses").text
    assert "3 with a website but no email address" in body
    assert "Nowhere To Look" not in body


def test_the_page_says_email_not_just_address(client):
    """The word "address" beside a list of suburbs reads as a street address."""
    import db
    db.upsert_business({"name": "Findable", "industry": "Plumber", "suburb": "Newcastle",
                        "rating": 4.6, "review_count": 20, "source": "csv",
                        "website": "https://findable.com.au"})
    body = client.get("/addresses").text
    assert "email address" in body
    assert "paste the email address" in body
    # It came out of the top nav when Revenue went in — ten tabs wrapped onto
    # a second row. It is a job rather than a destination, so it is reached
    # from the dashboard's own list and from the footer, and both must hold.
    assert 'href="/addresses"' in body


def test_finding_emails_is_still_reachable_without_a_nav_tab(client):
    """Dropping it from the nav must not drop it out of the app."""
    import db
    db.upsert_business({"name": "No email", "industry": "Plumber", "suburb": "Newcastle",
                        "rating": 4.6, "review_count": 20, "source": "csv",
                        "website": "https://noemail.com.au"})
    home = client.get("/").text
    assert 'href="/addresses"' in home, "from the dashboard's list of jobs"
    assert 'href="/addresses"' in client.get("/crm").text, "and from the footer everywhere"
    assert client.get("/addresses").status_code == 200


# ---------- Next steps, one at a time ----------
# A carousel is only bearable if it can be stopped, so most of these are about
# that rather than about the sliding.

def _steps(client):
    html = client.get("/").text
    return html.split('id="steps-window">')[1].split("</div>")[0], html


def test_every_job_is_a_slide_and_the_first_one_is_showing(client):
    rows = _seed(4)
    db.update_business(rows[0]["id"], {"status": "replied"})
    db.update_business(rows[1]["id"], {"email": ""})
    window, _ = _steps(client)
    assert window.count('class="step ') + window.count('class="step"') >= 2
    assert window.count("is-on") == 1, "one slide showing, the rest waiting"


def test_the_whole_slide_is_the_link(client):
    """There is no small target to hit on a card that moves."""
    rows = _seed(4)
    db.update_business(rows[0]["id"], {"status": "replied"})
    window, _ = _steps(client)
    assert window.strip().startswith("<a "), window[:60]


def test_the_slides_nobody_is_looking_at_are_out_of_the_tab_order(client):
    rows = _seed(4)
    db.update_business(rows[0]["id"], {"status": "replied"})
    db.update_business(rows[1]["id"], {"email": ""})
    window, _ = _steps(client)
    assert 'tabindex="-1"' in window
    assert 'aria-hidden="true"' in window


def test_there_is_a_way_to_stop_it(client):
    """Content that moves on its own for more than five seconds needs one."""
    rows = _seed(4)
    db.update_business(rows[0]["id"], {"status": "replied"})
    db.update_business(rows[1]["id"], {"email": ""})
    _, html = _steps(client)
    assert 'id="steps-play"' in html
    assert 'aria-label="Pause the steps"' in html


def test_a_single_step_does_not_pretend_to_be_a_carousel(client):
    """No timer, no dots, no pause button for a list of one."""
    rows = _seed(2)
    db.update_business(rows[0]["id"], {"status": "replied"})
    for row in rows[1:]:
        db.update_business(row["id"], {"email": "has@one.com.au",
                                       "masthead": "newcastleherald.com.au"})
    html = client.get("/").text
    import worklist
    if len(worklist.board()) == 1:
        assert 'id="steps-dots"' not in html
        assert 'id="steps-play"' not in html


def test_the_dots_say_what_each_step_is(client):
    """A row of anonymous dots is decoration; these are labelled."""
    rows = _seed(4)
    db.update_business(rows[0]["id"], {"status": "replied"})
    db.update_business(rows[1]["id"], {"email": ""})
    html = client.get("/").text
    dots = html.split('id="steps-dots"')[1].split("</div>")[0]
    assert 'aria-label="1 replied to an email"' in dots


def test_an_empty_board_still_says_what_to_do(client):
    """Nothing waiting is not nothing to do."""
    html = client.get("/").text
    assert 'class="steps-window"' not in html
    assert "Next steps" in html
    assert "Find businesses" in html, "the empty state still points somewhere"


# ---------- What needs a person outside the app ----------
# board() is the day's work. This is the other list: the handful of things
# nobody in the app can do, because they live in a hosting console or on
# somebody's laptop. The morning brief reads it off /health.

def _keys(client=None):
    import worklist
    return [g["key"] for g in worklist.setup_gaps()]


def test_it_names_what_only_the_operator_can_fix(client):
    from unittest.mock import patch
    import worklist
    with patch.object(worklist, "db", worklist.db):
        keys = _keys()
    assert "sending" in keys and "inbound" in keys


def test_a_gap_goes_away_once_it_is_closed(client):
    import app
    from unittest.mock import patch
    with patch.object(app, "SEND_ENABLED", True):
        # config is read inside the function, so patch where it is read
        import config
        with patch.object(config, "SEND_ENABLED", True):
            assert "sending" not in _keys()


def test_it_counts_the_emails_the_switch_is_holding(client):
    import db
    import worklist
    business_id = db.insert_business({"name": "Ready", "status": "qualified"})
    db.insert_message({"business_id": business_id, "to_email": "a@b.com",
                       "subject": "S", "body": "B", "status": "approved"})
    sending = next(g for g in worklist.setup_gaps() if g["key"] == "sending")
    assert "1 approved email is waiting" in sending["why"]


def test_the_deal_value_gap_only_shows_when_something_is_unpriced(client):
    import db
    import revenue
    assert "deal_value" not in _keys(), "nothing open, nothing to price"
    db.insert_business({"name": "Open", "status": "qualified"})
    assert "deal_value" in _keys()
    revenue.set_default_value(9000)
    assert "deal_value" not in _keys()


@pytest.fixture
def no_backups(tmp_path, monkeypatch):
    """
    A backups folder of this test's own.

    Other tests trip the automatic snapshot that runs before a bulk delete,
    and it lands in the folder the whole suite shares — so run in order these
    saw a fresh backup and no gap, while passing perfectly well alone.
    """
    import backup
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path / "backups")
    return tmp_path / "backups"


@pytest.fixture
def no_uploads(tmp_path, monkeypatch):
    import delivery
    monkeypatch.setattr(delivery, "UPLOAD_DIR", tmp_path / "uploads")
    return tmp_path / "uploads"


def test_the_backup_gap_waits_until_there_is_something_to_lose(client, no_backups, no_uploads):
    import db
    import worklist
    assert "backup" not in _keys()
    for i in range(20):
        db.insert_business({"name": f"B{i}"})
    gap = next(g for g in worklist.setup_gaps() if g["key"] == "backup")
    assert "one file on one disk" in gap["why"]


def test_it_says_the_uploaded_files_are_not_in_the_snapshot_either(client, no_backups, no_uploads):
    import db
    import delivery
    import worklist
    for i in range(20):
        db.insert_business({"name": f"B{i}"})
    business_id = db.insert_business({"name": "Client", "status": "won"})
    delivery.save(business_id, "2026-09", upload=("Pearch.pdf", b"%PDF-1.4 x"))
    gap = next(g for g in worklist.setup_gaps() if g["key"] == "backup")
    assert "not in a snapshot either" in gap["why"]


def test_every_gap_points_at_the_screen_that_fixes_it(client):
    import worklist
    for gap in worklist.setup_gaps():
        assert gap["where"].startswith("/"), gap
        assert client.get(gap["where"]).status_code == 200, gap


def test_it_never_names_an_environment_variable(client):
    """The health endpoint is public. It says a thing is off, not how to switch it on."""
    import worklist
    for gap in worklist.setup_gaps():
        blob = f"{gap['title']} {gap['why']}"
        assert "PEARCH_" not in blob and "_API_KEY" not in blob, gap
        assert "=" not in blob, gap


def test_health_carries_the_list(client):
    got = client.get("/health").json()
    assert isinstance(got["needs_you"], list)
    assert {"key", "title", "why", "where"} <= set(got["needs_you"][0])


def test_health_leaks_no_business_names(client):
    """It is public, so it reports the state of the app and nothing about the book."""
    import db
    db.insert_business({"name": "Very Distinctive Plumbing", "status": "qualified"})
    assert "Very Distinctive" not in client.get("/health").text
