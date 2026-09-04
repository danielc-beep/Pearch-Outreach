"""
Signing in.

The app sits behind one shared login. Two things must hold whatever else
changes: a public host with no password set serves nothing, and a visitor
without a valid session gets the sign-in page rather than the database.
"""
from __future__ import annotations

import time

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
    auth._attempts.clear()
    return TestClient(app_module.app, follow_redirects=False)


@pytest.fixture(autouse=True)
def restore_app(monkeypatch):
    yield
    import importlib
    monkeypatch.delenv("PEARCH_PASSWORD", raising=False)
    import auth, config, app as app_module
    auth._attempts.clear()
    importlib.reload(config)
    importlib.reload(auth)
    importlib.reload(app_module)


def _sign_in(c: TestClient, username="Daniel", password="s3cret", next_token=""):
    return c.post("/login", data={"username": username, "password": password,
                                  "next": next_token})


# ---------- Nothing is served without a password ----------

def test_without_a_password_localhost_still_works(client):
    assert client.get("/").status_code == 200


def test_without_a_password_a_public_host_is_refused(client):
    response = client.get("/", headers={"host": "acm-outreach.onrender.com"})
    assert response.status_code == 503
    assert "no shared password is set" in response.text


def test_health_and_unsubscribe_stay_public(client):
    for path in ("/health", "/unsubscribe"):
        response = client.get(path, headers={"host": "acm-outreach.onrender.com"})
        assert response.status_code == 200, path


# ---------- The sign-in page ----------

def test_a_visitor_is_sent_to_the_sign_in_page(monkeypatch):
    c = _client(monkeypatch, "s3cret")
    response = c.get("/", headers={"host": "acm-outreach.onrender.com"})
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_the_sign_in_page_is_a_page_not_a_browser_dialog(monkeypatch):
    c = _client(monkeypatch, "s3cret")
    response = c.get("/login")
    assert response.status_code == 200
    assert "www-authenticate" not in {k.lower() for k in response.headers}
    assert 'name="username"' in response.text
    assert 'type="password"' in response.text
    assert "ACM" in response.text          # it carries the masthead


def test_the_right_credentials_sign_you_in(monkeypatch):
    c = _client(monkeypatch, "s3cret")
    response = _sign_in(c)
    assert response.status_code == 303
    assert response.headers["location"] == "/"
    assert c.get("/", headers={"host": "acm-outreach.onrender.com"}).status_code == 200


def test_the_wrong_password_is_refused_and_says_so_on_the_page(monkeypatch):
    c = _client(monkeypatch, "s3cret")
    response = _sign_in(c, password="wrong")
    assert response.status_code == 401
    assert "do not match" in response.text
    assert c.get("/", headers={"host": "acm-outreach.onrender.com"}).status_code == 303


def test_the_error_does_not_say_which_half_was_wrong(monkeypatch):
    """Naming the half tells someone guessing they have found a username."""
    c = _client(monkeypatch, "s3cret")
    wrong_user = _sign_in(c, username="nobody").text
    wrong_pass = _sign_in(c, password="nope").text
    assert "do not match" in wrong_user and "do not match" in wrong_pass


def test_the_username_is_matched_case_insensitively(monkeypatch):
    for typed in ("Daniel", "daniel", "DANIEL"):
        c = _client(monkeypatch, "s3cret")
        assert _sign_in(c, username=typed).status_code == 303, typed


def test_the_password_is_matched_exactly(monkeypatch):
    c = _client(monkeypatch, "ACM2026")
    assert _sign_in(c, password="ACM2026").status_code == 303
    for wrong in ("acm2026", "ACM2026 x", "Acm2026"):
        assert _sign_in(c, password=wrong).status_code == 401, wrong


def test_the_default_username_is_daniel(monkeypatch):
    import importlib, config
    monkeypatch.delenv("PEARCH_USERNAME", raising=False)
    importlib.reload(config)
    assert config.APP_USERNAME == "Daniel"


def test_no_password_ships_in_the_code():
    """The repository is public; a default password would publish it."""
    import config, inspect
    source = inspect.getsource(config)
    assert 'os.getenv("PEARCH_PASSWORD", "")' in source
    assert "ACM2026" not in source


# ---------- Sessions ----------

def test_signing_out_ends_the_session(monkeypatch):
    c = _client(monkeypatch, "s3cret")
    _sign_in(c)
    assert c.get("/", headers={"host": "acm-outreach.onrender.com"}).status_code == 200
    c.get("/logout")
    assert c.get("/", headers={"host": "acm-outreach.onrender.com"}).status_code == 303


def test_the_cookie_dies_with_the_browser(monkeypatch):
    """No Max-Age, so closing the browser means signing in again."""
    c = _client(monkeypatch, "s3cret")
    header = _sign_in(c).headers["set-cookie"].lower()
    assert "httponly" in header
    assert "samesite=lax" in header
    assert "max-age" not in header and "expires" not in header


def test_a_forged_cookie_is_refused(monkeypatch):
    import auth
    c = _client(monkeypatch, "s3cret")
    for forged in ("rubbish", "abc.def", auth.make_token()[:-4] + "aaaa"):
        c.cookies.set(auth.COOKIE, forged)
        assert c.get("/", headers={"host": "acm-outreach.onrender.com"}).status_code == 303, forged


def test_an_expired_session_is_refused(monkeypatch):
    import auth
    c = _client(monkeypatch, "s3cret")
    stale = auth.make_token(now=time.time() - auth.SESSION_HOURS * 3600 - 60)
    assert auth.token_ok(stale) is False
    c.cookies.set(auth.COOKIE, stale)
    assert c.get("/", headers={"host": "acm-outreach.onrender.com"}).status_code == 303


def test_changing_the_password_signs_everyone_out(monkeypatch):
    c = _client(monkeypatch, "s3cret")
    header = _sign_in(c).headers["set-cookie"]
    value = header.split("=", 1)[1].split(";")[0]

    import auth
    assert auth.token_ok(value)
    _client(monkeypatch, "a-new-password")
    import importlib
    importlib.reload(auth)
    assert auth.token_ok(value) is False


# ---------- Where you land afterwards ----------

def test_you_land_back_where_you_were_going(monkeypatch):
    c = _client(monkeypatch, "s3cret")
    response = c.get("/review?masthead=examiner.com.au",
                     headers={"host": "acm-outreach.onrender.com"})
    token = response.headers["location"].split("next=")[1]
    assert _sign_in(c, next_token=token).headers["location"] == "/review?masthead=examiner.com.au"


def test_the_next_parameter_cannot_send_you_off_site(monkeypatch):
    """An open redirect on a login page builds a phishing link from a real domain."""
    import base64
    import auth
    c = _client(monkeypatch, "s3cret")
    for evil in ("https://evil.example.com", "//evil.example.com", "/login"):
        token = base64.urlsafe_b64encode(evil.encode()).decode().rstrip("=")
        assert auth.safe_next(token) == "/", evil
        assert _sign_in(c, next_token=token).headers["location"] == "/"


# ---------- Guessing ----------

def test_repeated_failures_lock_the_address_out(monkeypatch):
    import auth
    c = _client(monkeypatch, "s3cret")
    for _ in range(auth.MAX_ATTEMPTS):
        _sign_in(c, password="wrong")
    blocked = _sign_in(c, password="wrong")
    assert blocked.status_code == 429
    assert "Too many attempts" in blocked.text
    # The right password is refused too while the lockout stands, or the
    # throttle would be trivial to step around.
    assert _sign_in(c).status_code == 429


def test_signing_in_clears_the_count(monkeypatch):
    import auth
    c = _client(monkeypatch, "s3cret")
    for _ in range(auth.MAX_ATTEMPTS - 1):
        _sign_in(c, password="wrong")
    assert _sign_in(c).status_code == 303
    assert _attempts_are_empty(auth)


def _attempts_are_empty(auth) -> bool:
    return all(not v for v in auth._attempts.values())


# ---------- The API ----------

def test_an_api_call_gets_a_status_not_a_redirect(monkeypatch):
    """A fetch cannot follow a redirect to HTML and do anything sensible."""
    c = _client(monkeypatch, "s3cret")
    response = c.get("/api/stats", headers={"host": "acm-outreach.onrender.com"})
    assert response.status_code == 401


def test_the_api_works_once_signed_in(monkeypatch):
    c = _client(monkeypatch, "s3cret")
    _sign_in(c)
    assert c.get("/api/stats", headers={"host": "acm-outreach.onrender.com"}).status_code == 200
