"""
Six tabs, two of which have halves.

Nine across the top was more than anyone holds in their head, and four were
pairs — two about email, four about the records behind it. These pin the
shape, and pin that nothing that used to work stopped working.
"""
from __future__ import annotations

import db


def _nav(client):
    body = client.get("/").text
    block = body.split('<nav class="nav">')[1].split("</nav>")[0]
    return [line for line in block.splitlines() if "href=" in line]


def test_there_are_six_tabs_in_the_order_the_work_is_done(client):
    hrefs = [line.split('href="')[1].split('"')[0] for line in _nav(client)]
    assert hrefs == ["/", "/prospect", "/admin/review", "/crm",
                     "/emails/outbox", "/revenue"]


def test_emails_opens_on_the_outbox_with_the_inbox_beside_it(client):
    body = client.get("/emails/outbox").text
    assert 'href="/emails/outbox"' in body and 'href="/emails/inbox"' in body
    bar = body.split('class="subtabs"')[1].split("</nav>")[0]
    assert "Outbox" in bar and "Inbox" in bar


def test_the_inbox_carries_the_same_bar(client):
    bar = client.get("/emails/inbox").text.split('class="subtabs"')[1].split("</nav>")[0]
    assert 'aria-current="page"' in bar
    assert "Outbox" in bar and "Inbox" in bar


def test_admin_opens_on_review_with_its_four_sections(client):
    body = client.get("/admin/review").text
    for path in ("/admin/review", "/admin/align", "/admin/mastheads", "/admin/database"):
        assert f'href="{path}"' in body, path


def test_every_admin_page_carries_the_bar(client):
    for path in ("/admin/review", "/admin/align", "/admin/mastheads", "/admin/database"):
        body = client.get(path).text
        assert 'class="subtabs"' in body, path
        assert 'aria-current="page"' in body, path


def test_the_tab_you_are_on_is_the_one_marked(client):
    body = client.get("/admin/mastheads").text
    bar = body.split('class="subtabs"')[1].split("</nav>")[0]
    on = [line for line in bar.splitlines() if "is-on" in line]
    assert len(on) == 1 and "/admin/mastheads" in on[0]


def test_the_bar_says_how_much_is_waiting_behind_the_other_option(client):
    """A bar that only says where you are is a label, not navigation."""
    import replies
    replies.receive({"from": "stranger@nowhere.com.au", "subject": "Re: hi",
                     "text": "Who is this?"})
    bar = client.get("/emails/outbox").text.split('class="subtabs"')[1].split("</nav>")[0]
    assert "subtab-n" in bar, "the inbox count shows while you are in the outbox"


# ---------- Nothing that worked stopped working ----------

MOVED = {
    "/outbox": "/emails/outbox",
    "/replies": "/emails/inbox",
    "/review": "/admin/review",
    "/review/align": "/admin/align",
    "/align": "/admin/align",
    "/mastheads": "/admin/mastheads",
    "/businesses": "/admin/database",
}


def test_every_old_url_still_lands(client):
    for old, new in MOVED.items():
        got = client.get(old, follow_redirects=False)
        assert got.status_code == 308, old
        assert got.headers["location"] == new, old


def test_a_redirect_keeps_the_query_string(client):
    """A redirect that drops half the request is worse than a broken link."""
    got = client.get("/outbox?status=approved", follow_redirects=False)
    assert got.headers["location"] == "/emails/outbox?status=approved"
    got = client.get("/businesses?status=replied&q=acme", follow_redirects=False)
    assert got.headers["location"] == "/admin/database?status=replied&q=acme"


def test_the_two_entry_points_open_the_first_section(client):
    assert client.get("/emails", follow_redirects=False).headers["location"] \
        == "/emails/outbox"
    assert client.get("/admin", follow_redirects=False).headers["location"] \
        == "/admin/review"


def test_a_business_record_is_still_its_own_page(client):
    """The browse view moved under Admin; a record is a record."""
    business_id = db.insert_business({"name": "Acme", "suburb": "Newcastle"})
    assert client.get(f"/businesses/{business_id}").status_code == 200


def test_no_page_still_links_at_the_old_addresses(client):
    """A link left behind would work via a redirect and quietly cost a round trip."""
    import pathlib
    import re
    root = pathlib.Path(__file__).resolve().parent.parent
    stale = []
    # app.py is excluded on purpose: the old paths appear there as the
    # redirects themselves, which is the one place they should.
    for path in list((root / "templates").glob("*.html")) + \
            [root / "worklist.py", root / "coach.py"]:
        for hit in re.findall(r'["\'](/(?:outbox|replies|review|mastheads|businesses|align)'
                              r'(?:/align)?)["\'?#]', path.read_text()):
            stale.append(f"{path.name}: {hit}")
    assert not stale, stale
