# Deploying the ACM Outreach Database to Render

About 10 minutes. At the end you have a URL and one shared password to hand
your colleagues.

---

## Before you start

**Set a password.** The app refuses to serve anything but localhost unless
`PEARCH_PASSWORD` is set — a deployed instance without one shows a "locked"
page instead of your contact database. Pick a password now (a long random
one is fine, everyone uses the same one) and keep it handy for step 4.

**About the plan.** The database is a SQLite file, so it needs a persistent
disk to survive deploys and restarts. Render only offers disks on paid
instance types; on the free tier the database is wiped every time the service
redeploys or wakes from sleep. Starter is the cheapest plan that works
properly — check the current price on Render's pricing page.

---

## Path A — deploy from this branch (fastest, no new repo)

The code is at `pearch-outreach/` inside `danielc-beep/pearch-audit-`, on the
branch `claude/pearch-outreach-database-5bgjq9`. Render can deploy straight
from that subdirectory, so nothing needs moving first.

### 1. Create the service

1. Go to **https://dashboard.render.com** → **New** → **Web Service**
2. Connect the repository **`danielc-beep/pearch-audit-`**
   (if it isn't listed, click *Configure account* and grant Render access to it)
3. Fill in:

   | Field | Value |
   | --- | --- |
   | Name | `pearch-outreach` |
   | Branch | `claude/pearch-outreach-database-5bgjq9` |
   | Root Directory | `pearch-outreach` |
   | Runtime / Language | `Python 3` |
   | Build Command | `pip install -r requirements.txt` |
   | Start Command | `uvicorn app:app --host 0.0.0.0 --port $PORT` |
   | Instance Type | `Starter` |

   **Root Directory matters** — without it Render tries to build the audit app
   at the repo root instead.

### 2. Add the disk

Still on the create screen (or afterwards under **Settings → Disks**):

| Field | Value |
| --- | --- |
| Name | `pearch-outreach-data` |
| Mount Path | `/var/data` |
| Size | `1 GB` |

### 3. Set the health check

**Settings → Health Check Path** → `/health`

### 4. Add the environment variables

**Environment → Add Environment Variable**, for each of these:

| Key | Value | Why |
| --- | --- | --- |
| `PEARCH_DB_PATH` | `/var/data/pearch_outreach.db` | Puts the database on the disk so it survives deploys |
| `PEARCH_PASSWORD` | *the password you chose* | **Required.** The shared sign-in password |
| `PEARCH_USERNAME` | `ACM` | The username everyone signs in with |
| `PYTHON_VERSION` | `3.11` | Pins the runtime |

Leave the rest for now — the app works without them.

### 5. Deploy

Click **Create Web Service**. First build takes 2–3 minutes. When it goes
live, open the URL: the browser asks for a username and password — `ACM`
and the password from step 4.

You'll land on the dashboard with an empty database. Type an industry and a
location into the search bar and hit **Prospect** to fill it.

---

## Path B — deploy from a standalone repo

If you'd rather it lived in its own repository (cleaner long term):

```bash
# create the empty repo first, in the browser or with the gh CLI
gh repo create danielc-beep/pearch-outreach --private

cd pearch-outreach
./scripts/publish_new_repo.sh danielc-beep/pearch-outreach
```

Then in Render: **New → Blueprint**, point it at `danielc-beep/pearch-outreach`,
and it reads `render.yaml` — disk, health check and a generated password all
come from that file. Read the generated password under **Environment** after
the first deploy.

If you've already deployed via Path A, you don't need to start over: change
**Settings → Repository** to the new repo, set **Branch** to `main`, and clear
**Root Directory**. The disk and its database come across untouched.

---

## Sharing it

Send colleagues three things:

1. the Render URL
2. the username (`ACM`)
3. the password

Everyone shares one login, so treat the password like a door key — anyone
with it can see every contact and export the database. Change it any time by
editing `PEARCH_PASSWORD` in Render; the service restarts and the old password
stops working.

**What stays public on purpose:** `/health` (Render polls it) and
`/unsubscribe` (recipients click it from emails). Neither exposes any data.

---

## Turning on the real features

Add these in **Environment** whenever you're ready. Each one triggers a
redeploy, which takes about a minute.

### Real business data — Google Places

Without this the only sources are CSV import and sample data.

1. **https://console.cloud.google.com** → create a project
2. **APIs & Services → Library** → enable **Places API (New)**
3. **Credentials → Create Credentials → API key**, then restrict it to the
   Places API
4. Render: add `GOOGLE_PLACES_API_KEY` = your key

Text Search is billed per request, not per result, and Google includes a
monthly free allowance — check current pricing before running big jobs.

### Claude-written drafts

Add `ANTHROPIC_API_KEY` (from **https://console.anthropic.com**). Without it
the **Draft with Claude** button is greyed out and drafts come from the
campaign template instead.

### Actually sending email

This is the one to leave until last. Sending needs **both**:

- `RESEND_API_KEY` — from **https://resend.com**, with your sending domain verified
- `PEARCH_SEND_ENABLED` = `1`

Also set:

- `PEARCH_FROM_EMAIL` — an address on your verified domain
- `PEARCH_REPLY_TO` — where replies should land
- `PEARCH_SENDER_IDENTITY` — the business name and address that appears in the
  footer of every email (a legal requirement, not decoration)
- `PEARCH_DAILY_SEND_CAP` — defaults to 50

Even with all of that, nothing sends itself: every message is drafted, a human
approves it in the Outbox, and only then can it go. That is deliberate.

---

## Keeping it running

- **Deploying a change.** Push to the branch Render is watching; it rebuilds
  automatically.
- **Backups.** The database is one file on the disk. Use **Export CSV** in the
  footer for a copy you can keep, or open a Render shell and download
  `/var/data/pearch_outreach.db`.
- **If a deploy fails**, check **Logs** — the usual cause is a missing Root
  Directory (Path A) or a typo in an environment variable name.
- **If the site shows "Pearch Outreach is locked"**, `PEARCH_PASSWORD` isn't
  set on the service. Add it and redeploy.
