"""Every test runs against a throwaway database, never the real one."""
from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("PEARCH_DB_PATH", str(Path(tempfile.mkdtemp()) / "test.db"))
# Most tests prospect with the sample source, which is off by default in
# production so fictional businesses cannot reach a real database.
os.environ.setdefault("PEARCH_ENABLE_SAMPLE_SOURCE", "1")
# No background snapshots during tests; backup.py is tested directly.
os.environ.setdefault("PEARCH_BACKUPS", "0")

import pytest  # noqa: E402

import db  # noqa: E402


@pytest.fixture(autouse=True)
def clean_db():
    db.reset_db()
    yield
    db.reset_db()


@pytest.fixture
def client():
    from fastapi.testclient import TestClient
    import app
    return TestClient(app.app)


@pytest.fixture
def sample_run():
    import prospect
    return prospect.run(
        "sample",
        {"industry": "mortgage broker", "location": "Newcastle NSW", "limit": 8},
        enrich=False,
    )
