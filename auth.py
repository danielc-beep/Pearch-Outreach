"""
Shared-password access control.

The whole app sits behind one HTTP Basic password, which is the right amount
of ceremony for a handful of colleagues sharing one link. Two rules matter:

1. If PEARCH_PASSWORD is set, every page needs it — except the health check
   and the unsubscribe page, which have to stay reachable.
2. If it is NOT set, only localhost is served. Any other host gets a 503
   telling the operator to set a password. A deployment that forgets the
   password fails loudly rather than quietly publishing the contact database.
"""
from __future__ import annotations

import base64
import binascii
import secrets

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import HTMLResponse, Response

from config import APP_PASSWORD, APP_USERNAME, LOCAL_HOSTS, PUBLIC_PATHS

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
    return path.startswith(PUBLIC_PATHS)


def _is_local(request: Request) -> bool:
    host = (request.headers.get("host") or "").split(":")[0]
    return host in LOCAL_HOSTS


def _credentials_ok(header: str) -> bool:
    """Constant-time check of an `Authorization: Basic ...` header."""
    scheme, _, encoded = (header or "").partition(" ")
    if scheme.lower() != "basic" or not encoded:
        return False
    try:
        decoded = base64.b64decode(encoded).decode("utf-8")
    except (binascii.Error, UnicodeDecodeError, ValueError):
        return False
    username, _, password = decoded.partition(":")
    username, password = username.strip(), password.strip()
    # The username is matched case-insensitively: it is not the secret, and a
    # colleague typing "acm" should not be locked out of a login they were
    # given as "ACM". The password is matched exactly.
    # Both halves are always checked, so a wrong username costs the same time
    # as a wrong password.
    return (secrets.compare_digest(username.lower(), APP_USERNAME.lower())
            & secrets.compare_digest(password, APP_PASSWORD))


class PasswordMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path

        if not APP_PASSWORD:
            if _is_local(request) or _is_public(path):
                return await call_next(request)
            return HTMLResponse(UNPROTECTED_HTML, status_code=503)

        if _is_public(path) or _credentials_ok(request.headers.get("authorization", "")):
            return await call_next(request)

        return Response(
            "Sign in to ACM Outreach Database.",
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="ACM Outreach Database", charset="UTF-8"'},
        )
