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
| `ANTHROPIC_API_KEY` | Per-business email drafts, and the dashboard coach's chat |
| `PEARCH_DRAFT_MODEL` | Which model drafts (default `claude-opus-5`) |
| `PEARCH_DRAFT_EFFORT` | How hard it thinks: `low`–`max` (default `medium`) |
| `RESEND_API_KEY` + `PEARCH_SEND_ENABLED=1` | Actually sending email |
| `PEARCH_INBOUND_SECRET` | Replies coming back in through `/api/inbound/mail` |
| `PEARCH_DEFAULT_DEAL_VALUE` | Starting value for an unpriced deal (set it in the app instead) |
| `ABR_GUID` | ABN / legal entity lookup against the Australian Business Register |
| `PEARCH_DB_PATH` | Where the SQLite file lives (use a mounted disk in production) |

### Access

One shared password (HTTP Basic) protects the whole app, which is the right
amount of ceremony for a few colleagues sharing a link. Set `PEARCH_PASSWORD`
and everyone signs in as `ACM` with that password.

With no password set, the app serves `localhost` freely — but **refuses to
serve any other host**, showing a "locked" page instead. A deployment that
forgets the password fails loudly rather than quietly publishing the contact
database. `/health` and `/unsubscribe` stay public either way; the latter has
to work for recipients clicking through from an email.

## The screens

Six tabs, in the order the work is actually done. Three of them have sections,
because several of the old nine were pairs.

| Tab | Sections | What it's for |
| --- | --- | --- |
| Dashboard | — | Target, revenue, pipeline, next steps, and the coach |
| Prospect | — | Pick a source, run a search, see what came back |
| CRM | — | The kanban board — drag a deal from one stage to the next |
| Emails | Outbox · Inbox | Drafts waiting to go, and everything that came back |
| Revenue | The year · Delivery | The year against last year, the client book, and what is still to publish |
| Admin | Review · Align · Mastheads · Database | The queue, masthead alignment, all 78 titles, and every record |

`/health` is public and unauthenticated. Alongside liveness it carries
`needs_you` — the handful of things nobody inside the app can fix, because
they live in a hosting console or on somebody's laptop: no prospecting
source, sending off, replies not being collected, unpriced deals counting as
nothing, a stale or missing backup. Dan's morning brief reads it, which is
the one place this app gets seen without anybody opening it. It is
deliberately plain about the state and quiet about the remedy — it says a
thing is off, never which environment variable turns it on — and it carries
no business names.

Off the nav but linked where they are needed: `/businesses/{id}` (one business),
`/addresses` (the ones with a website but no email), `/followups`,
`/suppressions`, `/backups`, `/unsubscribe`.

Every URL these pages used to live at still works — `/outbox`, `/replies`,
`/review`, `/review/align`, `/align`, `/mastheads` and `/businesses` all
redirect, query string included.

## Next steps

The dashboard's job list is a carousel: one job at a time, in the order they
should be done, the whole slide a link to the screen that fixes it. A carousel
is the wrong shape for most things and the right one here — the list is short,
every item is a real job, and the point is to put one of them in front of you
rather than let five counts blur into wallpaper.

What stops it being annoying: it pauses the moment you point at it or tab into
it, it pauses when the tab is hidden, there is a button to stop it for good,
and it does not move at all for anyone whose system asks for less motion. A
moving target you cannot click is worse than a static list. The dots are
labelled with what each step is, so they are navigation rather than
decoration, and arrow keys work when focus is inside.

The live poll updates the counts in place, and rebuilds the slides when the
set of jobs changes — a slide for a job that is finished is worse than no
slide.

## The coach

The dashboard's third column is an advisor. The question box sits directly
under the heading, because asking is what people open the panel to do; the
three suggestions below it are what it says before anybody has asked anything.
It reads the live pipeline —
target and gap, revenue, what is sitting at each stage, what has gone cold,
which trades have replied and signed — and offers three things to do next,
best first. Underneath it is a chat box that answers questions against the
same figures.

Three rules make it safe to act on:

- **It never invents a number.** Claude is handed a brief built from the
  database and told those are the only figures it may use, so every claim can
  be checked against the dashboard it sits on.
- **It never invents a link.** A suggestion names one of the screens in
  `coach.FOCUS`; the app owns the URL. A made-up path is impossible.
- **It works without a key.** With no `ANTHROPIC_API_KEY` the same three come
  out of arithmetic on the same brief, and the panel says which it is showing.
  Only a model answer is cached, so a key arriving fixes the panel at once
  rather than waiting for the pipeline to move.

Advice is cached against a fingerprint of the figures that would change it, so
the page can poll without spending a model call on an afternoon where nothing
happened.

It runs down the full height of the dashboard grid, with the trades table
tucked into the space beside it. Laid out flat the coach was half again as
tall as the columns next to it, which left a wedge of empty blue under
"What's waiting" and pushed the table below the fold; this way the three
columns finish level and the whole dashboard lands on one screen.

## Replies, and the second email

**Replies** come in through `/api/inbound/mail` — a public URL, so it is shut
unless `PEARCH_INBOUND_SECRET` is set and every call carries it in an
`X-Pearch-Secret` header. Point any forwarder at it: Resend inbound, a
Cloudflare email worker, a Zap. Three distinctions do the work:

- An **out-of-office is not a reply**. It is recorded and moves nothing. It is
  the classic false positive, and counting it would corrupt the one number
  this exists to keep true.
- An **opt-out is not a reply** either. It is suppressed, marked
  do-not-contact and ruled out on arrival.
- A **bounce is about somebody who is not the sender**. It arrives from a mail
  system, so matching it on who sent it finds nobody; the failed address is
  inside the message and is read out of it. A hard bounce takes that address
  out of sending — which stops the follow-up sequence too — but does not rule
  the business out, because a dead mailbox is not a refusal and somebody
  should go and find a working address. A full mailbox is a soft bounce and is
  only recorded.
- An **unmatched reply is not dropped**. It waits at `/emails/inbox` to be
  attached, and the dashboard counts it above the replies themselves.

Matching is the address first, then the domain — a reply often comes from a
colleague of the person we wrote to. Free-mail domains never match on the
domain, because nobody's company is gmail.com. If none of this is set up,
paste a reply in on the business's own page and it is read the same way.

**Follow-ups** at `/followups`. Most replies to cold outreach arrive on the
second or third email, so one touch is about a third of a list. It writes
them; it never sends them — they land in the outbox as drafts and go out
through the same approval, preflight, suppression list and daily cap as
everything else. It stops after two follow-ups, and it stops the moment
anything human comes back through the inbox, whatever the stage says.

## Revenue, and the client book

A client pays for twelve months from **the day their content goes live**, not
the day the deal was signed — there are usually a couple of weeks of writing
in between, and billing a term from the wrong end of that gap is how a renewal
gets missed. Both dates are stored, and they do different jobs:

- **signed** puts the money in a month of the year's revenue.
- **live** starts the term and sets the renewal date.

Every revenue event — the first sale and each renewal — is a row in
`contracts`, so a client's second year counts as revenue rather than
disappearing (a renewal is not a new deal, and `won_at` alone cannot see it).
Deals that predate the ledger are backfilled once on startup.

`/revenue` shows the year month by month, with **last year drawn as a
reference tick on each bar** rather than a second coloured series: against
this page's ink, cyan and any muted slate come out about eleven units apart in
normal vision, which the palette validator rejects, and a tick on a bar says
"the line you are being measured against" more directly anyway. Year-to-date
is compared with the *same stretch* of last year, never with its full twelve
months.

**What an unpriced deal counts as** is set on that page too, not in a hosting
console — it is a pricing decision somebody makes in a meeting, and it is read
live, so saving is enough and there is nothing to redeploy. It starts at $0, so
the pipeline figure is only what somebody actually agreed to until you say
otherwise; the box pre-fills with the middle of what you have already priced.
Deals carrying a real figure are never overwritten by it, and the count of
unpriced ones is stated beside every total that leans on it.

## What the client is paying for

A client's twelve months starts the day their content goes live, so a piece
sitting on a desk is a term that has not begun. `/revenue/delivery` tracks it:
brief → writing → with the desk → published, and **publishing sets the go-live
date** and starts the clock. There is nothing to type twice, and a second piece
never moves a renewal date that is already set.

Then, each month, a rep files the Pearch report on the client's own page:
citations, notes, and the file itself. That is the evidence the renewal rests
on — a renewal conversation with nothing on file but an invoice is a hard one.
One report per client per month, replaced rather than duplicated, so a
corrected upload leaves one row rather than two and a question.

Reps are a name typed in a box, not accounts. The app has one shared login, so
a name is an honest record of who did the work where a login would be a claim
the app cannot back up.

Uploaded files live beside the database and are **not** inside a snapshot. The
backups page says so and prints the folder — a snapshot records that a report
exists and what it said, not the file.

Underneath is the client book. A won deal with no go-live date is the top row,
not a hidden one — that is a client whose content nobody has published. A
renewal starts the day the current term ends, so renewing early or late does
not change what the client gets. And a client who ran a year and left is
`churned`, not `lost`: folding them into lost deals would flatter the loss
column and hide the churn.

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
├── coach.py            the dashboard's three suggestions and its sounding board
├── coverage.py         all 78 mastheads and what each one's book is worth
├── replies.py          inbound mail: whose it is, what it is, what it changes
├── followup.py         the second and third emails, and when they are owed
├── renewals.py         go-live dates, terms, renewal dates, churn
├── revenue.py          the year against last year, and the chart geometry
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
