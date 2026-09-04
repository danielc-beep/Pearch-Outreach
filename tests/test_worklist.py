"""
The work board, and the screen for addresses the scraper could not find.

The board answers the only question you have on opening the app: what do I
do next. So the rules that matter are which jobs appear, which are hidden,
and that every one of them links somewhere that starts the work.
"""
from __future__ import annotations

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
    assert "What's" in html and "waiting" in html
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
