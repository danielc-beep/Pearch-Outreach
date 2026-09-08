"""
The content a client pays for, and the report on it each month.

Two things carry these. Publishing must start the term — that is the whole
reason the content pipeline exists rather than a note in a field. And an
uploaded file must never be something a browser will run, because a filename
is attacker-controlled and this app hands it back.
"""
from __future__ import annotations

from datetime import date

import pytest

import db
import delivery
import renewals


def _client(name="Acme Plumbing", **extra):
    return db.insert_business({"name": name, "status": "won", "deal_value": 9000,
                               "won_at": "2026-08-01", "suburb": "Newcastle",
                               "masthead": "newcastleherald.com.au", **extra})


# ---------- The content ----------

def test_the_stages_are_a_pipeline_in_order(client):
    assert [s["key"] for s in delivery.STAGES] == \
        ["brief", "writing", "with_editor", "published"]


def test_a_brief_opens_against_a_client(client):
    business_id = _client()
    piece = delivery.start(business_id, "How to pick a plumber", "Sam")
    assert piece["stage"] == "brief" and piece["owner"] == "Sam"
    assert delivery.pieces_for(business_id)[0]["angle"] == "How to pick a plumber"


def test_publishing_starts_the_twelve_months(client):
    """The whole reason this exists rather than a date typed in twice."""
    business_id = _client()
    assert db.get_business(business_id)["live_at"] is None
    piece = delivery.start(business_id, "An angle")
    delivery.move(piece["id"], "published",
                  url="https://newcastleherald.com.au/story/1", when="2026-09-01")
    assert db.get_business(business_id)["live_at"] == "2026-09-01"
    assert renewals.book()["clients"][0]["due"] == "2027-09-01"


def test_a_second_piece_does_not_move_the_renewal_date(client):
    """Nobody's renewal should shift because a URL was corrected."""
    business_id = _client()
    first = delivery.start(business_id, "First")
    delivery.move(first["id"], "published", url="https://x.com/1", when="2026-09-01")
    second = delivery.start(business_id, "Second")
    delivery.move(second["id"], "published", url="https://x.com/2", when="2026-11-20")
    assert db.get_business(business_id)["live_at"] == "2026-09-01"


def test_publishing_without_a_link_is_refused(client):
    """A published piece nobody can point at is not published."""
    business_id = _client()
    piece = delivery.start(business_id, "An angle")
    with pytest.raises(ValueError, match="link"):
        delivery.move(piece["id"], "published")


def test_a_stage_that_does_not_exist_is_refused(client):
    business_id = _client()
    piece = delivery.start(business_id, "An angle")
    with pytest.raises(ValueError):
        delivery.move(piece["id"], "somewhere-else")


def test_a_piece_that_has_sat_too_long_is_flagged(client):
    business_id = _client()
    piece = delivery.start(business_id, "An angle")
    db.update_content(piece["id"], {"stage": "with_editor"})
    with db.tx() as conn:
        conn.execute("UPDATE content SET updated_at = datetime('now', '-30 days')")
    board = delivery.board()
    assert board["stale"] == 1
    assert board["pieces"][0]["stale"] is True


def test_a_published_piece_leaves_the_board(client):
    business_id = _client()
    piece = delivery.start(business_id, "An angle")
    assert len(delivery.board()["pieces"]) == 1
    delivery.move(piece["id"], "published", url="https://x.com/1")
    assert delivery.board()["pieces"] == []


def test_the_dashboard_asks_for_the_ones_holding_a_term_up(client):
    business_id = _client()
    piece = delivery.start(business_id, "An angle")
    db.update_content(piece["id"], {"stage": "with_editor"})
    with db.tx() as conn:
        conn.execute("UPDATE content SET updated_at = datetime('now', '-30 days')")
    assert "pieces sitting too long" in client.get("/").text


# ---------- The monthly report ----------

def _live(name="Acme Plumbing"):
    business_id = _client(name)
    piece = delivery.start(business_id, "An angle")
    delivery.move(piece["id"], "published", url="https://x.com/1", when="2026-01-10")
    return business_id


def test_only_live_clients_are_owed_a_report(client):
    """Somebody whose content has not published has nothing to report on."""
    _client("Not live yet")
    _live("Live one")
    due = delivery.due("2026-09")
    assert [c["name"] for c in due["clients"]] == ["Live one"]
    assert due["outstanding"] == 1


def test_filing_a_report_clears_them_from_the_list(client):
    business_id = _live()
    delivery.save(business_id, "2026-09", owner="Sam", citations=3)
    due = delivery.due("2026-09")
    assert due["outstanding"] == 0 and due["done"] == 1


def test_a_report_is_one_per_client_per_month(client):
    """A corrected upload should replace, not sit beside the wrong one."""
    business_id = _live()
    delivery.save(business_id, "2026-09", citations=3)
    delivery.save(business_id, "2026-09", citations=5)
    reports = db.reports_for(business_id)
    assert len(reports) == 1 and reports[0]["citations"] == 5


def test_last_month_is_a_different_report(client):
    business_id = _live()
    delivery.save(business_id, "2026-08", citations=1)
    delivery.save(business_id, "2026-09", citations=4)
    assert len(db.reports_for(business_id)) == 2
    assert delivery.due("2026-07")["outstanding"] == 1


def test_a_nonsense_period_is_refused(client):
    business_id = _live()
    for bad in ("2026", "2026-13", "September", ""):
        with pytest.raises(ValueError):
            delivery.save(business_id, bad)


def test_the_dashboard_counts_the_reports_still_to_file(client):
    _live()
    assert "clients owed a report" in client.get("/").text


# ---------- The file ----------

def test_a_pearch_report_is_stored_and_handed_back(client):
    business_id = _live()
    report = delivery.save(business_id, "2026-09", upload=("Pearch September.pdf", b"%PDF-1.4 x"))
    assert report["file_name"] == "Pearch September.pdf"
    found = delivery.resolve_file(report)
    assert found and found[0].read_bytes() == b"%PDF-1.4 x"
    assert found[1] == "application/pdf"


def test_the_name_the_browser_sent_is_never_the_path_on_disk(client):
    """A filename is attacker-controlled; one that walks out of the folder writes anywhere."""
    business_id = _live()
    report = delivery.save(business_id, "2026-09",
                           upload=("../../../etc/passwd.pdf", b"%PDF x"))
    assert ".." not in report["file_path"]
    assert str(delivery.UPLOAD_DIR) in report["file_path"]
    assert delivery.resolve_file(report)[0].read_bytes() == b"%PDF x"


def test_a_file_a_browser_would_run_is_refused(client):
    business_id = _live()
    for name in ("evil.html", "evil.svg", "evil.js", "evil.exe", "noextension"):
        with pytest.raises(ValueError, match="PDF"):
            delivery.save(business_id, "2026-09", upload=(name, b"x"))


def test_an_oversized_file_is_refused(client):
    business_id = _live()
    with pytest.raises(ValueError, match="capped"):
        delivery.save(business_id, "2026-09",
                      upload=("big.pdf", b"x" * (delivery.MAX_UPLOAD + 1)))


def test_a_row_pointing_outside_the_uploads_folder_serves_nothing(client):
    """A path is checked against the folder rather than trusted."""
    assert delivery.resolve_file({"id": 1, "file_path": "/etc/passwd"}) is None
    assert delivery.resolve_file({"id": 1, "file_path": ""}) is None


def test_the_download_route_serves_it_as_an_attachment(client):
    business_id = _live()
    report = delivery.save(business_id, "2026-09", upload=("Pearch.pdf", b"%PDF-1.4 x"))
    got = client.get(f"/api/reports/{report['id']}/file")
    assert got.status_code == 200
    assert got.content == b"%PDF-1.4 x"
    assert "attachment" in got.headers["content-disposition"]
    assert got.headers["x-content-type-options"] == "nosniff"


def test_the_upload_endpoint_takes_a_file(client):
    business_id = _live()
    got = client.post(f"/api/reports/{business_id}",
                      data={"period": "2026-09", "owner": "Sam", "citations": "4",
                            "notes": "Cited twice"},
                      files={"file": ("Pearch.pdf", b"%PDF-1.4 x", "application/pdf")})
    assert got.status_code == 200
    assert got.json()["citations"] == 4
    assert db.reports_for(business_id)[0]["file_name"] == "Pearch.pdf"


def test_the_upload_endpoint_reports_a_bad_file_rather_than_crashing(client):
    business_id = _live()
    got = client.post(f"/api/reports/{business_id}", data={"period": "2026-09"},
                      files={"file": ("evil.svg", b"<svg onload=alert(1)>", "image/svg+xml")})
    assert got.status_code == 400
    assert "PDF" in got.json()["detail"]


def test_a_report_can_be_filed_with_no_file_at_all(client):
    business_id = _live()
    got = client.post(f"/api/reports/{business_id}",
                      data={"period": "2026-09", "notes": "Nothing to attach"})
    assert got.status_code == 200
    assert got.json()["file_name"] is None


# ---------- On the page ----------

def test_the_delivery_page_shows_both_halves(client):
    business_id = _live()
    delivery.start(business_id, "Another angle", "Sam")
    body = client.get("/revenue/delivery").text
    assert "What is still to publish" in body
    assert "Another angle" in body
    assert "Acme Plumbing" in body
    assert "undefined" not in body


def test_a_client_page_carries_the_brief_and_the_report_forms(client):
    business_id = _live()
    body = client.get(f"/businesses/{business_id}").text
    assert 'id="brief-form"' in body and 'id="report-form"' in body


def test_a_prospect_page_carries_neither(client):
    """They are not a client, so there is nothing to deliver or report on."""
    business_id = db.insert_business({"name": "Just a prospect", "status": "new"})
    body = client.get(f"/businesses/{business_id}").text
    assert 'id="brief-form"' not in body and 'id="report-form"' not in body


def test_the_backups_page_says_the_files_are_not_in_the_snapshot(client):
    """
    A snapshot is the database only.

    It records that a report exists and what it said, and does not contain the
    file — which is the difference between a backup and the belief in one.
    """
    business_id = _live()
    delivery.save(business_id, "2026-09", upload=("Pearch.pdf", b"%PDF-1.4 x"))
    body = client.get("/backups").text
    assert "does not contain the file" in body
    assert str(delivery.UPLOAD_DIR) in body
