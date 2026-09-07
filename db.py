"""
SQLite storage for the outreach database.

Plain sqlite3 rather than an ORM: the schema is small, the queries are
readable as SQL, and there is nothing to install. Every connection is opened
with row_factory=sqlite3.Row so callers get dict-like rows.

The schema is created and migrated on import via init_db(); adding a column
means adding it to SCHEMA and to MIGRATIONS.
"""
from __future__ import annotations

import json
import secrets
import re
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from config import DB_PATH, MIN_PROSPECT_RATING

_local = threading.local()

SCHEMA = """
CREATE TABLE IF NOT EXISTS businesses (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at        TEXT NOT NULL,
    updated_at        TEXT NOT NULL,
    name              TEXT NOT NULL,
    legal_name        TEXT,
    abn               TEXT,
    website           TEXT,
    domain            TEXT,
    email             TEXT,
    phone             TEXT,
    address           TEXT,
    suburb            TEXT,
    state             TEXT,
    postcode          TEXT,
    region            TEXT,
    country           TEXT DEFAULT 'AU',
    industry          TEXT,
    category          TEXT,
    size_band         TEXT,
    rating            REAL,
    review_count      INTEGER,
    linkedin          TEXT,
    facebook          TEXT,
    instagram         TEXT,
    description       TEXT,
    source            TEXT NOT NULL DEFAULT 'manual',
    source_ref        TEXT,
    status            TEXT NOT NULL DEFAULT 'new',
    fit_score         INTEGER NOT NULL DEFAULT 0,
    score_reasons     TEXT,
    notes             TEXT,
    website_status    TEXT,
    masthead          TEXT,
    contact_url       TEXT,
    do_not_contact    INTEGER NOT NULL DEFAULT 0,
    last_contacted_at TEXT,
    enriched_at       TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_businesses_domain ON businesses(domain) WHERE domain IS NOT NULL AND domain != '';
CREATE INDEX IF NOT EXISTS idx_businesses_status ON businesses(status);
CREATE INDEX IF NOT EXISTS idx_businesses_region ON businesses(region);
CREATE INDEX IF NOT EXISTS idx_businesses_score  ON businesses(fit_score DESC);

CREATE TABLE IF NOT EXISTS contacts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id  INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    created_at   TEXT NOT NULL,
    first_name   TEXT,
    last_name    TEXT,
    role         TEXT,
    email        TEXT,
    phone        TEXT,
    linkedin     TEXT,
    is_primary   INTEGER NOT NULL DEFAULT 0,
    source       TEXT
);
CREATE INDEX IF NOT EXISTS idx_contacts_business ON contacts(business_id);

CREATE TABLE IF NOT EXISTS prospecting_runs (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    started_at    TEXT NOT NULL,
    finished_at   TEXT,
    source        TEXT NOT NULL,
    query         TEXT,
    status        TEXT NOT NULL DEFAULT 'running',
    found_count   INTEGER NOT NULL DEFAULT 0,
    new_count     INTEGER NOT NULL DEFAULT 0,
    dupe_count    INTEGER NOT NULL DEFAULT 0,
    result_ids    TEXT,
    error         TEXT
);

CREATE TABLE IF NOT EXISTS campaigns (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    name       TEXT NOT NULL,
    subject    TEXT NOT NULL,
    body       TEXT NOT NULL,
    status     TEXT NOT NULL DEFAULT 'draft'
);

CREATE TABLE IF NOT EXISTS messages (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at   TEXT NOT NULL,
    business_id  INTEGER NOT NULL REFERENCES businesses(id) ON DELETE CASCADE,
    contact_id   INTEGER REFERENCES contacts(id) ON DELETE SET NULL,
    campaign_id  INTEGER REFERENCES campaigns(id) ON DELETE SET NULL,
    to_email     TEXT NOT NULL,
    subject      TEXT NOT NULL,
    body         TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'draft',
    sent_at      TEXT,
    provider_id  TEXT,
    error        TEXT
);
CREATE INDEX IF NOT EXISTS idx_messages_business ON messages(business_id);
CREATE INDEX IF NOT EXISTS idx_messages_status ON messages(status);

CREATE TABLE IF NOT EXISTS activities (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at  TEXT NOT NULL,
    business_id INTEGER REFERENCES businesses(id) ON DELETE CASCADE,
    kind        TEXT NOT NULL,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_activities_business ON activities(business_id, id DESC);

CREATE TABLE IF NOT EXISTS suppressions (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    value      TEXT NOT NULL UNIQUE,   -- an email address or a bare domain
    reason     TEXT
);
"""

# Columns added after the first release. Each entry is applied if missing.
CREATE_TRASH = """
CREATE TABLE IF NOT EXISTS trash (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    deleted_at TEXT NOT NULL,
    batch      TEXT NOT NULL,
    reason     TEXT,
    name       TEXT,
    payload    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_trash_batch ON trash(batch);
"""


CREATE_MOVES = """
CREATE TABLE IF NOT EXISTS stage_moves (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    business_id INTEGER NOT NULL,
    from_stage  TEXT,
    to_stage    TEXT NOT NULL,
    moved_at    TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_moves_business ON stage_moves(business_id);
CREATE INDEX IF NOT EXISTS idx_moves_to ON stage_moves(to_stage);
"""


CREATE_METRICS = """
CREATE TABLE IF NOT EXISTS metrics_daily (
    day            TEXT PRIMARY KEY,
    businesses     INTEGER NOT NULL DEFAULT 0,
    with_email     INTEGER NOT NULL DEFAULT 0,
    sent           INTEGER NOT NULL DEFAULT 0,
    queue          INTEGER NOT NULL DEFAULT 0,
    pipeline_value REAL NOT NULL DEFAULT 0,
    won_value      REAL NOT NULL DEFAULT 0,
    updated_at     TEXT NOT NULL
);
"""


CREATE_SETTINGS = """
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


MIGRATIONS: list[tuple[str, str]] = [
    ("prospecting_runs", "ALTER TABLE prospecting_runs ADD COLUMN result_ids TEXT"),
    ("businesses", "ALTER TABLE businesses ADD COLUMN website_status TEXT"),
    ("businesses", "ALTER TABLE businesses ADD COLUMN masthead TEXT"),
    ("businesses", "ALTER TABLE businesses ADD COLUMN contact_url TEXT"),
    # What this one is worth. Null means nobody has said — which is not the
    # same as nothing, so the two are counted separately everywhere.
    ("businesses", "ALTER TABLE businesses ADD COLUMN deal_value REAL"),
    # When it was won, so revenue can be counted against a period rather than
    # only ever as a running total.
    ("businesses", "ALTER TABLE businesses ADD COLUMN won_at TEXT"),
]


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def get_conn() -> sqlite3.Connection:
    """One connection per thread — FastAPI's threadpool runs sync routes off-loop."""
    conn = getattr(_local, "conn", None)
    if conn is None:
        Path(DB_PATH).parent.mkdir(parents=True, exist_ok=True)
        conn = sqlite3.connect(DB_PATH, check_same_thread=False)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA busy_timeout = 5000")
        _local.conn = conn
    return conn


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    conn = get_conn()
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise


def init_db() -> None:
    conn = get_conn()
    conn.executescript(SCHEMA)
    conn.executescript(CREATE_TRASH)
    conn.executescript(CREATE_SETTINGS)
    conn.executescript(CREATE_METRICS)
    conn.executescript(CREATE_MOVES)
    existing = {
        table: {r["name"] for r in conn.execute(f"PRAGMA table_info({table})")}
        for table in ("businesses", "contacts", "campaigns", "messages", "prospecting_runs")
    }
    for table, ddl in MIGRATIONS:
        column = ddl.split("ADD COLUMN ")[1].split()[0]
        if column not in existing.get(table, set()):
            conn.execute(ddl)
    conn.commit()


def reset_db() -> None:
    """Drop everything and recreate. Used by the tests and `--reset`."""
    conn = get_conn()
    for table in ("activities", "messages", "campaigns", "prospecting_runs",
                  "contacts", "suppressions", "trash", "settings", "metrics_daily",
                  "stage_moves",
                  "businesses"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
    init_db()


# ---------- Settings ----------
# A small key/value table for the handful of things that have to be changeable
# from inside the app rather than from a hosting dashboard. Today that is the
# shared password: a setting nobody should have to file a ticket to change.

def get_setting(key: str, default: str = "") -> str:
    try:
        row = get_conn().execute(
            "SELECT value FROM settings WHERE key = ?", (key,)).fetchone()
    except sqlite3.Error:
        return default          # the table is not there yet; the caller has a fallback
    return row["value"] if row else default


def set_setting(key: str, value: str) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO settings (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "updated_at = excluded.updated_at",
            (key, value, now()))


def delete_setting(key: str) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM settings WHERE key = ?", (key,))


# ---------- Businesses ----------

BUSINESS_FIELDS = (
    "name", "legal_name", "abn", "website", "domain", "email", "phone",
    "address", "suburb", "state", "postcode", "region", "country",
    "industry", "category", "size_band", "rating", "review_count",
    "linkedin", "facebook", "instagram", "description",
    "source", "source_ref", "status", "fit_score", "score_reasons",
    "notes", "website_status", "masthead", "contact_url", "deal_value", "won_at",
    "do_not_contact", "last_contacted_at", "enriched_at",
)

STATUSES = [
    "new", "researching", "qualified", "contacted",
    "replied", "won", "lost", "disqualified",
]


def row_to_dict(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if row is None:
        return None
    d = dict(row)
    if isinstance(d.get("score_reasons"), str) and d["score_reasons"]:
        try:
            d["score_reasons"] = json.loads(d["score_reasons"])
        except json.JSONDecodeError:
            d["score_reasons"] = []
    else:
        d["score_reasons"] = d.get("score_reasons") or []
    return d


def _clean(data: dict[str, Any]) -> dict[str, Any]:
    out = {k: v for k, v in data.items() if k in BUSINESS_FIELDS}
    if isinstance(out.get("score_reasons"), (list, dict)):
        out["score_reasons"] = json.dumps(out["score_reasons"])
    if "do_not_contact" in out:
        out["do_not_contact"] = 1 if out["do_not_contact"] else 0
    return out


def find_duplicate(data: dict[str, Any]) -> dict[str, Any] | None:
    """
    Match on domain first (the strongest signal), then on name+postcode.
    Returns the existing row, or None.
    """
    conn = get_conn()
    domain = (data.get("domain") or "").strip().lower()
    if domain:
        row = conn.execute("SELECT * FROM businesses WHERE domain = ?", (domain,)).fetchone()
        if row:
            return row_to_dict(row)
    name = (data.get("name") or "").strip()
    postcode = (data.get("postcode") or "").strip()
    if name and postcode:
        row = conn.execute(
            "SELECT * FROM businesses WHERE lower(name) = lower(?) AND postcode = ?",
            (name, postcode),
        ).fetchone()
        if row:
            return row_to_dict(row)
    ref = (data.get("source_ref") or "").strip()
    if ref:
        row = conn.execute(
            "SELECT * FROM businesses WHERE source = ? AND source_ref = ?",
            (data.get("source", ""), ref),
        ).fetchone()
        if row:
            return row_to_dict(row)
    return None


def insert_business(data: dict[str, Any]) -> int:
    payload = _clean(data)
    payload.setdefault("status", "new")
    cols = list(payload)
    sql = (
        f"INSERT INTO businesses (created_at, updated_at, {', '.join(cols)}) "
        f"VALUES (?, ?, {', '.join('?' for _ in cols)})"
    )
    ts = now()
    with tx() as conn:
        cur = conn.execute(sql, [ts, ts, *[payload[c] for c in cols]])
        return int(cur.lastrowid)


def update_business(business_id: int, data: dict[str, Any]) -> None:
    payload = _clean(data)
    if not payload:
        return
    sets = ", ".join(f"{c} = ?" for c in payload)
    with tx() as conn:
        conn.execute(
            f"UPDATE businesses SET {sets}, updated_at = ? WHERE id = ?",
            [*payload.values(), now(), business_id],
        )


def upsert_business(data: dict[str, Any]) -> tuple[int, bool]:
    """
    Insert, or merge into an existing record. Returns (id, created).

    Merging never overwrites a filled field with an empty one — a thin result
    from one source can only ever add detail to what we already know.
    """
    existing = find_duplicate(data)
    if existing is None:
        return insert_business(data), True
    merged = {
        k: v for k, v in data.items()
        if k in BUSINESS_FIELDS
        and v not in (None, "", [])
        and not existing.get(k)
    }
    # Numeric signals are refreshed even when already set — they go stale.
    for k in ("rating", "review_count", "fit_score"):
        if data.get(k) not in (None, ""):
            merged[k] = data[k]
    if data.get("score_reasons"):
        merged["score_reasons"] = data["score_reasons"]
    if merged:
        update_business(int(existing["id"]), merged)
    return int(existing["id"]), False


def get_business(business_id: int) -> dict[str, Any] | None:
    row = get_conn().execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    return row_to_dict(row)


# Sample records are identifiable two ways: the source that created them, and
# the reserved domain the generator always uses. Both are checked, so a record
# whose source was overwritten by a later merge is still caught.
SAMPLE_CLAUSE = "(source = 'sample' OR domain LIKE '%.example.com.au')"


def count_sample_businesses() -> int:
    """How many fictional businesses are sitting in the database."""
    row = get_conn().execute(
        f"SELECT COUNT(*) AS n FROM businesses WHERE {SAMPLE_CLAUSE}"
    ).fetchone()
    return int(row["n"])


def delete_sample_businesses() -> tuple[int, str]:
    """Remove every fictional business. Returns (how many, the batch that undoes it)."""
    return _delete_where(SAMPLE_CLAUSE, (), "removed sample data")


BELOW_RATING_CLAUSE = "(rating IS NULL OR rating < ?)"


def count_below_rating(minimum: float) -> int:
    """How many stored businesses would not clear the rating floor."""
    row = get_conn().execute(
        f"SELECT COUNT(*) AS n FROM businesses WHERE {BELOW_RATING_CLAUSE}", (minimum,)
    ).fetchone()
    return int(row["n"])


def delete_below_rating(minimum: float) -> tuple[int, str]:
    """Remove every business under the rating floor. Returns (how many, batch id)."""
    return _delete_where(BELOW_RATING_CLAUSE, (minimum,),
                         f"removed businesses under {minimum:g} stars")


def _delete_where(clause: str, args: tuple[Any, ...], reason: str) -> tuple[int, str]:
    """
    Delete every business matching a clause, keeping a copy of each.

    Bulk deletes are where an accident is expensive: one click can take
    hundreds of records and everything attached to them. Each is captured
    first, under one batch id, so the whole action is a single undo.
    """
    ids = [int(r["id"]) for r in get_conn().execute(
        f"SELECT id FROM businesses WHERE {clause}", args)]
    if not ids:
        return 0, ""

    batch = _new_batch()
    snapshots = [s for s in (_capture(i) for i in ids) if s]
    with tx() as conn:
        for snapshot in snapshots:
            _to_trash(conn, snapshot, batch, reason)
        conn.execute(f"DELETE FROM businesses WHERE {clause}", args)
        conn.execute("DELETE FROM trash WHERE deleted_at < datetime('now', ?)",
                     (f"-{TRASH_KEEPS_DAYS} days",))
    return len(ids), batch


def businesses_without_masthead(limit: int = 500) -> list[dict[str, Any]]:
    """Records not yet aligned to a masthead — what a backfill has to work on."""
    rows = get_conn().execute(
        "SELECT * FROM businesses WHERE masthead IS NULL OR masthead = '' "
        "ORDER BY id LIMIT ?", (limit,)
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def count_without_masthead() -> int:
    row = get_conn().execute(
        "SELECT COUNT(*) AS n FROM businesses WHERE masthead IS NULL OR masthead = ''"
    ).fetchone()
    return int(row["n"])


# ---------- Where deals actually go ----------
# Every move between stages, as data rather than as a sentence in the
# activity log. The log was already writing "Moved from Contacted to Replied"
# and that is readable by a person and miserable to count; this is the same
# fact in a shape a query can use.

def record_move(business_id: int, from_stage: str, to_stage: str) -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO stage_moves (business_id, from_stage, to_stage, moved_at) "
            "VALUES (?, ?, ?, ?)", (business_id, from_stage or None, to_stage, now()))


def backfill_moves() -> int:
    """
    Recover the moves already written into the activity log.

    Our own sentences, in a shape we chose, so parsing them is reliable —
    but only for moves made through the app. A business imported straight
    into a stage never moved and never will appear here, which is why
    anything counted from this table has to say what it is counted from.
    """
    conn = get_conn()
    if conn.execute("SELECT 1 FROM stage_moves LIMIT 1").fetchone():
        return 0
    labels = {"new": "New", "researching": "Researching", "qualified": "Qualified",
              "contacted": "Contacted", "replied": "Replied", "won": "Won",
              "lost": "Lost", "disqualified": "Ruled out"}
    by_label = {v.lower(): k for k, v in labels.items()}
    pattern = re.compile(r"Moved from (.+?) to (.+?)\.")
    found = 0
    with tx() as c:
        for row in conn.execute(
                "SELECT business_id, detail, created_at FROM activities "
                "WHERE kind = 'stage' ORDER BY id"):
            match = pattern.search(row["detail"] or "")
            if not match:
                continue
            was = by_label.get(match.group(1).strip().lower())
            now_at = by_label.get(match.group(2).strip().lower())
            if not now_at:
                continue
            c.execute("INSERT INTO stage_moves (business_id, from_stage, to_stage, moved_at) "
                      "VALUES (?, ?, ?, ?)", (row["business_id"], was, now_at, row["created_at"]))
            found += 1
    return found


def reached(stage: str) -> list[int]:
    """Every business that has ever arrived at this stage."""
    rows = get_conn().execute(
        "SELECT DISTINCT business_id FROM stage_moves WHERE to_stage = ?", (stage,)).fetchall()
    return [int(r["business_id"]) for r in rows]


def stage_durations(stage: str) -> list[float]:
    """
    Days spent at a stage, for everything that has since left it.

    Anything still sitting there is excluded: counting an unfinished wait as
    if it were finished drags every average down and makes a stall look fast.
    """
    # The next move is the next row for that business, found by id rather than
    # by clock. Timestamps here are second-precision, so two moves inside one
    # second read as simultaneous and a comparison on time drops them both.
    rows = get_conn().execute(
        "SELECT m.business_id, m.moved_at, "
        "  (SELECT n.moved_at FROM stage_moves n "
        "   WHERE n.business_id = m.business_id AND n.id > m.id "
        "   ORDER BY n.id LIMIT 1) AS left_at "
        "FROM stage_moves m WHERE m.to_stage = ?", (stage,)).fetchall()
    out = []
    for row in rows:
        if not row["left_at"]:
            continue
        try:
            arrived = datetime.fromisoformat(row["moved_at"])
            gone = datetime.fromisoformat(row["left_at"])
        except ValueError:
            continue
        out.append(max(0.0, (gone - arrived).total_seconds() / 86400))
    return out


# ---------- Yesterday ----------
# A number on its own is a point. The same number with a fortnight behind it
# is a line, and a line answers the question the point cannot: is this going
# up. One row a day, rewritten as the day goes on, so the table stays the
# size of the calendar rather than the size of the traffic.

METRICS = ("businesses", "with_email", "sent", "queue", "pipeline_value", "won_value")


def record_today(values: dict[str, Any]) -> None:
    """Write today's figures, replacing whatever today already said."""
    day = now()[:10]
    columns = [m for m in METRICS if m in values]
    with tx() as conn:
        conn.execute(
            f"INSERT INTO metrics_daily (day, updated_at, {', '.join(columns)}) "
            f"VALUES (?, ?, {', '.join('?' for _ in columns)}) "
            f"ON CONFLICT(day) DO UPDATE SET updated_at = excluded.updated_at, "
            + ", ".join(f"{c} = excluded.{c}" for c in columns),
            [day, now(), *[values[c] for c in columns]])


def history(days: int = 14) -> list[dict[str, Any]]:
    """The last `days` daily rows, oldest first, for drawing."""
    rows = get_conn().execute(
        "SELECT * FROM metrics_daily ORDER BY day DESC LIMIT ?", (days,)).fetchall()
    return [dict(r) for r in reversed(rows)]


def trend(days: int = 14) -> dict[str, Any]:
    """
    Each metric as a series, with the change across the window.

    `change` compares the newest reading to the oldest one held. With fewer
    than two days recorded there is no change to report and it says so
    rather than reporting nought, which would read as "flat".
    """
    rows = history(days)
    out: dict[str, Any] = {"days": len(rows), "series": {}, "change": {}}
    for metric in METRICS:
        series = [float(r[metric] or 0) for r in rows]
        out["series"][metric] = series
        out["change"][metric] = (series[-1] - series[0]) if len(series) > 1 else None
    return out


def spark(series: list[float], width: float = 100.0, height: float = 22.0) -> str:
    """
    A polyline for a sparkline, in a 0-100 by 0-22 box.

    Flat series get a flat line down the middle rather than a divide by zero,
    and a single reading gets nothing — one point is not a trend.
    """
    if len(series) < 2:
        return ""
    low, high = min(series), max(series)
    span = (high - low) or 1.0
    step = width / (len(series) - 1)
    points = []
    for i, value in enumerate(series):
        y = height - ((value - low) / span) * height if high != low else height / 2
        points.append(f"{i * step:.1f},{y:.1f}")
    return " ".join(points)


# ---------- Money ----------
# Two numbers a sales manager asks for before any other: what is in the
# pipeline, and what has been won. Both are sums of deal_value, split by the
# stage the business is standing at.

OPEN_STAGES = ("qualified", "contacted", "replied")


def won_between(start: str, end: str) -> float:
    """
    Revenue won inside a window.

    Dated by when the deal was actually won. Records won before that date
    existed fall back to when they were last touched, which is the closest
    thing to the truth we have for them and is better than dropping them.
    """
    row = get_conn().execute(
        "SELECT COALESCE(SUM(deal_value), 0) AS total FROM businesses "
        "WHERE status = 'won' AND deal_value IS NOT NULL "
        "AND DATE(COALESCE(won_at, updated_at)) BETWEEN ? AND ?", (start, end)).fetchone()
    return float(row["total"] or 0.0)


def revenue(default_value: float = 0.0) -> dict[str, Any]:
    """
    Pipeline and closed-won, plus how much of it is guesswork.

    `default_value` fills in for businesses nobody has priced. It is reported
    separately rather than folded in silently: a pipeline number is worth
    having only if you know how much of it somebody actually agreed to.
    """
    conn = get_conn()
    marks = ", ".join("?" for _ in OPEN_STAGES)

    def totals(clause: str, args: list[Any]) -> dict[str, Any]:
        row = conn.execute(
            f"SELECT COUNT(*) AS n, "
            f"       SUM(CASE WHEN deal_value IS NOT NULL THEN 1 ELSE 0 END) AS priced, "
            f"       COALESCE(SUM(deal_value), 0) AS total "
            f"FROM businesses WHERE {clause}", args).fetchone()
        priced = int(row["priced"] or 0)
        unpriced = int(row["n"]) - priced
        return {"count": int(row["n"]), "priced": priced, "unpriced": unpriced,
                "set_total": float(row["total"] or 0.0),
                "total": float(row["total"] or 0.0) + unpriced * float(default_value)}

    return {"pipeline": totals(f"status IN ({marks})", list(OPEN_STAGES)),
            "won": totals("status = 'won'", []),
            "default_value": float(default_value)}


def masthead_counts() -> list[dict[str, Any]]:
    """How many prospects sit under each masthead, most first."""
    rows = get_conn().execute(
        "SELECT COALESCE(masthead, '') AS masthead, COUNT(*) AS n FROM businesses "
        "GROUP BY COALESCE(masthead, '') ORDER BY n DESC"
    ).fetchall()
    return [{"masthead": r["masthead"], "n": int(r["n"])} for r in rows]


# ---------- Trash: nothing is deleted without a way back ----------

TRASH_KEEPS_DAYS = 30


def _capture(business_id: int) -> dict[str, Any] | None:
    """
    Everything a delete would take with it.

    contacts, messages and activities all cascade from businesses, so
    capturing the row alone would restore a business with no history and no
    record of what was sent to it — which is worse than not restoring it,
    because it looks complete.
    """
    conn = get_conn()
    business = conn.execute("SELECT * FROM businesses WHERE id = ?", (business_id,)).fetchone()
    if not business:
        return None
    return {
        "business": dict(business),
        "contacts": [dict(r) for r in conn.execute(
            "SELECT * FROM contacts WHERE business_id = ?", (business_id,))],
        "messages": [dict(r) for r in conn.execute(
            "SELECT * FROM messages WHERE business_id = ?", (business_id,))],
        "activities": [dict(r) for r in conn.execute(
            "SELECT * FROM activities WHERE business_id = ?", (business_id,))],
    }


def _to_trash(conn: sqlite3.Connection, snapshot: dict[str, Any],
              batch: str, reason: str) -> None:
    conn.execute(
        "INSERT INTO trash (deleted_at, batch, reason, name, payload) VALUES (?, ?, ?, ?, ?)",
        (now(), batch, reason, snapshot["business"].get("name"), json.dumps(snapshot)),
    )


def _new_batch() -> str:
    """One id per user action, so a purge of forty is undone as one thing."""
    return now() + "-" + secrets.token_hex(4)


def list_trash(limit: int = 200) -> list[dict[str, Any]]:
    rows = get_conn().execute(
        "SELECT id, deleted_at, batch, reason, name FROM trash "
        "ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def trash_batches(limit: int = 40) -> list[dict[str, Any]]:
    """Recent deletions grouped by the action that caused them."""
    rows = get_conn().execute(
        "SELECT batch, reason, COUNT(*) AS n, MAX(deleted_at) AS deleted_at "
        "FROM trash GROUP BY batch ORDER BY deleted_at DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


def restore_batch(batch: str) -> int:
    """
    Put a deleted batch back, with its contacts, messages and history.

    Rows keep their original ids where those ids are still free, so links
    from anywhere else in the app still point at the right record.
    """
    rows = get_conn().execute(
        "SELECT id, payload FROM trash WHERE batch = ?", (batch,)).fetchall()
    if not rows:
        return 0

    restored = 0
    with tx() as conn:
        for row in rows:
            snapshot = json.loads(row["payload"])
            business = snapshot["business"]
            taken = conn.execute("SELECT 1 FROM businesses WHERE id = ?",
                                 (business["id"],)).fetchone()
            if taken:
                continue          # something already occupies that id; leave it be
            columns = ", ".join(business)
            marks = ", ".join("?" for _ in business)
            conn.execute(f"INSERT INTO businesses ({columns}) VALUES ({marks})",
                         list(business.values()))
            for table in ("contacts", "messages", "activities"):
                for record in snapshot.get(table, []):
                    cols = ", ".join(record)
                    qs = ", ".join("?" for _ in record)
                    conn.execute(f"INSERT OR IGNORE INTO {table} ({cols}) VALUES ({qs})",
                                 list(record.values()))
            conn.execute("DELETE FROM trash WHERE id = ?", (row["id"],))
            restored += 1
    return restored


def empty_trash(older_than_days: int = 0) -> int:
    """Clear the trash. With no argument, all of it."""
    with tx() as conn:
        if older_than_days:
            cur = conn.execute("DELETE FROM trash WHERE deleted_at < datetime('now', ?)",
                               (f"-{int(older_than_days)} days",))
        else:
            cur = conn.execute("DELETE FROM trash")
        return cur.rowcount or 0


def delete_business(business_id: int, reason: str = "deleted by hand") -> str:
    """Delete one business. Returns the batch id that undoes it."""
    batch = _new_batch()
    snapshot = _capture(business_id)
    with tx() as conn:
        if snapshot:
            _to_trash(conn, snapshot, batch, reason)
        conn.execute("DELETE FROM businesses WHERE id = ?", (business_id,))
        conn.execute("DELETE FROM trash WHERE deleted_at < datetime('now', ?)",
                     (f"-{TRASH_KEEPS_DAYS} days",))
    return batch


def list_businesses(
    q: str = "",
    status: str = "",
    region: str = "",
    state: str = "",
    industry: str = "",
    source: str = "",
    has_email: bool | None = None,
    has_website: bool | None = None,
    website_status: str = "",
    masthead: str = "",
    needs_review: bool = False,
    needs_draft: bool = False,
    min_score: int = 0,
    min_rating: float = 0.0,
    sort: str = "score",
    limit: int = 50,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    """Filtered, sorted page of businesses plus the total matching count."""
    where: list[str] = []
    args: list[Any] = []
    if q:
        where.append(
            "(name LIKE ? OR domain LIKE ? OR suburb LIKE ? OR industry LIKE ? "
            "OR category LIKE ? OR email LIKE ?)"
        )
        args += [f"%{q}%"] * 6
    for column, value in (("status", status), ("region", region), ("state", state),
                          ("source", source), ("website_status", website_status),
                          ("masthead", masthead if masthead != "none" else None)):
        if value:
            where.append(f"{column} = ?")
            args.append(value)
    if masthead == "none":
        # The filter's "Not aligned yet" option, so the records a backfill
        # could not place are findable rather than invisible. A masthead that
        # is not one of the 78 counts as not aligned — it reads as set, and
        # behaves as unset.
        import mastheads
        sites = list(mastheads.BY_SITE)
        where.append(f"(masthead IS NULL OR masthead NOT IN ({', '.join('?' for _ in sites)}))")
        args.extend(sites)
    if industry:
        where.append("(industry LIKE ? OR category LIKE ?)")
        args += [f"%{industry}%"] * 2
    if has_email is True:
        where.append("email IS NOT NULL AND email != ''")
    elif has_email is False:
        where.append("(email IS NULL OR email = '')")
    if has_website is True:
        where.append("website IS NOT NULL AND website != ''")
    elif has_website is False:
        where.append("(website IS NULL OR website = '')")
    if needs_review:
        # The review queue: everyone still awaiting a decision. Contactable,
        # not settled either way, and without a draft already approved or
        # sent — approving is what takes a business out of the queue.
        #
        # And at or above the rating floor. Discovery already filters on it,
        # but a CSV import does not, and rows that predate the floor are still
        # in the database — so the queue enforces it too rather than assuming
        # everything upstream did. An unrated business fails it: the email
        # opens by congratulating them on a number we would not have.
        if MIN_PROSPECT_RATING > 0:
            where.append("rating IS NOT NULL AND rating >= ?")
            args.append(MIN_PROSPECT_RATING)
        # And aligned to a masthead that exists. The email says which paper is
        # putting the content together, and that is the whole reason it lands.
        # Checking the field is merely non-empty is not enough: an imported
        # record carrying "theexaminer.com.au" — close, but not one of the 78
        # — passes that check and then falls back to the network, which is
        # exactly the weaker letter the rule exists to prevent.
        import mastheads
        sites = list(mastheads.BY_SITE)
        where.append(f"masthead IN ({', '.join('?' for _ in sites)})")
        args.extend(sites)
        where.append("email IS NOT NULL AND email != ''")
        where.append("do_not_contact = 0")
        where.append("status NOT IN ('contacted','replied','won','lost','disqualified')")
        where.append("id NOT IN (SELECT business_id FROM messages "
                     "WHERE status IN ('approved','sent'))")
    if needs_draft:
        # In the queue and with nothing written yet — what a bulk draft run
        # is for. Anything already drafted is left alone rather than getting
        # a second, near-identical email written for it.
        where.append("id NOT IN (SELECT business_id FROM messages)")
    if min_score:
        where.append("fit_score >= ?")
        args.append(min_score)
    if min_rating:
        # An unrated business fails the floor rather than slipping through it.
        where.append("rating IS NOT NULL AND rating >= ?")
        args.append(min_rating)

    clause = f"WHERE {' AND '.join(where)}" if where else ""
    order = {
        "score": "fit_score DESC, id DESC",
        "name": "name COLLATE NOCASE ASC",
        "newest": "id DESC",
        "oldest": "id ASC",
        "region": "region ASC, fit_score DESC",
    }.get(sort, "fit_score DESC, id DESC")

    conn = get_conn()
    total = conn.execute(f"SELECT COUNT(*) AS n FROM businesses {clause}", args).fetchone()["n"]
    rows = conn.execute(
        f"SELECT * FROM businesses {clause} ORDER BY {order} LIMIT ? OFFSET ?",
        [*args, limit, offset],
    ).fetchall()
    return [row_to_dict(r) for r in rows], int(total)


def businesses_needing_website_check(limit: int = 25) -> tuple[list[dict[str, Any]], int]:
    """A batch of businesses whose website has never been checked, plus the total."""
    clause = ("website IS NOT NULL AND website != '' "
              "AND (website_status IS NULL OR website_status = '')")
    conn = get_conn()
    total = conn.execute(f"SELECT COUNT(*) AS n FROM businesses WHERE {clause}").fetchone()["n"]
    rows = conn.execute(
        f"SELECT * FROM businesses WHERE {clause} ORDER BY id LIMIT ?", (limit,)
    ).fetchall()
    return [row_to_dict(r) for r in rows], int(total)


def businesses_needing_enrichment(limit: int = 20,
                                  recheck: bool = False) -> tuple[list[dict[str, Any]], int]:
    """
    A batch of businesses to visit for a contact address, plus the total left.

    Selects on enriched_at, not on the absence of an email. Selecting on "no
    email" cannot terminate: a visit that finds nothing leaves the record
    exactly as it was, so the same businesses come back in the next batch for
    ever. enriched_at records the attempt, so every batch makes progress even
    when it finds nothing.
    """
    clause = "website IS NOT NULL AND website != '' AND (email IS NULL OR email = '')"
    if not recheck:
        clause += " AND (enriched_at IS NULL OR enriched_at = '')"
    conn = get_conn()
    total = conn.execute(f"SELECT COUNT(*) AS n FROM businesses WHERE {clause}").fetchone()["n"]
    rows = conn.execute(
        f"SELECT * FROM businesses WHERE {clause} ORDER BY fit_score DESC, id LIMIT ?", (limit,)
    ).fetchall()
    return [row_to_dict(r) for r in rows], int(total)


def distinct_values(column: str) -> list[str]:
    if column not in {"region", "state", "industry", "source", "status", "category"}:
        raise ValueError(f"not a filterable column: {column}")
    rows = get_conn().execute(
        f"SELECT DISTINCT {column} AS v FROM businesses "
        f"WHERE {column} IS NOT NULL AND {column} != '' ORDER BY v"
    ).fetchall()
    return [r["v"] for r in rows]


def industry_options() -> list[str]:
    """
    Every label the industry filter can match, deduplicated.

    Records carry two vocabularies: `industry` is the ICP label scoring uses
    ("Home loans"), while `category` is the trade as the source names it
    ("Mortgage broker"). Both are offered because the filter matches both —
    otherwise the dropdown lists options that return nothing.
    """
    seen: dict[str, str] = {}
    for column in ("industry", "category"):
        for value in distinct_values(column):
            seen.setdefault(value.strip().lower(), value.strip())
    return sorted(seen.values(), key=str.lower)


def state_options() -> list[str]:
    """The states actually present, so the filter never offers an empty one."""
    return sorted({v.strip().upper() for v in distinct_values("state") if v.strip()})


# ---------- Contacts ----------

def add_contact(business_id: int, data: dict[str, Any]) -> int:
    fields = ("first_name", "last_name", "role", "email", "phone", "linkedin", "is_primary", "source")
    payload = {k: data.get(k) for k in fields if data.get(k) not in (None, "")}
    payload["is_primary"] = 1 if data.get("is_primary") else 0
    cols = list(payload)
    with tx() as conn:
        cur = conn.execute(
            f"INSERT INTO contacts (business_id, created_at, {', '.join(cols)}) "
            f"VALUES (?, ?, {', '.join('?' for _ in cols)})",
            [business_id, now(), *[payload[c] for c in cols]],
        )
        return int(cur.lastrowid)


def list_contacts(business_id: int) -> list[dict[str, Any]]:
    rows = get_conn().execute(
        "SELECT * FROM contacts WHERE business_id = ? ORDER BY is_primary DESC, id",
        (business_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- Prospecting runs ----------

def start_run(source: str, query: dict[str, Any]) -> int:
    with tx() as conn:
        cur = conn.execute(
            "INSERT INTO prospecting_runs (started_at, source, query, status) VALUES (?, ?, ?, 'running')",
            (now(), source, json.dumps(query)),
        )
        return int(cur.lastrowid)


def finish_run(run_id: int, *, found: int, new: int, dupes: int,
               status: str = "done", error: str | None = None,
               result_ids: list[int] | None = None) -> None:
    with tx() as conn:
        conn.execute(
            "UPDATE prospecting_runs SET finished_at = ?, status = ?, found_count = ?, "
            "new_count = ?, dupe_count = ?, result_ids = ?, error = ? WHERE id = ?",
            (now(), status, found, new, dupes, json.dumps(result_ids or []), error, run_id),
        )


def get_run(run_id: int) -> dict[str, Any] | None:
    row = get_conn().execute("SELECT * FROM prospecting_runs WHERE id = ?", (run_id,)).fetchone()
    if not row:
        return None
    d = dict(row)
    for key in ("query", "result_ids"):
        try:
            d[key] = json.loads(d[key] or ("[]" if key == "result_ids" else "{}"))
        except json.JSONDecodeError:
            d[key] = [] if key == "result_ids" else {}
    return d


def businesses_by_ids(ids: list[int]) -> list[dict[str, Any]]:
    if not ids:
        return []
    placeholders = ", ".join("?" for _ in ids)
    rows = get_conn().execute(
        f"SELECT * FROM businesses WHERE id IN ({placeholders}) ORDER BY fit_score DESC, id DESC",
        ids,
    ).fetchall()
    return [row_to_dict(r) for r in rows]


def running_now() -> list[dict[str, Any]]:
    """
    Prospecting runs still in flight.

    A dashboard that shows the app working while it works is the difference
    between live and merely recent. A run that died without finishing would
    sit here for ever, so anything older than ten minutes is not reported —
    it is not running, it is lost.
    """
    rows = get_conn().execute(
        "SELECT id, started_at, source, query FROM prospecting_runs "
        "WHERE status = 'running' AND started_at >= datetime('now', '-10 minutes') "
        "ORDER BY id DESC").fetchall()
    out = []
    for row in rows:
        item = dict(row)
        try:
            item["query"] = json.loads(item["query"] or "{}")
        except json.JSONDecodeError:
            item["query"] = {}
        out.append(item)
    return out


def recent_activity(limit: int = 10) -> list[dict[str, Any]]:
    """
    The last things that happened, newest first, with the business named.

    A dashboard that only shows totals looks the same whether the app is busy
    or asleep. This is the part that shows it is alive.
    """
    rows = get_conn().execute(
        "SELECT a.created_at, a.kind, a.detail, b.id AS business_id, b.name "
        "FROM activities a LEFT JOIN businesses b ON b.id = a.business_id "
        "ORDER BY a.id DESC LIMIT ?", (limit,)).fetchall()
    return [dict(r) for r in rows]


def recent_runs(limit: int = 10) -> list[dict[str, Any]]:
    rows = get_conn().execute(
        "SELECT * FROM prospecting_runs ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        try:
            d["query"] = json.loads(d["query"] or "{}")
        except json.JSONDecodeError:
            d["query"] = {}
        out.append(d)
    return out


# ---------- Messages ----------

def insert_message(data: dict[str, Any]) -> int:
    fields = ("business_id", "contact_id", "campaign_id", "to_email",
              "subject", "body", "status", "sent_at", "provider_id", "error")
    payload = {k: data.get(k) for k in fields if k in data}
    payload.setdefault("status", "draft")
    cols = list(payload)
    with tx() as conn:
        cur = conn.execute(
            f"INSERT INTO messages (created_at, {', '.join(cols)}) "
            f"VALUES (?, {', '.join('?' for _ in cols)})",
            [now(), *[payload[c] for c in cols]],
        )
        return int(cur.lastrowid)


def update_message(message_id: int, data: dict[str, Any]) -> None:
    cols = list(data)
    if not cols:
        return
    with tx() as conn:
        conn.execute(
            f"UPDATE messages SET {', '.join(f'{c} = ?' for c in cols)} WHERE id = ?",
            [*data.values(), message_id],
        )


def get_message(message_id: int) -> dict[str, Any] | None:
    row = get_conn().execute("SELECT * FROM messages WHERE id = ?", (message_id,)).fetchone()
    return dict(row) if row else None


def list_messages(business_id: int | None = None, status: str = "",
                  limit: int = 100) -> list[dict[str, Any]]:
    where, args = [], []
    if business_id is not None:
        where.append("m.business_id = ?")
        args.append(business_id)
    if status:
        where.append("m.status = ?")
        args.append(status)
    clause = f"WHERE {' AND '.join(where)}" if where else ""
    rows = get_conn().execute(
        f"SELECT m.*, b.name AS business_name FROM messages m "
        f"JOIN businesses b ON b.id = m.business_id {clause} ORDER BY m.id DESC LIMIT ?",
        [*args, limit],
    ).fetchall()
    return [dict(r) for r in rows]


def recent_send_to_domain(domain: str, within_days: int = 30,
                          ignore_message_id: int | None = None) -> dict[str, Any] | None:
    """
    The last email sent to anyone at this domain, if it was recent.

    A sweep of five trades across one town turns up two partners at the same
    firm often enough to matter, and info@ getting the same pitch twice in a
    week is how a prospect becomes a complaint.
    """
    if not domain:
        return None
    clause = "AND m.id != ?" if ignore_message_id else ""
    args: list[Any] = [f"%@{domain}", f"-{int(within_days)} days"]
    if ignore_message_id:
        args.append(ignore_message_id)
    row = get_conn().execute(
        "SELECT m.*, b.name AS business_name FROM messages m "
        "JOIN businesses b ON b.id = m.business_id "
        "WHERE m.status = 'sent' AND m.to_email LIKE ? "
        f"AND m.sent_at >= datetime('now', ?) {clause} "
        "ORDER BY m.sent_at DESC LIMIT 1", args
    ).fetchone()
    return dict(row) if row else None


def sends_today() -> int:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    row = get_conn().execute(
        "SELECT COUNT(*) AS n FROM messages WHERE status = 'sent' AND sent_at LIKE ?",
        (f"{today}%",),
    ).fetchone()
    return int(row["n"])


# ---------- Activities ----------

def log_activity(business_id: int | None, kind: str, detail: str = "") -> None:
    with tx() as conn:
        conn.execute(
            "INSERT INTO activities (created_at, business_id, kind, detail) VALUES (?, ?, ?, ?)",
            (now(), business_id, kind, detail),
        )


def list_activities(business_id: int, limit: int = 25) -> list[dict[str, Any]]:
    rows = get_conn().execute(
        "SELECT * FROM activities WHERE business_id = ? ORDER BY id DESC LIMIT ?",
        (business_id, limit),
    ).fetchall()
    return [dict(r) for r in rows]


def count_stale_in_stage(status: str, days: int) -> int:
    """
    How many at this stage have been sitting there longer than `days`.

    "Sitting there" is measured from the last recorded stage change, and from
    the day the record was created when it has never moved — a business that
    has been New since the day it was found has been New since that day.
    """
    row = get_conn().execute(
        "SELECT COUNT(*) AS n FROM businesses b WHERE b.status = ? AND "
        "COALESCE((SELECT MAX(a.created_at) FROM activities a "
        "          WHERE a.business_id = b.id AND a.kind = 'stage'), b.created_at) "
        "<= datetime('now', ?)",
        (status, f"-{int(days)} days"),
    ).fetchone()
    return int(row["n"])


def stage_since(business_ids: list[int]) -> dict[int, str]:
    """
    When each business last changed stage, in one query rather than N.

    Falls back to nothing for a business that has never moved; the caller
    uses its creation date, since a record that has sat at New since the day
    it was found has been at New since the day it was found.
    """
    if not business_ids:
        return {}
    marks = ", ".join("?" for _ in business_ids)
    rows = get_conn().execute(
        f"SELECT business_id, MAX(created_at) AS at FROM activities "
        f"WHERE kind = 'stage' AND business_id IN ({marks}) GROUP BY business_id",
        business_ids,
    ).fetchall()
    return {int(r["business_id"]): r["at"] for r in rows}


# ---------- Suppressions ----------

def suppress(value: str, reason: str = "") -> None:
    value = (value or "").strip().lower()
    if not value:
        return
    with tx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO suppressions (created_at, value, reason) VALUES (?, ?, ?)",
            (now(), value, reason),
        )


def is_suppressed(email: str) -> bool:
    email = (email or "").strip().lower()
    if not email:
        return True
    domain = email.split("@")[-1]
    row = get_conn().execute(
        "SELECT 1 FROM suppressions WHERE value = ? OR value = ? LIMIT 1", (email, domain)
    ).fetchone()
    return row is not None


def list_suppressions(limit: int = 200) -> list[dict[str, Any]]:
    rows = get_conn().execute(
        "SELECT * FROM suppressions ORDER BY id DESC LIMIT ?", (limit,)
    ).fetchall()
    return [dict(r) for r in rows]


# ---------- Dashboard stats ----------

def stats() -> dict[str, Any]:
    conn = get_conn()
    total = conn.execute("SELECT COUNT(*) AS n FROM businesses").fetchone()["n"]
    by_status = {
        r["status"]: r["n"]
        for r in conn.execute("SELECT status, COUNT(*) AS n FROM businesses GROUP BY status")
    }
    with_email = conn.execute(
        "SELECT COUNT(*) AS n FROM businesses WHERE email IS NOT NULL AND email != ''"
    ).fetchone()["n"]
    week = conn.execute(
        "SELECT COUNT(*) AS n FROM businesses WHERE created_at >= datetime('now', '-7 days')"
    ).fetchone()["n"]
    top_regions = [
        dict(r) for r in conn.execute(
            "SELECT region, COUNT(*) AS n FROM businesses WHERE region IS NOT NULL AND region != '' "
            "GROUP BY region ORDER BY n DESC LIMIT 6"
        )
    ]
    top_industries = [
        dict(r) for r in conn.execute(
            "SELECT industry, COUNT(*) AS n FROM businesses WHERE industry IS NOT NULL AND industry != '' "
            "GROUP BY industry ORDER BY n DESC LIMIT 6"
        )
    ]
    avg_score = conn.execute("SELECT AVG(fit_score) AS a FROM businesses").fetchone()["a"] or 0
    return {
        "total": int(total),
        "by_status": {s: int(by_status.get(s, 0)) for s in STATUSES},
        "with_email": int(with_email),
        "added_this_week": int(week),
        "top_regions": top_regions,
        "top_industries": top_industries,
        "avg_score": round(float(avg_score), 1),
        "drafts": len(list_messages(status="draft", limit=1000)),
        "sent": len(list_messages(status="sent", limit=1000)),
        "sends_today": sends_today(),
    }


def iter_all(columns: Iterable[str] = ()) -> Iterator[dict[str, Any]]:
    """Stream every business — used by the CSV export."""
    cols = ", ".join(columns) if columns else "*"
    for row in get_conn().execute(f"SELECT {cols} FROM businesses ORDER BY id"):
        yield dict(row)


init_db()
