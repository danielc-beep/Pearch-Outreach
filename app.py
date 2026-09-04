"""
ACM Outreach Database — web app.

Server-rendered pages for the humans (Jinja2), a small JSON API for the bits
the pages do without a reload (prospecting, drafting, approving, sending).

Run it:
    uvicorn app:app --reload --port 8000
"""
from __future__ import annotations

import csv
import io
import json
import logging
import os
from typing import Any
from urllib.parse import urlencode

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import (FileResponse, HTMLResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pydantic import BaseModel, Field

import db
import mastheads
import demo
import outreach
import prospect
import review
import worklist
import backup
import sources
import auth
from auth import PasswordMiddleware
from config import (ANTHROPIC_API_KEY, APP_NAME, APP_PASSWORD, APP_TAGLINE, APP_USERNAME,
                    DB_PATH, DAILY_SEND_CAP, MIN_PROSPECT_RATING, SEND_ENABLED,
                    STATIC_DIR, TEMPLATES_DIR)
from scoring import band

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

app = FastAPI(title=APP_NAME)
app.add_middleware(PasswordMiddleware)

# A free instance loses its database on every restart; this puts sample
# businesses back so a shared URL is never an empty shell. No-op otherwise.
demo.seed_if_empty()
app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")
templates = Jinja2Templates(directory=str(TEMPLATES_DIR))

SORTS = [
    ("score", "Best fit"),
    ("newest", "Newest first"),
    ("oldest", "Oldest first"),
    ("name", "Name A–Z"),
    ("region", "By region"),
]
PAGE_SIZE = 50


def score_widget(score: int | None) -> Markup:
    """The little score bar used in every table. Rendered here, not in CSS-less HTML."""
    value = int(score or 0)
    tier = band(value)
    return Markup(
        f'<span class="score"><span class="bar {tier}"><i style="width:{value}%"></i></span>'
        f'<span class="n">{value}</span></span>'
    )


def masthead_label(name: str) -> str:
    """
    A masthead name for a menu rather than a sentence.

    The names are stored the way they are written mid-email — "the Newcastle
    Herald" — because that is where they spend most of their life. A picker
    wants the capital.
    """
    name = (name or "").strip()
    return name[:1].upper() + name[1:] if name else name


templates.env.filters["masthead_label"] = masthead_label

# A snapshot on startup and once a day after. The disk holds the only copy of
# everything the app knows, and a deploy is a good moment to take one.
if os.getenv("PEARCH_BACKUPS", "1") == "1":
    backup.start_daily()

templates.env.globals.update(
    score_widget=score_widget,
    app_name=APP_NAME,
    tagline=APP_TAGLINE,
    send_enabled=SEND_ENABLED,
    password_set=bool(APP_PASSWORD),
    ai_available=bool(ANTHROPIC_API_KEY),
    statuses=db.STATUSES,
    masthead_groups=mastheads.options(),
    masthead_name=mastheads.name_for,
)


def _int_param(value: str | int | None, default: int = 0) -> int:
    """
    Read a number from a query string that a form filled in.

    An HTML form submits every field it holds, so an untouched number box
    arrives as "" — which a plain `int` query parameter rejects with a 422.
    Typed as a string and coerced here, a blank box means "no filter" and junk
    means the same, rather than an error page.
    """
    if value in (None, ""):
        return default
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return default


def _float_param(value: str | float | None, default: float = 0.0) -> float:
    """_int_param for a rating box, which holds a decimal."""
    if value in (None, ""):
        return default
    try:
        return float(str(value).strip())
    except (TypeError, ValueError):
        return default


def preferred_source(infos: list[sources.SourceInfo]) -> str:
    """Default the source picker to the best thing that actually works today."""
    by_key = {s.key: s for s in infos}
    for key in ("google_places", "sample", "csv"):
        if key in by_key and by_key[key].available:
            return key
    return infos[0].key


def page(request: Request, name: str, **context: Any) -> HTMLResponse:
    return templates.TemplateResponse(request, name, context)


# ---------- Pages ----------

@app.get("/", response_class=HTMLResponse)
def home(request: Request) -> HTMLResponse:
    stats = db.stats()
    top, _ = db.list_businesses(sort="score", limit=8)
    infos = sources.all_sources()
    live = next((s.label for s in infos if s.available and s.key not in ("sample", "csv")), "")
    return page(
        request, "home.html",
        nav="home",
        stats=stats,
        contactable_pct=round(100 * stats["with_email"] / stats["total"]) if stats["total"] else 0,
        top_businesses=top,
        runs=db.recent_runs(6),
        sources=infos,
        live_source=live,
        default_source=preferred_source(infos),
        board=worklist.board(),
        next_step=worklist.next_step(bool(stats["total"])),
    )


REVIEW_FILTERS = ("q", "status", "region", "state", "industry", "source",
                  "masthead", "min_score", "min_rating")


def _review_filters(params: dict[str, str]) -> dict[str, Any]:
    """The subset of the database filters the review queue accepts."""
    out: dict[str, Any] = {}
    for key in REVIEW_FILTERS:
        value = (params.get(key) or "").strip()
        if not value:
            continue
        if key == "min_score":
            out[key] = _int_param(value)
        elif key == "min_rating":
            out[key] = _float_param(value)
        else:
            out[key] = value
    return out


@app.get("/review", response_class=HTMLResponse)
def review_page(request: Request) -> HTMLResponse:
    """One business at a time, with a decision at the end of it."""
    filters = _review_filters(dict(request.query_params))
    return page(
        request, "review.html",
        nav="review",
        total=len(review.queue_ids(**filters)),
        undrafted=db.list_businesses(needs_review=True, needs_draft=True, limit=1, **filters)[1],
        f={k: (request.query_params.get(k) or "") for k in REVIEW_FILTERS},
        regions=db.distinct_values("region"),
        industries=db.industry_options(),
        has_filters=bool(filters),
    )


class TerritoryStep(BaseModel):
    masthead: str
    industry: str
    source: str = "google_places"
    limit: int | str = 20
    enrich: bool = True


@app.post("/api/territory/step")
def api_territory_step(step: TerritoryStep) -> JSONResponse:
    """One industry of a masthead's patch. The caller loops over the rest."""
    try:
        return JSONResponse(prospect.territory_step(
            step.masthead, step.industry, source_key=step.source,
            limit=max(1, min(_int_param(step.limit, 20), 60)), enrich=step.enrich))
    except (KeyError, ValueError, RuntimeError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.exception("territory step failed")
        raise HTTPException(status_code=502, detail=f"Prospecting failed: {e}") from e


@app.get("/api/review/queue")
def api_review_queue(request: Request) -> JSONResponse:
    ids = review.queue_ids(**_review_filters(dict(request.query_params)))
    return JSONResponse({"ids": ids, "total": len(ids)})


class DraftBatch(BaseModel):
    limit: int = 8
    use_ai: bool = True
    filters: dict[str, str] = {}


@app.post("/api/drafts/batch")
def api_draft_batch(body: DraftBatch) -> JSONResponse:
    """
    Write drafts for a batch of businesses awaiting one.

    Batched with a remaining count; the caller loops until it reaches zero.
    A single request that drafted forty emails would outlive the proxy.
    """
    filters = _review_filters(body.filters)
    return JSONResponse(outreach.draft_batch(
        limit=max(1, min(body.limit, 20)), use_ai=body.use_ai, **filters))


@app.get("/api/review/card/{business_id}")
def api_review_card(business_id: int) -> JSONResponse:
    card = review.card(business_id)
    if not card:
        raise HTTPException(status_code=404, detail="No such business")
    return JSONResponse(card)


class Decision(BaseModel):
    decision: str
    note: str = ""


@app.post("/api/review/decide/{business_id}")
def api_review_decide(business_id: int, body: Decision) -> JSONResponse:
    try:
        return JSONResponse(review.decide(business_id, body.decision, note=body.note))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.get("/businesses", response_class=HTMLResponse)
def businesses(request: Request, q: str = "", status: str = "", region: str = "",
               state: str = "", industry: str = "", source: str = "",
               has_email: str = "", website_status: str = "", min_score: str = "",
               min_rating: str = "", masthead: str = "",
               sort: str = "score", page_no: str = "1") -> HTMLResponse:
    min_score_value = _int_param(min_score)
    page_no = max(1, _int_param(page_no, 1))
    rows, total = db.list_businesses(
        q=q, status=status, region=region, state=state, industry=industry, source=source,
        has_email={"1": True, "0": False}.get(has_email),
        website_status=website_status,
        min_score=min_score_value, min_rating=_float_param(min_rating),
        masthead=masthead, sort=sort,
        limit=PAGE_SIZE, offset=(page_no - 1) * PAGE_SIZE,
    )
    # Keep the raw string in the filter dict so the form redisplays what was
    # typed, rather than replacing an empty box with a 0.
    filters = {"q": q, "status": status, "region": region, "state": state,
               "industry": industry, "source": source, "has_email": has_email,
               "website_status": website_status, "min_score": min_score,
               "min_rating": min_rating, "masthead": masthead, "sort": sort}
    query_string = urlencode({k: v for k, v in filters.items() if v})

    def page_url(n: int) -> str:
        params = {k: v for k, v in filters.items() if v}
        params["page_no"] = n
        return f"/businesses?{urlencode(params)}"

    # An empty result is ambiguous: the search found nothing, or the other
    # filters excluded what it found. Only worth a second query when empty.
    search_only_total = 0
    if not rows and q:
        _, search_only_total = db.list_businesses(q=q, limit=1)

    return page(
        request, "businesses.html",
        nav="businesses",
        businesses=rows, total=total, f=filters,
        search_only_total=search_only_total,
        search_only_url=f"/businesses?{urlencode({'q': q, 'sort': sort})}",
        has_filters=bool(query_string.replace("sort=score", "").strip("&")),
        regions=db.distinct_values("region"),
        industries=db.industry_options(),
        sorts=SORTS,
        page=page_no,
        pages=max(1, -(-total // PAGE_SIZE)),
        page_url=page_url,
        export_query=f"?{query_string}" if query_string else "",
        sample_count=db.count_sample_businesses(),
        min_prospect_rating=MIN_PROSPECT_RATING,
        unaligned_count=db.count_without_masthead(),
        below_rating_count=db.count_below_rating(MIN_PROSPECT_RATING),
        unreachable_count=db.list_businesses(website_status="unreachable", limit=1)[1],
    )


@app.get("/businesses/{business_id}", response_class=HTMLResponse)
def business_detail(request: Request, business_id: int) -> HTMLResponse:
    business = db.get_business(business_id)
    if not business:
        raise HTTPException(status_code=404, detail="No such business")
    return page(
        request, "business_detail.html",
        nav="businesses",
        b=business,
        band=band(business["fit_score"]),
        contacts=db.list_contacts(business_id),
        messages=db.list_messages(business_id=business_id),
        activities=db.list_activities(business_id),
    )


@app.get("/addresses", response_class=HTMLResponse)
def addresses_page(request: Request, masthead: str = "", industry: str = "") -> HTMLResponse:
    """
    The businesses the scraper could not find an address for.

    They are real, they scored, and they are unusable until someone has an
    address to write to — so this is the one screen where a person can be
    faster than the automation.
    """
    rows, total = db.list_businesses(
        has_email=False, masthead=masthead, industry=industry,
        sort="score", limit=60,
    )
    # A business with no website has nowhere to look, so it is not work.
    rows = [b for b in rows if b.get("website")]
    return page(
        request, "addresses.html",
        nav="addresses",
        businesses=rows, total=total,
        f={"masthead": masthead, "industry": industry},
        industries=db.industry_options(),
    )


@app.get("/prospect", response_class=HTMLResponse)
def prospect_page(request: Request, source: str = "", run: int | None = None) -> HTMLResponse:
    infos = sources.all_sources()
    active = source or preferred_source(infos)
    payload = [
        {
            "key": s.key, "label": s.label, "description": s.description,
            "available": s.available, "unavailable_reason": s.unavailable_reason,
            "fields": [
                {"name": f.name, "label": f.label, "placeholder": f.placeholder,
                 "kind": f.kind, "default": f.default, "help": f.help,
                 "options": f.options}
                for f in s.fields
            ],
        }
        for s in infos
    ]

    results_html = ""
    run_row = db.get_run(run) if run else None
    if run_row:
        results_html = templates.get_template("_results.html").render(
            run=run_row,
            businesses=db.businesses_by_ids(run_row["result_ids"]),
            score_widget=score_widget,
        )

    return page(
        request, "prospect.html",
        nav="prospect",
        sources=infos,
        sources_json=Markup(json.dumps(payload)),
        active_source=active,
        run_result=bool(run_row),
        results_html=Markup(results_html),
        patches=mastheads.with_a_patch(),
        home_location=mastheads.home_location,
        territory_industries=prospect.TERRITORY_INDUSTRIES,
        live_prospecting=any(s.available and s.key != "csv" and s.key != "sample" for s in infos),
    )


@app.get("/campaigns", response_class=HTMLResponse)
def campaigns_page(request: Request, id: int | None = None) -> HTMLResponse:
    all_campaigns = outreach.list_campaigns()
    current = outreach.get_campaign(id) if id else outreach.default_campaign()
    sample, _ = db.list_businesses(sort="score", limit=1)
    preview_business = sample[0] if sample else None
    fields = outreach.merge_fields(preview_business or {"name": "Acme Pty Ltd"})
    return page(
        request, "campaigns.html",
        nav="campaigns",
        campaigns=all_campaigns,
        current=current,
        preview_business=preview_business,
        merge_fields=sorted(fields),
        preview={
            "subject": outreach.render_template(current["subject"], fields),
            "body": outreach.render_template(current["body"], fields),
            "footer": outreach._footer("someone@example.com.au", str(request.base_url)),
        },
    )


@app.post("/campaigns")
def save_campaign(name: str = Form(...), subject: str = Form(...), body: str = Form(...),
                  campaign_id: int = Form(0), save_as_new: str = Form("")) -> RedirectResponse:
    saved = outreach.save_campaign(name, subject, body,
                                   None if save_as_new else (campaign_id or None))
    return RedirectResponse(f"/campaigns?id={saved['id']}", status_code=303)


@app.get("/outbox", response_class=HTMLResponse)
def outbox(request: Request, status: str = "") -> HTMLResponse:
    return page(
        request, "outbox.html",
        nav="outbox",
        messages=db.list_messages(status=status),
        active_tab=status,
        tabs=[("", "All"), ("draft", "Drafts"), ("approved", "Approved"),
              ("sent", "Sent"), ("failed", "Failed")],
        sends_today=db.sends_today(),
        daily_cap=DAILY_SEND_CAP,
        approved_count=len(db.list_messages(status="approved", limit=5000)),
    )


# ---------- Signing in ----------

def _https(request: Request) -> bool:
    """Whether to mark the cookie Secure. Localhost is http, so it must not be."""
    proto = request.headers.get("x-forwarded-proto", request.url.scheme)
    return proto == "https"


@app.get("/login", response_class=HTMLResponse)
def login_page(request: Request, next: str = "") -> HTMLResponse:
    if auth.token_ok(request.cookies.get(auth.COOKIE, "")):
        return RedirectResponse(auth.safe_next(next), status_code=303)
    # The username field arrives filled in. It is a shared login on one link,
    # the password is the secret, and the half that is not secret should never
    # be the half that goes wrong.
    return templates.TemplateResponse(
        request, "login.html",
        {"next_token": next, "error": "", "username": APP_USERNAME})


@app.post("/login")
def login_submit(request: Request, username: str = Form(""), password: str = Form(""),
                 next: str = Form("")):
    ip = auth.client_ip(request)
    wait = auth.locked_out(ip)
    if wait:
        return templates.TemplateResponse(
            request, "login.html",
            {"next_token": next, "username": username,
             "error": f"Too many attempts. Try again in {max(1, wait // 60)} minute"
                      f"{'s' if wait // 60 != 1 else ''}."},
            status_code=429)

    if not auth.credentials_ok(username, password):
        auth.record_failure(ip)
        log.warning("failed sign-in for %r from %s", username[:40], ip)
        # One message for both halves: saying which was wrong tells someone
        # guessing that they have found a real username.
        return templates.TemplateResponse(
            request, "login.html",
            {"next_token": next, "username": username,
             "error": "That username and password do not match."},
            status_code=401)

    auth.clear_failures(ip)
    response = RedirectResponse(auth.safe_next(next), status_code=303)
    return auth.set_session(response, secure=_https(request))


@app.get("/logout")
@app.post("/logout")
def logout():
    return auth.clear_session(RedirectResponse("/login", status_code=303))


@app.get("/backups", response_class=HTMLResponse)
def backups_page(request: Request) -> HTMLResponse:
    """Snapshots and the trash — the two ways back from a mistake."""
    return page(
        request, "backups.html", nav="",
        backups=backup.listing(),
        batches=db.trash_batches(),
        keep=backup.KEEP,
        trash_days=db.TRASH_KEEPS_DAYS,
        db_path=str(DB_PATH),
    )


@app.post("/api/backup/now")
def api_backup_now() -> JSONResponse:
    return JSONResponse(backup.make("manual"))


@app.get("/api/backup/download/{name}")
def api_backup_download(name: str) -> FileResponse:
    """
    Hand over a snapshot to keep somewhere else.

    A snapshot on the same disk protects against a mistake. Only a copy off
    the disk protects against losing the disk, and this is that copy.
    """
    path = backup.resolve(name)
    if not path:
        raise HTTPException(status_code=404, detail="No such backup")
    return FileResponse(path, media_type="application/octet-stream", filename=name)


@app.post("/api/trash/restore/{batch}")
def api_restore_batch(batch: str) -> JSONResponse:
    restored = db.restore_batch(batch)
    log.info("restored %s businesses from batch %s", restored, batch)
    return JSONResponse({"restored": restored, "total": db.stats()["total"]})


@app.get("/suppressions", response_class=HTMLResponse)
def suppressions_page(request: Request) -> HTMLResponse:
    return page(request, "suppressions.html", nav="", suppressions=db.list_suppressions())


@app.post("/suppressions")
def add_suppression(value: str = Form(...), reason: str = Form("")) -> RedirectResponse:
    outreach.unsubscribe(value, reason or "added by hand")
    return RedirectResponse("/suppressions", status_code=303)


@app.get("/unsubscribe", response_class=HTMLResponse)
def unsubscribe_page(request: Request, email: str = "") -> HTMLResponse:
    return page(request, "unsubscribe.html", nav="", email=email, done=False)


@app.post("/unsubscribe", response_class=HTMLResponse)
def do_unsubscribe(request: Request, email: str = Form(...)) -> HTMLResponse:
    outreach.unsubscribe(email, "unsubscribe link")
    return page(request, "unsubscribe.html", nav="", email=email, done=True)


@app.get("/health")
def health() -> dict[str, Any]:
    """
    Liveness plus a configuration read-out.

    Reports whether each integration is switched on from the running process's
    point of view — which is the only view that matters when a key looks set in
    a dashboard but the app disagrees. Booleans only: no key, or any part of
    one, is ever returned here.
    """
    return {
        "status": "ok",
        # Which build is actually running, so "did my deploy land?" has an
        # answer that does not depend on reading the dashboard correctly.
        "commit": os.getenv("RENDER_GIT_COMMIT", "")[:7],
        "businesses": db.stats()["total"],
        "sources": {s.key: s.available for s in sources.all_sources()},
        "configured": {
            "password": bool(APP_PASSWORD),
            "anthropic": bool(ANTHROPIC_API_KEY),
            "sending": SEND_ENABLED,
        },
    }


# ---------- JSON API ----------

class ProspectRequest(BaseModel):
    source: str = Field(default="sample")
    enrich: bool = True
    industry: str = ""
    location: str = ""
    limit: int | str = 40
    csv: str = ""
    masthead: str = ""


@app.get("/api/masthead/match")
def api_masthead_match(location: str = "") -> JSONResponse:
    """The masthead a typed location falls under, for the search bar to preselect."""
    site = mastheads.match(location)
    return JSONResponse({"site": site, "name": mastheads.name_for(site),
                         "matched": bool(site)})


@app.post("/api/prospect/run")
def api_prospect_run(req: ProspectRequest) -> JSONResponse:
    query = {"industry": req.industry, "location": req.location,
             "limit": req.limit, "csv": req.csv, "masthead": req.masthead}
    try:
        result = prospect.run(req.source, query, enrich=req.enrich)
    except KeyError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except (RuntimeError, ValueError) as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    except Exception as e:
        log.exception("prospecting run failed")
        raise HTTPException(status_code=502, detail=f"Prospecting failed: {e}") from e
    result["businesses"] = result["businesses"][:50]
    return JSONResponse(result)


@app.get("/api/businesses")
def api_businesses(q: str = "", status: str = "", region: str = "", industry: str = "",
                   min_score: str = "", min_rating: str = "",
                   sort: str = "score", limit: int = 50,
                   offset: int = 0) -> JSONResponse:
    rows, total = db.list_businesses(q=q, status=status, region=region, industry=industry,
                                     min_score=_int_param(min_score),
                                     min_rating=_float_param(min_rating), sort=sort,
                                     limit=min(limit, 500), offset=offset)
    return JSONResponse({"total": total, "businesses": rows})


class BusinessPatch(BaseModel):
    status: str | None = None
    email: str | None = None
    phone: str | None = None
    notes: str | None = None
    industry: str | None = None
    masthead: str | None = None
    do_not_contact: int | None = None


@app.post("/api/mastheads/align")
def api_align_mastheads(limit: int = 500) -> JSONResponse:
    """
    Give every stored business its masthead.

    Prospecting stamps one now; anything found before that has none, and a
    masthead filter that omits most of the database is worse than no filter.
    """
    result = prospect.align_mastheads(limit=max(1, min(limit, 2000)))
    log.info("aligned %s businesses to a masthead, %s unmatched",
             result["aligned"], result["unmatched"])
    return JSONResponse(result)


@app.post("/api/businesses/purge-low-rated")
def api_purge_low_rated() -> JSONResponse:
    """
    Delete every business under the Google rating floor.

    Declared above the /{business_id} routes: a literal path has to be
    registered before the parameterised one, or FastAPI matches
    "purge-low-rated" as a business id and rejects it as a 422.

    The floor is applied to new prospecting runs, but anything found before it
    existed stays until something removes it. Since every email opens by
    congratulating the business on its rating, a record we would not
    congratulate is one we cannot work.
    """
    backup.make("before-rating-purge")
    removed, batch = db.delete_below_rating(MIN_PROSPECT_RATING)
    log.info("purged %s businesses under %s stars (batch %s)", removed, MIN_PROSPECT_RATING, batch)
    return JSONResponse({"removed": removed, "batch": batch,
                         "remaining": db.stats()["total"]})


@app.post("/api/businesses/{business_id}")
def api_update_business(business_id: int, patch: BusinessPatch) -> JSONResponse:
    if not db.get_business(business_id):
        raise HTTPException(status_code=404, detail="No such business")
    data = {k: v for k, v in patch.model_dump().items() if v is not None}
    if data.get("status") and data["status"] not in db.STATUSES:
        raise HTTPException(status_code=400, detail=f"Unknown status: {data['status']}")
    if data.get("masthead") and data["masthead"] not in mastheads.BY_SITE:
        raise HTTPException(status_code=400, detail=f"Unknown masthead: {data['masthead']}")
    if data:
        db.update_business(business_id, data)
        db.log_activity(business_id, "updated", ", ".join(sorted(data)))
        if "email" in data or "industry" in data:
            prospect.reenrich_score_only(business_id)
    return JSONResponse(db.get_business(business_id) or {})


@app.post("/api/websites/verify")
def api_verify_websites(limit: int = 25, recheck: bool = False) -> JSONResponse:
    """
    Check a batch of business websites.

    Batched so the request finishes well inside a hosting proxy's timeout —
    the response carries `remaining` and the caller loops until it is zero.
    """
    return JSONResponse(prospect.verify_websites(limit=min(limit, 50), recheck=recheck))


@app.post("/api/sample/purge")
def api_purge_sample() -> JSONResponse:
    """
    Delete every fictional business.

    The sample source is off by default now, but records created before that
    stay until something removes them — and a database that mixes invented
    companies with real prospects is worse than either alone.
    """
    backup.make("before-sample-purge")
    removed, batch = db.delete_sample_businesses()
    log.info("purged %s sample businesses (batch %s)", removed, batch)
    return JSONResponse({"removed": removed, "batch": batch,
                         "remaining": db.stats()["total"]})


@app.post("/api/businesses/{business_id}/delete")
def api_delete_business(business_id: int) -> JSONResponse:
    if not db.get_business(business_id):
        raise HTTPException(status_code=404, detail="No such business")
    batch = db.delete_business(business_id)
    return JSONResponse({"deleted": business_id, "batch": batch})


@app.post("/api/enrich/missing")
def api_enrich_missing(limit: int = 12, recheck: bool = False) -> JSONResponse:
    """
    Re-check a batch of businesses that have a website but still no email.

    Each one costs several page fetches, so this is batched and reports
    `remaining` for the caller to loop on.
    """
    return JSONResponse(prospect.enrich_missing(limit=min(limit, 25), recheck=recheck))


class ReplyNote(BaseModel):
    note: str = ""


@app.post("/api/businesses/{business_id}/replied")
def api_log_reply(business_id: int, payload: ReplyNote) -> JSONResponse:
    """Record that a prospect replied, and move them to `replied`."""
    result = outreach.log_reply(business_id, payload.note)
    if result is None:
        raise HTTPException(status_code=404, detail="No such business")
    return JSONResponse(result)


@app.post("/api/businesses/{business_id}/enrich")
def api_enrich(business_id: int) -> JSONResponse:
    result = prospect.reenrich(business_id)
    if result is None:
        raise HTTPException(status_code=404, detail="No such business")
    return JSONResponse(result)


class DraftRequest(BaseModel):
    campaign_id: int | None = None
    contact_id: int | None = None
    use_ai: bool = True


@app.post("/api/businesses/{business_id}/draft")
def api_draft(business_id: int, req: DraftRequest) -> JSONResponse:
    try:
        message = outreach.draft_message(
            business_id, campaign_id=req.campaign_id,
            contact_id=req.contact_id, use_ai=req.use_ai,
        )
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    return JSONResponse(message)


class MessageEdit(BaseModel):
    subject: str
    body: str


class SendBatch(BaseModel):
    limit: int = 10


@app.post("/api/messages/send-approved")
def api_send_approved(request: Request, body: SendBatch) -> JSONResponse:
    """Send the approved queue, never past the daily cap."""
    return JSONResponse(outreach.send_approved(
        limit=max(1, min(body.limit, 25)), base_url=str(request.base_url)))


@app.post("/api/messages/{message_id}")
def api_edit_message(message_id: int, edit: MessageEdit) -> JSONResponse:
    """Save changes to a draft."""
    try:
        return JSONResponse(outreach.edit_message(message_id, edit.subject, edit.body))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e


@app.post("/api/messages/{message_id}/approve")
def api_approve(message_id: int) -> JSONResponse:
    message = outreach.approve_message(message_id)
    if not message:
        raise HTTPException(status_code=404, detail="No such message")
    return JSONResponse(message)


@app.post("/api/messages/{message_id}/send")
def api_send(message_id: int, request: Request) -> JSONResponse:
    return JSONResponse(outreach.send_message(message_id, str(request.base_url)))


@app.get("/api/messages/{message_id}/preview")
def api_message_preview(request: Request, message_id: int) -> JSONResponse:
    """Exactly what would leave the building, assembled but not sent."""
    result = outreach.preview(message_id, base_url=str(request.base_url))
    if not result:
        raise HTTPException(status_code=404, detail="No such message")
    return JSONResponse(result)


@app.get("/api/export.csv")
def api_export(q: str = "", status: str = "", region: str = "", industry: str = "",
               min_score: str = "", min_rating: str = "", has_email: str = "",
               sort: str = "score") -> StreamingResponse:
    rows, _ = db.list_businesses(q=q, status=status, region=region, industry=industry,
                                 min_score=_int_param(min_score),
                                 min_rating=_float_param(min_rating),
                                 has_email={"1": True, "0": False}.get(has_email),
                                 sort=sort, limit=100000)
    columns = ["id", "name", "website", "email", "phone", "address", "suburb", "state",
               "postcode", "region", "industry", "size_band", "rating", "review_count",
               "fit_score", "status", "source", "linkedin", "facebook", "instagram",
               "do_not_contact", "last_contacted_at", "created_at"]

    def generate():
        buffer = io.StringIO()
        writer = csv.DictWriter(buffer, fieldnames=columns, extrasaction="ignore")
        writer.writeheader()
        yield buffer.getvalue()
        for row in rows:
            buffer.seek(0)
            buffer.truncate(0)
            writer.writerow(row)
            yield buffer.getvalue()

    return StreamingResponse(
        generate(), media_type="text/csv",
        headers={"Content-Disposition": 'attachment; filename="pearch-outreach.csv"'},
    )


@app.get("/api/stats")
def api_stats() -> JSONResponse:
    return JSONResponse(db.stats())


if __name__ == "__main__":  # pragma: no cover
    import uvicorn
    uvicorn.run("app:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")), reload=True)
