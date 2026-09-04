"""The app must not serve a public host without a shared password."""
from __future__ import annotations

import base64

import pytest
from fastapi.testclient import TestClient


def _client(monkeypatch, password: str) -> TestClient:
    """Rebuild the app with a given password, since it's read at import time."""
    import importlib
    import auth, config, app as app_module
    monkeypatch.setenv("PEARCH_PASSWORD", password)
    importlib.reload(config)
    importlib.reload(auth)
    importlib.reload(app_module)
    return TestClient(app_module.app)


def _basic(username: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture(autouse=True)
def restore_app(monkeypatch):
    yield
    import importlib
    monkeypatch.delenv("PEARCH_PASSWORD", raising=False)
    import auth, config, app as app_module
    importlib.reload(config)
    importlib.reload(auth)
    importlib.reload(app_module)


def test_without_a_password_localhost_still_works(client):
    assert client.get("/").status_code == 200


def test_without_a_password_a_public_host_is_refused(client):
    response = client.get("/", headers={"host": "pearch-outreach.onrender.com"})
    assert response.status_code == 503
    assert "no shared password is set" in response.text


def test_health_and_unsubscribe_stay_public(client):
    for path in ("/health", "/unsubscribe"):
        response = client.get(path, headers={"host": "pearch-outreach.onrender.com"})
        assert response.status_code == 200, path


def test_with_a_password_the_app_challenges(monkeypatch):
    c = _client(monkeypatch, "s3cret")
    response = c.get("/", headers={"host": "pearch-outreach.onrender.com"})
    assert response.status_code == 401
    assert "Basic" in response.headers["www-authenticate"]


def test_with_a_password_correct_credentials_get_in(monkeypatch):
    c = _client(monkeypatch, "s3cret")
    response = c.get("/", headers={"host": "pearch-outreach.onrender.com",
                                   **_basic("ACM", "s3cret")})
    assert response.status_code == 200


def test_wrong_password_is_rejected(monkeypatch):
    c = _client(monkeypatch, "s3cret")
    for creds in (_basic("ACM", "wrong"), _basic("nope", "s3cret")):
        response = c.get("/", headers={"host": "pearch-outreach.onrender.com", **creds})
        assert response.status_code == 401


def test_a_password_still_leaves_unsubscribe_open(monkeypatch):
    c = _client(monkeypatch, "s3cret")
    assert c.get("/unsubscribe", headers={"host": "x.onrender.com"}).status_code == 200


def test_a_password_pasted_with_stray_whitespace_still_works(monkeypatch):
    """Hosting dashboards use textareas; a trailing newline must not lock you out."""
    c = _client(monkeypatch, "  s3cret\n")
    response = c.get("/", headers={"host": "x.onrender.com", **_basic("ACM", "s3cret")})
    assert response.status_code == 200


def test_credentials_typed_with_stray_whitespace_still_work(monkeypatch):
    """A password autofilled or pasted with a trailing space must still sign in."""
    c = _client(monkeypatch, "s3cret")
    response = c.get("/", headers={"host": "x.onrender.com", **_basic(" ACM ", "s3cret ")})
    assert response.status_code == 200


def test_the_default_username_is_acm(monkeypatch):
    from config import APP_USERNAME
    assert APP_USERNAME == "ACM"


def test_the_username_is_matched_case_insensitively(monkeypatch):
    """It is half a shared login the team types, not the secret."""
    c = _client(monkeypatch, "s3cret")
    for typed in ("ACM", "acm", "Acm"):
        response = c.get("/", headers={"host": "x.onrender.com",
                                       **_basic(typed, "s3cret")})
        assert response.status_code == 200, typed


def test_the_password_is_still_matched_exactly(monkeypatch):
    c = _client(monkeypatch, "ACM2026")
    ok = c.get("/", headers={"host": "x.onrender.com", **_basic("ACM", "ACM2026")})
    assert ok.status_code == 200
    for wrong in ("acm2026", "ACM2026 x", "Acm2026"):
        response = c.get("/", headers={"host": "x.onrender.com", **_basic("ACM", wrong)})
        assert response.status_code == 401, wrong


def test_no_password_ships_in_the_code():
    """The repository is public; a default password would publish it."""
    import config, inspect
    source = inspect.getsource(config)
    assert 'os.getenv("PEARCH_PASSWORD", "")' in source
    assert "ACM2026" not in source
