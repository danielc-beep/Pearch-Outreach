"""
Backups and undo.

Everything the app knows is one SQLite file on one disk, and three buttons
delete permanently. These cover the two ways back: a snapshot of the file,
and a copy of what a delete took with it.
"""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

import backup
import db
import outreach
import prospect


@pytest.fixture
def backups_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(backup, "BACKUP_DIR", tmp_path / "backups")
    return tmp_path / "backups"


def _seed(n=4):
    prospect.run("sample", {"industry": "dentist", "location": "Bendigo VIC", "limit": n},
                 enrich=False)
    return db.list_businesses(limit=99)[0]


# ---------- Snapshots ----------

def test_a_snapshot_is_a_working_database(client, backups_dir):
    rows = _seed(4)
    info = backup.make("test")
    path = backups_dir / info["name"]
    assert path.is_file()

    # Not just bytes on disk — it must open and hold the same records.
    conn = sqlite3.connect(str(path))
    try:
        count = conn.execute("SELECT COUNT(*) FROM businesses").fetchone()[0]
        names = {r[0] for r in conn.execute("SELECT name FROM businesses")}
    finally:
        conn.close()
    assert count == len(rows)
    assert rows[0]["name"] in names


def test_snapshots_are_pruned_to_the_keep_count(client, backups_dir, monkeypatch):
    _seed(2)
    monkeypatch.setattr(backup, "KEEP", 3)
    for i in range(6):
        # The name carries a timestamp to the second, so vary the reason.
        backup.make(f"run-{i}")
    assert len(backup.listing()) <= 3


def test_the_listing_is_newest_first(client, backups_dir):
    _seed(2)
    backup.make("older")
    backup.make("newer")
    names = [b["name"] for b in backup.listing()]
    assert names == sorted(names, reverse=True)


def test_a_backup_name_cannot_walk_out_of_its_directory(backups_dir):
    for bad in ("../../etc/passwd", "..%2Fsecret.db", "/etc/passwd",
                "acm-outreach-x.db", "nope"):
        assert backup.resolve(bad) is None, bad


def test_the_download_route_refuses_a_made_up_name(client):
    assert client.get("/api/backup/download/../../etc/passwd").status_code in (404, 400)
    assert client.get("/api/backup/download/acm-outreach-99999999-999999.db").status_code == 404


def test_the_backup_route_and_page(client, backups_dir):
    _seed(3)
    made = client.post("/api/backup/now").json()
    assert made["name"].startswith("acm-outreach-")
    page = client.get("/backups").text
    assert made["taken_at"] in page
    assert "not an off-site backup" in page          # the limitation is stated


# ---------- Undo ----------

def test_deleting_one_business_keeps_a_copy(client):
    rows = _seed(3)
    business_id = rows[0]["id"]
    batch = db.delete_business(business_id)
    assert db.get_business(business_id) is None
    assert batch
    assert any(t["batch"] == batch for t in db.trash_batches())


def test_a_restore_brings_back_the_whole_record(client):
    """
    Contacts, messages and history all cascade from a business, so a restore
    that returns the row alone gives back something that looks complete and
    is not.
    """
    rows = _seed(3)
    business_id = rows[0]["id"]
    db.add_contact(business_id, {"email": "sarah@firm.com.au", "first_name": "Sarah"})
    message = outreach.draft_message(business_id, use_ai=False)
    db.log_activity(business_id, "note", "spoke to them at a function")

    batch = db.delete_business(business_id)
    assert db.get_message(int(message["id"])) is None      # cascaded away

    assert db.restore_batch(batch) == 1
    restored = db.get_business(business_id)
    assert restored["name"] == rows[0]["name"]
    assert db.list_contacts(business_id)[0]["email"] == "sarah@firm.com.au"
    assert db.get_message(int(message["id"]))["subject"] == message["subject"]
    assert any("function" in a["detail"] for a in db.list_activities(business_id))


def test_a_bulk_purge_is_one_undo(client):
    _seed(6)
    total = db.list_businesses(limit=99)[1]
    removed, batch = db.delete_sample_businesses()
    assert removed == total
    assert db.list_businesses(limit=99)[1] == 0

    assert db.restore_batch(batch) == removed
    assert db.list_businesses(limit=99)[1] == total


def test_the_rating_purge_is_undoable_too(client):
    for rating in (4.8, 3.2, 2.9):
        db.upsert_business({"name": f"Business {rating}", "suburb": "Newcastle",
                            "rating": rating, "review_count": 20, "source": "google_places",
                            "domain": f"b{rating}.com.au"})
    removed, batch = db.delete_below_rating(4.0)
    assert removed == 2
    assert db.restore_batch(batch) == 2
    assert db.list_businesses(limit=99)[1] == 3


def test_restoring_twice_does_not_duplicate(client):
    rows = _seed(2)
    batch = db.delete_business(rows[0]["id"])
    assert db.restore_batch(batch) == 1
    assert db.restore_batch(batch) == 0          # the trash entry is gone
    assert db.list_businesses(limit=99)[1] == 2


def test_a_restore_will_not_overwrite_a_live_record(client):
    """
    An id that has since been reused belongs to whoever holds it now.
    Restoring over it would replace a real record with an old one.
    """
    rows = _seed(2)
    business_id = rows[0]["id"]
    batch = db.delete_business(business_id)
    # Something else takes that id back.
    db.get_conn().execute(
        "INSERT INTO businesses (id, name, created_at, updated_at) "
        "VALUES (?, 'Someone Else', ?, ?)",
        (business_id, db.now(), db.now()))
    db.get_conn().commit()

    assert db.restore_batch(batch) == 0
    assert db.get_business(business_id)["name"] == "Someone Else"


def test_the_restore_route(client):
    rows = _seed(3)
    batch = db.delete_business(rows[0]["id"])
    body = client.post(f"/api/trash/restore/{batch}").json()
    assert body["restored"] == 1
    assert body["total"] == 3
    # An unknown batch is simply nothing to restore.
    assert client.post("/api/trash/restore/nosuchbatch").json()["restored"] == 0


def test_a_purge_takes_a_snapshot_first(client, backups_dir):
    _seed(4)
    assert backup.listing() == []
    client.post("/api/sample/purge")
    reasons = [b["reason"] for b in backup.listing()]
    assert any("sample purge" in r for r in reasons), reasons


def test_the_purge_route_hands_back_an_undo(client):
    _seed(3)
    body = client.post("/api/sample/purge").json()
    assert body["removed"] == 3
    assert body["batch"]
    assert client.post(f"/api/trash/restore/{body['batch']}").json()["restored"] == 3


def test_old_trash_is_cleared(client):
    rows = _seed(2)
    db.delete_business(rows[0]["id"])
    db.get_conn().execute("UPDATE trash SET deleted_at = '2020-01-01T00:00:00'")
    db.get_conn().commit()
    assert db.empty_trash(older_than_days=30) == 1
    assert db.trash_batches() == []
