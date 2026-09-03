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
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from config import DB_PATH

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
MIGRATIONS: list[tuple[str, str]] = [
    ("prospecting_runs", "ALTER TABLE prospecting_runs ADD COLUMN result_ids TEXT"),
    ("businesses", "ALTER TABLE businesses ADD COLUMN website_status TEXT"),
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
                  "contacts", "suppressions", "businesses"):
        conn.execute(f"DROP TABLE IF EXISTS {table}")
    conn.commit()
    init_db()


# ---------- Businesses ----------

BUSINESS_FIELDS = (
    "name", "legal_name", "abn", "website", "domain", "email", "phone",
    "address", "suburb", "state", "postcode", "region", "country",
    "industry", "category", "size_band", "rating", "review_count",
    "linkedin", "facebook", "instagram", "description",
    "source", "source_ref", "status", "fit_score", "score_reasons",
    "notes", "website_status", "do_not_contact", "last_contacted_at", "enriched_at",
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


def delete_sample_businesses() -> int:
    """Remove every fictional business. Returns how many went."""
    count = count_sample_businesses()
    if count:
        with tx() as conn:
            conn.execute(f"DELETE FROM businesses WHERE {SAMPLE_CLAUSE}")
    return count


def delete_business(business_id: int) -> None:
    with tx() as conn:
        conn.execute("DELETE FROM businesses WHERE id = ?", (business_id,))


def list_businesses(
    q: str = "",
    status: str = "",
    region: str = "",
    state: str = "",
    industry: str = "",
    source: str = "",
    has_email: bool | None = None,
    website_status: str = "",
    min_score: int = 0,
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
                          ("source", source), ("website_status", website_status)):
        if value:
            where.append(f"{column} = ?")
            args.append(value)
    if industry:
        where.append("(industry LIKE ? OR category LIKE ?)")
        args += [f"%{industry}%"] * 2
    if has_email is True:
        where.append("email IS NOT NULL AND email != ''")
    elif has_email is False:
        where.append("(email IS NULL OR email = '')")
    if min_score:
        where.append("fit_score >= ?")
        args.append(min_score)

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
