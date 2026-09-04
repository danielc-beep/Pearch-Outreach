"""
Shared-password access control, behind a sign-in page.

The whole app sits behind one shared login, which is the right amount of
ceremony for a handful of colleagues sharing one link. Three rules matter:

1. If PEARCH_PASSWORD is set, every page needs it — except the health check
   and the unsubscribe page, which have to stay reachable.
2. If it is NOT set, only localhost is served. Any other host gets a 503
   telling the operator to set a password. A deployment that forgets the
   password fails loudly rather than quietly publishing the contact database.
3. Signing in is a page, not the browser's own dialog. The dialog cannot be
   branded, says nothing useful when the password is wrong, and offers no way
   to sign out short of closing the browser.

The session is a cookie carrying an expiry and an HMAC of it, keyed on the
password itself — so changing the password signs everybody out, and a
restart does not. It has no Max-Age, so closing the browser ends it and the
next visit asks again.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import time
from collections import defaultdict

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from config import APP_PASSWORD, APP_USERNAMES, LOCAL_HOSTS, PUBLIC_PATHS

COOKIE = "acm_session"
SESSION_HOURS = 12          # an absolute ceiling, even if the browser stays open
LOGIN_PATH = "/login"

# A shared password on a public URL invites guessing, and a form is easier to
# script against than a browser dialog. This is not a real rate limiter — one
# process, memory only — but it turns an overnight dictionary run into
# something that would take months.
MAX_ATTEMPTS = 8
LOCKOUT_SECONDS = 300
_attempts: dict[str, list[float]] = defaultdict(list)


UNPROTECTED_HTML = """<!doctype html>
<html><head><meta charset="utf-8"><title>Set a password first</title>
<meta name="viewport" content="width=device-width, initial-scale=1">
<style>body{font-family:-apple-system,Segoe UI,Helvetica,Arial,sans-serif;
background:radial-gradient(ellipse 90% 60% at 50% -10%,#24407A 0%,transparent 62%),
linear-gradient(180deg,#16224A 0%,#0D1636 100%);background-attachment:fixed;
color:#E8F1FA;display:flex;min-height:100vh;align-items:center;justify-content:center;margin:0;padding:24px}
.box{max-width:520px}h1{font-size:26px;letter-spacing:-0.02em;margin:0 0 12px;color:#fff}
p{color:#93A7C4;line-height:1.6}code{background:rgba(0,154,199,0.18);padding:2px 6px;border-radius:5px;color:#7FDCFA}</style>
</head><body><div class="box">
<h1>ACM Outreach Database is locked</h1>
<p>This instance is reachable from the internet but no shared password is set, so
it is refusing to serve the database.</p>
<p>Set <code>PEARCH_PASSWORD</code> in the environment (on Render: Settings &rarr;
Environment &rarr; Add Environment Variable) and redeploy. Everyone you share the
link with signs in with that one password.</p>
</div></body></html>"""


def _is_public(path: str) -> bool:
    return path.startswith(PUBLIC_PATHS) or path == LOGIN_PATH


def _is_local(request: Request) -> bool:
    host = (request.headers.get("host") or "").split(":")[0]
    return host in LOCAL_HOSTS


# ---------- The session cookie ----------

def _key() -> bytes:
    """
    The signing key, derived from the password.

    Deriving it means a password change invalidates every existing session,
    which is what anyone changing a shared password expects. It also means
    there is no second secret to set and forget.
    """
    return hashlib.sha256(("acm-outreach-session:" + APP_PASSWORD).encode()).digest()


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(now: float | None = None) -> str:
    """A token good until SESSION_HOURS from now."""
    expires = int((now if now is not None else time.time()) + SESSION_HOURS * 3600)
    body = str(expires).encode()
    return _b64(body) + "." + _b64(hmac.new(_key(), body, hashlib.sha256).digest())


def token_ok(token: str, now: float | None = None) -> bool:
    """Whether a token is ours and still current."""
    if not token or not APP_PASSWORD:
        return False
    body_part, _, signature_part = token.partition(".")
    if not body_part or not signature_part:
        return False
    try:
        body = _unb64(body_part)
        signature = _unb64(signature_part)
    except (ValueError, TypeError):
        return False

    if not hmac.compare_digest(signature, hmac.new(_key(), body, hashlib.sha256).digest()):
        return False
    try:
        return int(body) > (now if now is not None else time.time())
    except ValueError:
        return False


def _same(typed: str, expected: str) -> bool:
    """A comparison that takes the same time whether or not it matches."""
    return hmac.compare_digest(typed.encode("utf-8", "surrogatepass"),
                               expected.encode("utf-8", "surrogatepass"))


def credentials_ok(username: str, password: str) -> bool:
    """
    The username is matched case-insensitively against every accepted name —
    it is not the secret, and someone given "Daniel" who types "daniel" should
    not be turned away. The password is matched exactly. Both halves are always
    checked, and every name is, so a wrong username costs the same time as a
    wrong password.
    """
    if not APP_PASSWORD:
        return False
    typed = (username or "").strip().lower()
    named = False
    for accepted in APP_USERNAMES:
        named |= _same(typed, accepted.lower())
    return named & _same((password or "").strip(), APP_PASSWORD)


# ---------- Throttling ----------

def _prune(ip: str, now: float) -> list[float]:
    recent = [t for t in _attempts[ip] if now - t < LOCKOUT_SECONDS]
    _attempts[ip] = recent
    return recent


def locked_out(ip: str, now: float | None = None) -> int:
    """Seconds still to wait, or 0 if this address may try again."""
    now = now if now is not None else time.time()
    recent = _prune(ip, now)
    if len(recent) < MAX_ATTEMPTS:
        return 0
    return max(1, int(LOCKOUT_SECONDS - (now - min(recent))))


def record_failure(ip: str, now: float | None = None) -> None:
    now = now if now is not None else time.time()
    _prune(ip, now)
    _attempts[ip].append(now)


def clear_failures(ip: str) -> None:
    _attempts.pop(ip, None)


def client_ip(request: Request) -> str:
    """The caller's address, trusting Render's proxy header when it is there."""
    forwarded = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return forwarded or (request.client.host if request.client else "unknown")


def set_session(response: Response, secure: bool) -> Response:
    """
    Attach a fresh session.

    No Max-Age, so it is a session cookie: closing the browser ends it and
    the next visit asks for the password again.
    """
    response.set_cookie(COOKIE, make_token(), httponly=True, samesite="lax",
                        secure=secure, path="/")
    return response


def clear_session(response: Response) -> Response:
    response.delete_cookie(COOKIE, path="/")
    return response


def safe_next(encoded: str) -> str:
    """
    Where to go after signing in.

    Only a path on this site. An open redirect on a login page is how a
    convincing phishing link gets built out of a real domain.
    """
    try:
        target = _unb64(encoded or "").decode()
    except (ValueError, TypeError, UnicodeDecodeError):
        return "/"
    if not target.startswith("/") or target.startswith("//") or target.startswith(LOGIN_PATH):
        return "/"
    return target


class PasswordMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if not APP_PASSWORD:
            if _is_local(request) or _is_public(path):
                return await call_next(request)
            return HTMLResponse(UNPROTECTED_HTML, status_code=503)

        if _is_public(path) or token_ok(request.cookies.get(COOKIE, "")):
            return await call_next(request)

        # An API call gets a status it can act on; a person gets the page.
        if path.startswith("/api/"):
            return Response("Not signed in.", status_code=401)

        target = path + ("?" + request.url.query if request.url.query else "")
        return RedirectResponse(f"{LOGIN_PATH}?next={_b64(target.encode())}", status_code=303)
