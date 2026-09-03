# ACM Outreach Database

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/danielc-beep/Pearch-Outreach)

A database of Australian businesses worth talking to — and the machinery to
actually talk to them. Prospect a trade and a town, and it finds the
businesses, visits their websites for contact details, scores them against
your ideal customer, dedupes them against what you already have, and drafts
the outreach email a human then approves.

House style: deep editorial navy, one warm yellow accent, Inter with
Newsreader italics.

```
prospect  →  enrich  →  score  →  draft  →  approve  →  send
 Google      website     0-100    Claude    a human    Resend
 Places /    scrape      vs ICP    or a     says yes   + unsubscribe
 CSV /       for email             template
 sample
```

## Run it

```bash
pip install -r requirements.txt
uvicorn app:app --reload --port 8000
# open http://localhost:8000
```

No API keys needed to start. The `sample` prospecting source generates
fictional Australian businesses (every domain ends in `.example.com.au`) so
you can run the whole workflow end to end before wiring anything up.

Tests:

```bash
python -m pytest tests -q
```

## Configuration

Everything is environment variables — see `.env.example`. Nothing is
required; each key just switches on more of the app.

| Variable | What it turns on |
| --- | --- |
| `PEARCH_PASSWORD` | The shared sign-in password. **Required in production** — see below |
| `GOOGLE_PLACES_API_KEY` | Real business prospecting via Google Places |
| `ANTHROPIC_API_KEY` | Per-business email drafts written by Claude |
| `PEARCH_DRAFT_MODEL` | Which model drafts (default `claude-opus-5`) |
| `PEARCH_DRAFT_EFFORT` | How hard it thinks: `low`–`max` (default `medium`) |
| `RESEND_API_KEY` + `PEARCH_SEND_ENABLED=1` | Actually sending email |
| `ABR_GUID` | ABN / legal entity lookup against the Australian Business Register |
| `PEARCH_DB_PATH` | Where the SQLite file lives (use a mounted disk in production) |

### Access

One shared password (HTTP Basic) protects the whole app, which is the right
amount of ceremony for a few colleagues sharing a link. Set `PEARCH_PASSWORD`
and everyone signs in as `pearch` with that password.

With no password set, the app serves `localhost` freely — but **refuses to
serve any other host**, showing a "locked" page instead. A deployment that
forgets the password fails loudly rather than quietly publishing the contact
database. `/health` and `/unsubscribe` stay public either way; the latter has
to work for recipients clicking through from an email.

## The screens

| Route | What it's for |
| --- | --- |
| `/` | Dashboard — the prospecting search bar, live counts, pipeline, top prospects |
| `/businesses` | The database: filter by status, region, industry, score, contactability |
| `/businesses/{id}` | One business: details, score breakdown, contacts, drafts, timeline |
| `/prospect` | Pick a source, run a search, see what came back |
| `/campaigns` | The subject and body being sent, with a live merge preview |
| `/outbox` | Drafts waiting for approval, approved messages waiting to send, sent history |
| `/suppressions` | Everyone permanently excluded from outreach |
| `/unsubscribe` | The public unsubscribe page linked from every email |

## Prospecting sources

Sources live in `sources/` and are registered in `sources/__init__.py`. Each
one implements a single `search(query) -> list[dict]`; everything downstream
— normalising, region mapping, enrichment, scoring, deduping — is shared, so
adding a source is one function.

- **`google_places`** — Places API (New) text search. Name, address, website,
  phone, rating, review count. Needs `GOOGLE_PLACES_API_KEY`.
- **`csv`** — paste or import a list you already own. Headers are matched
  loosely, so a CRM export imports without renaming columns.
- **`sample`** — deterministic fictional businesses for trying things out.

To add another (an industry association member list, a directory, a scraped
page), copy `sources/csv_import.py`, write `search()`, and add the module to
`_MODULES`.

## Scoring

`scoring.py` gives every business 0–100 from nine signals — an email on file
is worth the most, then industry fit against the ICP, then whether they sit
in an ACM masthead region, then phone, address, reviews and socials. The
reasons are stored alongside the score and shown on the business page, so a
score is always explainable.

Tune `ICP` in `config.py` and rerun `prospect.rescore_all()` to re-rank the
whole database.

## Deduping

A business is matched on its domain first, then on name + postcode, then on
the source's own record id. Merging never overwrites a filled field with an
empty one, so a thin result from one source can only ever add detail to what
you already know — re-running the same search is safe and cheap.

## Sending, and not sending

Outreach email is regulated. The app is built so the careless path is the
safe one:

- Sending is **off** unless `PEARCH_SEND_ENABLED=1` *and* `RESEND_API_KEY`
  is set.
- Every message is written as a draft, and a human has to approve it before
  `send_message()` will look at it.
- Every send is checked against the suppression list, the business's
  do-not-contact flag, and a daily cap (`PEARCH_DAILY_SEND_CAP`, default 50).
- Every email carries sender identification and a working unsubscribe link —
  what the Spam Act 2003 (Cth) requires of commercial electronic messages.
  Unsubscribes suppress the address and flag the business automatically.

Consent is still your call, not the app's. The Spam Act's tests for inferred
consent (a publicly listed business address, relevant to that person's role)
are the ones to apply before you prospect a list, and conspicuous publication
rules mean an address published with a "no unsolicited email" notice is off
limits regardless of what this tool found.

## Layout

```
pearch-outreach/
├── app.py              FastAPI routes — pages and JSON API
├── config.py           env vars, ICP, postcode → region map
├── db.py               SQLite schema and queries
├── prospect.py         the pipeline: search → normalise → score → upsert
├── enrich.py           website scrape for email/phone/socials/industry, ABR lookup
├── scoring.py          the 0-100 fit score
├── outreach.py         drafting (Anthropic SDK), campaigns, approval, sending
├── util.py             URL/email/phone/address normalising
├── sources/            prospecting sources (google_places, csv, sample)
├── templates/          Jinja2 pages
├── static/             the design system (app.css), app.js, favicon
└── tests/              pytest suite, no network, no keys
```

## Deploying

See **[DEPLOY.md](DEPLOY.md)** for step-by-step Render instructions — it covers
deploying from a subdirectory of an existing repo as well as from a standalone
one, and what to set when you're ready to turn on Google Places, Claude drafts
or real sending.

The short version: `render.yaml` describes a web service with a 1GB disk for
the SQLite file and `/health` as the health check. The database needs that
disk — Render's free tier has none, so a free instance loses everything on
each deploy.
