"""
Pearch Outreach — configuration.

Everything environment-dependent lives here so the rest of the app can be
imported and tested without any keys set. With zero configuration the app
runs fully: SQLite on disk, the `seed` prospecting source, drafts written
but never sent.
"""
from __future__ import annotations

import os
from pathlib import Path

ROOT_DIR = Path(__file__).parent
TEMPLATES_DIR = ROOT_DIR / "templates"
STATIC_DIR = ROOT_DIR / "static"

APP_NAME = "Pearch Outreach"
APP_TAGLINE = "Find the Australian businesses worth talking to — then talk to them."

# ---------- Access ----------
# A single shared password protects the whole app (HTTP Basic). Unset, the app
# serves localhost freely for development but REFUSES to serve any other host —
# so a deployment without a password fails loudly instead of quietly publishing
# the contact database to the internet.
# .strip() matters: hosting dashboards often use a multi-line textarea for
# environment variables, so a pasted password easily arrives with a trailing
# newline or space. Without this, the value shown in the dashboard and the
# value that actually unlocks the app differ by an invisible character.
APP_USERNAME = os.getenv("PEARCH_USERNAME", "pearch").strip()
APP_PASSWORD = os.getenv("PEARCH_PASSWORD", "").strip()

# Paths that stay public even when a password is set: the health check Render
# polls, and the unsubscribe page recipients click from an email.
PUBLIC_PATHS = ("/health", "/unsubscribe", "/static/", "/favicon.ico")
LOCAL_HOSTS = ("localhost", "127.0.0.1", "0.0.0.0", "testserver", "[::1]")


# ---------- Storage ----------
# Render's free tier has an ephemeral filesystem; point this at a mounted
# disk (e.g. /var/data/pearch_outreach.db) to persist between deploys.
DB_PATH = Path(os.getenv("PEARCH_DB_PATH", ROOT_DIR / "pearch_outreach.db"))

# On a free Render instance there is no persistent disk, so the database is
# empty every time the service redeploys or wakes from sleep. With this set,
# the app fills an empty database with sample businesses on boot, so a shared
# demo URL always shows a working tool rather than an empty shell.
PEARCH_DEMO_SEED = os.getenv("PEARCH_DEMO_SEED", "0") == "1"


# ---------- Prospecting sources ----------
GOOGLE_PLACES_API_KEY = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()

# Explorium (the data behind Vibe Prospecting). Each fetch spends credits.
EXPLORIUM_API_KEY = os.getenv("EXPLORIUM_API_KEY", "").strip()

# Australian Business Register (ABN Lookup) GUID — free, register at
# https://abr.business.gov.au/Tools/WebServices
ABR_GUID = os.getenv("ABR_GUID", "").strip()

# ---------- Drafting ----------
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "").strip()
DRAFT_MODEL = os.getenv("PEARCH_DRAFT_MODEL", "claude-opus-5")

# Effort trades thoroughness against token spend within one model. A 120-word
# outreach email is not intelligence-sensitive work, so "medium" is the sensible
# default; raise it if the drafts read thin, lower it if the bill does not.
DRAFT_EFFORT = os.getenv("PEARCH_DRAFT_EFFORT", "medium")

# ---------- Sending ----------
# Sending is OFF by default and stays off until someone deliberately turns it
# on. Drafts are always written to the database first and need an explicit
# approve step, so nothing can leave the building by accident.
SEND_ENABLED = os.getenv("PEARCH_SEND_ENABLED", "0") == "1"
RESEND_API_KEY = os.getenv("RESEND_API_KEY", "").strip()
FROM_NAME = os.getenv("PEARCH_FROM_NAME", "Pearch")
FROM_EMAIL = os.getenv("PEARCH_FROM_EMAIL", "onboarding@resend.dev")
REPLY_TO_EMAIL = os.getenv("PEARCH_REPLY_TO", "danielc@austcommunitymedia.com.au")

# Spam Act 2003 (Cth) requires accurate sender identification and a working
# unsubscribe in every commercial electronic message. Both are appended to
# every outgoing email by outreach.py — these are the values it uses.
SENDER_IDENTITY = os.getenv(
    "PEARCH_SENDER_IDENTITY",
    "Pearch · Australian Community Media · Newcastle NSW 2300",
)
UNSUBSCRIBE_URL = os.getenv("PEARCH_UNSUBSCRIBE_URL", "")  # falls back to the app's own /unsubscribe

# Belt and braces: a daily cap on sends so a bad loop can't blast a list.
DAILY_SEND_CAP = int(os.getenv("PEARCH_DAILY_SEND_CAP", "50"))

# ---------- Ideal customer profile ----------
# Drives scoring.py. Tune these to change what "qualified" means without
# touching the scoring logic.
ICP = {
    # Industries we sell into best, highest-value first.
    "industries": [
        "real estate", "home loans", "mortgage broker", "legal", "law firm",
        "accounting", "financial planning", "dental", "medical", "aged care",
        "trades", "building", "construction", "automotive", "hospitality",
        "tourism", "education", "retail", "agriculture", "mining services",
    ],
    # ACM heartland — regional Australia is where our mastheads are.
    "regions": [
        "Hunter", "Illawarra", "Central West NSW", "Riverina", "Bendigo",
        "Ballarat", "Canberra", "Launceston", "Wollongong", "Newcastle",
    ],
    "min_rating": 3.5,
    "min_reviews": 5,
}

# ---------- Regions ----------
# Postcode ranges → the ACM region a business belongs to. Deliberately coarse:
# it only needs to be good enough to group prospects for a masthead pitch.
POSTCODE_REGIONS: list[tuple[int, int, str, str]] = [
    (2250, 2263, "NSW", "Central Coast"),
    (2264, 2299, "NSW", "Hunter"),
    (2300, 2308, "NSW", "Newcastle"),
    (2309, 2339, "NSW", "Hunter"),
    (2500, 2535, "NSW", "Illawarra"),
    (2536, 2551, "NSW", "South Coast NSW"),
    (2580, 2594, "NSW", "Southern Tablelands"),
    (2600, 2618, "ACT", "Canberra"),
    (2620, 2639, "NSW", "Riverina"),
    (2640, 2739, "NSW", "Riverina"),
    (2740, 2786, "NSW", "Blue Mountains"),
    (2787, 2899, "NSW", "Central West NSW"),
    (3000, 3207, "VIC", "Melbourne"),
    (3208, 3334, "VIC", "Geelong"),
    (3335, 3399, "VIC", "Ballarat"),
    (3400, 3489, "VIC", "Wimmera"),
    (3490, 3599, "VIC", "Bendigo"),
    (3600, 3749, "VIC", "Goulburn Valley"),
    (3750, 3999, "VIC", "Gippsland"),
    (4000, 4207, "QLD", "Brisbane"),
    (4208, 4299, "QLD", "Gold Coast"),
    (4300, 4499, "QLD", "Darling Downs"),
    (4500, 4579, "QLD", "Sunshine Coast"),
    (4580, 4899, "QLD", "Regional QLD"),
    (5000, 5199, "SA", "Adelaide"),
    (5200, 5799, "SA", "Regional SA"),
    (6000, 6199, "WA", "Perth"),
    (6200, 6799, "WA", "Regional WA"),
    (7000, 7099, "TAS", "Hobart"),
    (7100, 7999, "TAS", "Launceston"),
    (800, 899, "NT", "Darwin"),
    (900, 999, "NT", "Regional NT"),
]


def region_for_postcode(postcode: str | int | None) -> tuple[str | None, str | None]:
    """Return (state, region) for an Australian postcode. ('NSW', 'Hunter')."""
    if postcode in (None, ""):
        return None, None
    try:
        pc = int(str(postcode).strip()[:4])
    except (TypeError, ValueError):
        return None, None
    for low, high, state, region in POSTCODE_REGIONS:
        if low <= pc <= high:
            return state, region
    return None, None
