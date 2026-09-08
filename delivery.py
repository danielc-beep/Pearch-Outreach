"""
What a client actually gets: the content, and the report on it each month.

The app could tell you a client had signed and that nobody had set their
go-live date. It could not tell you why, whose job it was, or how close it
was — so "signed but not live" was a complaint rather than a piece of work.
This is the work: a brief becomes a draft, a draft goes to an editor, an
editor publishes it, and publishing it starts the twelve months. The go-live
date sets itself from the day it published, which is the only day it should
ever have been.

Then, every month, a rep puts the Pearch report on the client's record. That
is the evidence the whole thing rests on. A renewal conversation with nothing
on file but an invoice is a hard conversation; one with twelve months of
reports behind it is a different one.

Reps are a name typed in a box, not accounts. The app has one shared login,
so a name is an honest record of who did the work and a login would be a
claim the app cannot back up.
"""
from __future__ import annotations

import logging
import re
import unicodedata
import uuid
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import db
import renewals
from config import DB_PATH

log = logging.getLogger(__name__)

# Where the content is up to. Ordered, because it is a pipeline: the page
# shades it along one ramp rather than giving each step an unrelated colour.
STAGES = [
    {"key": "brief", "label": "Brief", "blurb": "Agreed with the client, not written yet."},
    {"key": "writing", "label": "Writing", "blurb": "With whoever is writing it."},
    {"key": "with_editor", "label": "With the desk", "blurb": "Filed, waiting on the masthead."},
    {"key": "published", "label": "Published", "blurb": "Live. The twelve months runs from here."},
]
BY_STAGE = {s["key"]: s for s in STAGES}
ORDER = [s["key"] for s in STAGES]

# How long a piece can sit at one stage before somebody should chase it.
STALE_AFTER = {"brief": 7, "writing": 10, "with_editor": 7, "published": 0}

# Uploads. A cap because the disk is small and shared with the database, and
# an allowlist because a file this app hands back to a browser should never be
# something a browser will run.
UPLOAD_DIR = Path(DB_PATH).parent / "uploads"
MAX_UPLOAD = 20 * 1024 * 1024
ALLOWED = {
    ".pdf": "application/pdf",
    ".png": "image/png",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".csv": "text/csv",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


# ---------- The content ----------

def _age_days(stamp: str | None) -> int:
    if not stamp:
        return 0
    try:
        when = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
    except ValueError:
        return 0
    if when.tzinfo is None:
        when = when.replace(tzinfo=timezone.utc)
    return max(0, (datetime.now(timezone.utc) - when).days)


def _decorate(piece: dict[str, Any]) -> dict[str, Any]:
    stage = BY_STAGE.get(piece["stage"], BY_STAGE["brief"])
    days = _age_days(piece.get("updated_at"))
    limit = STALE_AFTER.get(piece["stage"], 0)
    return {**piece, "stage_label": stage["label"], "days": days,
            "stale": bool(limit and days >= limit),
            "next": ORDER[ORDER.index(piece["stage"]) + 1]
                    if piece["stage"] in ORDER[:-1] else "",
            "next_label": BY_STAGE[ORDER[ORDER.index(piece["stage"]) + 1]]["label"]
                          if piece["stage"] in ORDER[:-1] else ""}


def pieces_for(business_id: int) -> list[dict[str, Any]]:
    return [_decorate(p) for p in db.content_for(business_id)]


def start(business_id: int, angle: str = "", owner: str = "",
          notes: str = "") -> dict[str, Any] | None:
    """Open a brief for a client. The first thing that happens after they sign."""
    if not db.get_business(business_id):
        return None
    content_id = db.add_content(business_id, {
        "angle": angle.strip()[:400], "owner": owner.strip()[:80],
        "notes": notes.strip()[:2000], "stage": "brief"})
    db.log_activity(business_id, "content", f"Brief opened{f' — {angle.strip()[:120]}' if angle.strip() else ''}")
    return _decorate(db.get_content(content_id))


def move(content_id: int, stage: str, url: str = "", when: str = "") -> dict[str, Any] | None:
    """
    Advance a piece. Publishing it starts the client's twelve months.

    That is the point of the whole thing: the term should run from the day the
    content went live, and until now somebody had to remember to type that
    date in a second place. Publishing sets it, and only if it is not already
    set — nobody's renewal date should move because a URL was corrected.
    """
    piece = db.get_content(content_id)
    if not piece:
        return None
    if stage not in BY_STAGE:
        raise ValueError(f"There is no stage called {stage}.")
    url = (url or "").strip()[:500]
    if stage == "published" and not url:
        raise ValueError("A published piece needs the link it published at.")

    fields: dict[str, Any] = {"stage": stage}
    if url:
        fields["url"] = url
    business_id = int(piece["business_id"])

    if stage == "published":
        day = (when or "")[:10] or date.today().isoformat()
        fields["published_at"] = day
        db.update_content(content_id, fields)
        business = db.get_business(business_id)
        if business and not business.get("live_at"):
            renewals.go_live(business_id, day)
            db.log_activity(business_id, "content",
                            f"Published {day} — the twelve months starts here")
        else:
            db.log_activity(business_id, "content", f"Published {day}")
        return _decorate(db.get_content(content_id))

    db.update_content(content_id, fields)
    db.log_activity(business_id, "content", f"Moved to {BY_STAGE[stage]['label']}")
    return _decorate(db.get_content(content_id))


def board() -> dict[str, Any]:
    """Everything not yet published, and how long it has been sitting."""
    pieces = [_decorate(p) for p in db.content_in_flight()]
    pieces.sort(key=lambda p: (not p["stale"], ORDER.index(p["stage"])))
    return {
        "pieces": pieces,
        "stages": STAGES,
        "counts": {s["key"]: sum(1 for p in pieces if p["stage"] == s["key"]) for s in STAGES},
        "stale": sum(1 for p in pieces if p["stale"]),
    }


# ---------- The monthly report ----------

def this_period(today: date | None = None) -> str:
    today = today or datetime.now(timezone.utc).date()
    return f"{today.year:04d}-{today.month:02d}"


def period_label(period: str) -> str:
    try:
        year, month = period.split("-")
        return f"{['', 'January', 'February', 'March', 'April', 'May', 'June', 'July', 'August', 'September', 'October', 'November', 'December'][int(month)]} {year}"
    except (ValueError, IndexError):
        return period


def due(period: str = "", today: date | None = None) -> dict[str, Any]:
    """
    Which clients are owed a report this month, and which already have one.

    Only clients whose content is live: a client whose piece has not published
    has nothing to report on yet, and putting them on a chase list would be
    asking somebody to write a report about nothing.
    """
    period = period or this_period(today)
    done = db.reports_in_period(period)
    rows = []
    for client in db.clients():
        business_id = int(client["id"])
        if not client.get("live_at"):
            continue
        report = done.get(business_id)
        rows.append({**client, "report": report, "has_report": bool(report),
                     "state": renewals.state_of(client, db.latest_contract(business_id),
                                                today)})
    rows.sort(key=lambda r: (r["has_report"], r["name"].lower()))
    return {
        "period": period, "label": period_label(period), "clients": rows,
        "done": sum(1 for r in rows if r["has_report"]),
        "outstanding": sum(1 for r in rows if not r["has_report"]),
        "total": len(rows),
    }


def count_outstanding(today: date | None = None) -> int:
    return due(today=today)["outstanding"]


# ---------- Files ----------

def _safe_name(name: str) -> str:
    """
    A filename fit to show a person, with nothing in it fit to run.

    Never used as the path on disk — that is a generated id — so this only has
    to be readable and free of anything that could travel.
    """
    name = unicodedata.normalize("NFKD", name or "").encode("ascii", "ignore").decode()
    name = re.sub(r"[^A-Za-z0-9._ -]", "", name).strip() or "report"
    return name[:120]


def store_file(business_id: int, period: str, filename: str, blob: bytes) -> dict[str, Any]:
    """
    Put a report file on the disk beside the database, under a generated name.

    The name the browser sent is kept for display and never used as a path: a
    filename is attacker-controlled, and one that walks out of the directory
    is the classic way to write anywhere on a disk.
    """
    if not blob:
        raise ValueError("That file was empty.")
    if len(blob) > MAX_UPLOAD:
        raise ValueError(f"Files are capped at {MAX_UPLOAD // (1024 * 1024)} MB.")
    shown = _safe_name(filename)
    suffix = Path(shown).suffix.lower()
    if suffix not in ALLOWED:
        raise ValueError("Reports can be a PDF, an image, a spreadsheet or a document.")

    folder = UPLOAD_DIR / str(int(business_id))
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{period}-{uuid.uuid4().hex}{suffix}"
    path.write_bytes(blob)
    return {"file_name": shown, "file_path": str(path), "file_bytes": len(blob)}


def resolve_file(report: dict[str, Any]) -> tuple[Path, str] | None:
    """
    The file on disk for a report, and what to serve it as.

    The stored path is checked against the uploads directory rather than
    trusted, so a row edited by hand cannot talk the app into reading
    something else off the disk.
    """
    stored = (report or {}).get("file_path")
    if not stored:
        return None
    path = Path(stored).resolve()
    root = UPLOAD_DIR.resolve()
    if root not in path.parents or not path.is_file():
        log.warning("report %s points outside the uploads directory", report.get("id"))
        return None
    return path, ALLOWED.get(path.suffix.lower(), "application/octet-stream")


def uploads_held() -> dict[str, Any]:
    """
    How many report files are on the disk, and how much room they take.

    The backups page prints it, because a snapshot is the database only: it
    records that a report exists and what it said, and does not contain the
    file. Saying so is the difference between a backup and the belief in one.
    """
    if not UPLOAD_DIR.exists():
        return {"count": 0, "bytes": 0, "size": "nothing"}
    files = [f for f in UPLOAD_DIR.rglob("*") if f.is_file()]
    total = sum(f.stat().st_size for f in files)
    size = (f"{total / 1_000_000:.1f} MB" if total >= 100_000
            else f"{total / 1_000:.0f} KB")
    return {"count": len(files), "bytes": total, "size": size}


def save(business_id: int, period: str, owner: str = "", citations: int | None = None,
         notes: str = "", upload: tuple[str, bytes] | None = None) -> dict[str, Any] | None:
    """Record this month's report, with the Pearch file if one came with it."""
    if not db.get_business(business_id):
        return None
    if not re.fullmatch(r"\d{4}-(0[1-9]|1[0-2])", period or ""):
        raise ValueError("A period looks like 2026-09.")
    data: dict[str, Any] = {"owner": owner.strip()[:80] or None,
                            "notes": notes.strip()[:4000] or None}
    if citations is not None:
        data["citations"] = max(0, int(citations))
    if upload:
        data.update(store_file(business_id, period, upload[0], upload[1]))
    report_id = db.save_report(business_id, period, data)
    db.log_activity(business_id, "report",
                    f"{period_label(period)} report filed"
                    + (f" — {data['file_name']}" if upload else ""))
    return db.get_report(report_id)
