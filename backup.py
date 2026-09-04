"""
Snapshots of the database.

Everything the app knows lives in one SQLite file on one disk: every
business, every decision, every draft and — once sending is live — the
record of what was sent to whom, which is the part that cannot be
reconstructed by prospecting again.

Two different risks, and they need different answers. A snapshot on the same
disk protects against a mistake: a bad purge, a wrong import, a migration
that went sideways. It does nothing about the disk itself going away — only
a copy somewhere else does that, which is what the download is for. The page
says so rather than letting a list of snapshots imply a safety it does not
provide.

Snapshots use SQLite's own backup API rather than copying the file, because
a plain copy of a database in WAL mode can catch it mid-write and produce
something that looks fine until the day it is needed.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from config import DB_PATH

log = logging.getLogger(__name__)

BACKUP_DIR = Path(DB_PATH).parent / "backups"
KEEP = 14                       # a fortnight of daily snapshots
NAME_RE = re.compile(r"^acm-outreach-(\d{8}-\d{6})(?:-([a-z0-9-]+))?\.db$")


def _slug(reason: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (reason or "").lower()).strip("-")[:32]


def make(reason: str = "manual") -> dict[str, Any]:
    """Take a snapshot now. Returns its name, size and time."""
    BACKUP_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    slug = _slug(reason)
    path = BACKUP_DIR / f"acm-outreach-{stamp}{'-' + slug if slug else ''}.db"

    source = sqlite3.connect(str(DB_PATH))
    try:
        target = sqlite3.connect(str(path))
        try:
            # SQLite's own backup: consistent even while the app is writing.
            source.backup(target)
        finally:
            target.close()
    finally:
        source.close()

    prune()
    log.info("backup written: %s (%s bytes)", path.name, path.stat().st_size)
    return _describe(path)


def _describe(path: Path) -> dict[str, Any]:
    stat = path.stat()
    match = NAME_RE.match(path.name)
    when = ""
    if match:
        when = datetime.strptime(match.group(1), "%Y%m%d-%H%M%S").strftime("%Y-%m-%d %H:%M")
    return {
        "name": path.name,
        "reason": (match.group(2) or "").replace("-", " ") if match else "",
        "taken_at": when,
        "bytes": stat.st_size,
        "size": f"{stat.st_size / 1_000_000:.1f} MB" if stat.st_size >= 100_000
                else f"{stat.st_size / 1_000:.0f} KB",
    }


def listing() -> list[dict[str, Any]]:
    """Every snapshot on disk, newest first."""
    if not BACKUP_DIR.exists():
        return []
    files = [p for p in BACKUP_DIR.iterdir() if NAME_RE.match(p.name)]
    files.sort(key=lambda p: p.name, reverse=True)
    return [_describe(p) for p in files]


def prune(keep: int | None = None) -> int:
    """
    Drop the oldest snapshots past the keep count. Returns how many went.

    KEEP is read here rather than used as a default argument, because a
    default is bound once at import — which quietly makes the setting
    unchangeable at runtime.
    """
    keep = KEEP if keep is None else keep
    files = [p for p in BACKUP_DIR.iterdir() if NAME_RE.match(p.name)] \
        if BACKUP_DIR.exists() else []
    files.sort(key=lambda p: p.name, reverse=True)
    removed = 0
    for path in files[keep:]:
        path.unlink(missing_ok=True)
        removed += 1
    return removed


def resolve(name: str) -> Path | None:
    """
    The path for a snapshot name, or None.

    The name is matched against the pattern rather than joined onto the
    directory, so nothing from a URL can walk out of it.
    """
    if not NAME_RE.match(name or ""):
        return None
    path = BACKUP_DIR / name
    return path if path.is_file() else None


def latest() -> dict[str, Any] | None:
    found = listing()
    return found[0] if found else None


def age_in_days() -> float | None:
    """How long since the last snapshot, or None if there has never been one."""
    newest = latest()
    if not newest or not newest["taken_at"]:
        return None
    taken = datetime.strptime(newest["taken_at"], "%Y-%m-%d %H:%M").replace(tzinfo=timezone.utc)
    return (datetime.now(timezone.utc) - taken).total_seconds() / 86400


def start_daily(interval_hours: float = 24.0) -> None:
    """
    Take a snapshot on startup and once a day after that.

    A background thread rather than a cron job, because this runs as a single
    web service with nowhere to hang one. It is a daemon and it swallows its
    own errors: a backup that fails must never be the reason the app stops
    serving.
    """
    def loop() -> None:
        while True:
            try:
                if (age_in_days() or 999) >= interval_hours / 24:
                    make("daily")
            except Exception:
                log.exception("scheduled backup failed")
            time.sleep(interval_hours * 3600)

    threading.Thread(target=loop, name="backup", daemon=True).start()
