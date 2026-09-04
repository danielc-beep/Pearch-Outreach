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
    monkeypatch.delenv("PEARCH_USERNAME", raising=False)
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


def test_a_stale_username_in_the_environment_cannot_lock_you_out(monkeypatch):
    """
    The deployment was carrying PEARCH_USERNAME=pearch from the first day it
    went up, so the name the owner had been given did not work and the only
    symptom was a password that looked wrong.
    """
    monkeypatch.setenv("PEARCH_USERNAME", "pearch")
    c = _client(monkeypatch, "s3cret")
    assert _sign_in(c, username="Daniel").status_code == 303


def test_a_configured_username_still_works_alongside_it(monkeypatch):
    monkeypatch.setenv("PEARCH_USERNAME", "pearch")
    c = _client(monkeypatch, "s3cret")
    assert _sign_in(c, username="pearch").status_code == 303
    assert _sign_in(c, username="PEARCH").status_code == 303


def test_an_unrelated_name_is_still_refused(monkeypatch):
    monkeypatch.setenv("PEARCH_USERNAME", "pearch")
    c = _client(monkeypatch, "s3cret")
    assert _sign_in(c, username="admin").status_code == 401


def test_a_non_ascii_username_is_refused_not_a_crash(monkeypatch):
    """secrets.compare_digest rejects non-ASCII strings by raising."""
    c = _client(monkeypatch, "s3cret")
    assert _sign_in(c, username="Dani\u00e9l").status_code == 401


def test_the_username_field_arrives_filled_in(monkeypatch):
    """The half that is not secret should never be the half that goes wrong."""
    monkeypatch.setenv("PEARCH_USERNAME", "pearch")
    c = _client(monkeypatch, "s3cret")
    assert 'value="Daniel"' in c.get("/login").text


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


# ---------- Changing the password from inside the app ----------
# The password lived only in the hosting dashboard, which meant nobody without
# Render credentials could ever change it — and it took an afternoon to find
# out it was not the value everyone believed it was.

def _stored_client(monkeypatch, env_password="s3cret"):
    """A signed-in client with a clean settings table."""
    import db
    db.reset_db()
    c = _client(monkeypatch, env_password)
    import auth
    auth.forget_cached_password()
    _sign_in(c, password=env_password)
    return c


def _change(c, current, new, confirm=None):
    return c.post("/password", data={"current": current, "new_password": new,
                                     "confirm": new if confirm is None else confirm})


def test_the_change_password_page_needs_a_session(monkeypatch):
    c = _client(monkeypatch, "s3cret")
    response = c.get("/password", headers={"host": "acm-outreach.onrender.com"})
    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")


def test_a_new_password_takes_effect(monkeypatch):
    import auth
    c = _stored_client(monkeypatch)
    assert _change(c, "s3cret", "Pearch2026").status_code == 303
    assert auth.password_ok("Pearch2026")
    assert auth.password_ok("s3cret") is False


def test_you_can_sign_in_again_with_the_new_one(monkeypatch):
    c = _stored_client(monkeypatch)
    _change(c, "s3cret", "Pearch2026")
    c.get("/logout")
    assert _sign_in(c, password="s3cret").status_code == 401
    assert _sign_in(c, password="Pearch2026").status_code == 303


def test_changing_it_does_not_sign_out_the_person_changing_it(monkeypatch):
    """The session key comes from the password, so the cookie must be reissued."""
    c = _stored_client(monkeypatch)
    _change(c, "s3cret", "Pearch2026")
    assert c.get("/", headers={"host": "acm-outreach.onrender.com"}).status_code == 200


def test_it_does_sign_out_everybody_else(monkeypatch):
    import auth
    from fastapi.testclient import TestClient
    import app as app_module
    c = _stored_client(monkeypatch)
    other = TestClient(app_module.app, follow_redirects=False)
    _sign_in(other, password="s3cret")
    assert other.get("/", headers={"host": "acm-outreach.onrender.com"}).status_code == 200
    _change(c, "s3cret", "Pearch2026")
    assert other.get("/", headers={"host": "acm-outreach.onrender.com"}).status_code == 303


def test_the_wrong_current_password_changes_nothing(monkeypatch):
    import auth
    c = _stored_client(monkeypatch)
    response = _change(c, "not-it", "Pearch2026")
    assert "not the current password" in response.text
    assert auth.password_ok("s3cret")


def test_a_mistyped_confirmation_changes_nothing(monkeypatch):
    import auth
    c = _stored_client(monkeypatch)
    response = _change(c, "s3cret", "Pearch2026", confirm="Pearch2027")
    assert "do not match" in response.text
    assert auth.password_ok("s3cret")


def test_a_short_password_is_refused(monkeypatch):
    import auth
    c = _stored_client(monkeypatch)
    assert "six characters" in _change(c, "s3cret", "ACM26").text
    assert auth.password_ok("s3cret")


def test_the_password_is_never_stored_in_the_clear(monkeypatch):
    import db
    c = _stored_client(monkeypatch)
    _change(c, "s3cret", "Pearch2026")
    stored = db.get_setting("password_hash")
    assert stored.startswith("pbkdf2$")
    assert "Pearch2026" not in stored


def test_the_dashboard_password_is_the_way_back(monkeypatch):
    """
    Forget the in-app password and changing PEARCH_PASSWORD gets you in again.
    Without this the only recovery would be editing the database by hand.
    """
    import auth
    c = _stored_client(monkeypatch)
    _change(c, "s3cret", "forgotten-forever")

    _client(monkeypatch, "a-new-dashboard-password")
    import importlib
    importlib.reload(auth)
    auth.forget_cached_password()
    assert auth.password_ok("a-new-dashboard-password")
    assert auth.password_ok("forgotten-forever") is False


def test_the_dashboard_password_stops_working_once_changed_in_the_app(monkeypatch):
    """Exactly one password works at a time, or revoking access means nothing."""
    import auth
    c = _stored_client(monkeypatch)
    _change(c, "s3cret", "Pearch2026")
    assert auth.password_ok("s3cret") is False


def test_a_tampered_hash_refuses_rather_than_crashing(monkeypatch):
    import auth, db
    c = _stored_client(monkeypatch)
    _change(c, "s3cret", "Pearch2026")
    for junk in ("", "rubbish", "pbkdf2$notanumber$aa$bb", "sha1$1$aa$bb"):
        db.set_setting("password_hash", junk)
        auth.forget_cached_password()
        assert auth.password_ok("Pearch2026") is False, junk
