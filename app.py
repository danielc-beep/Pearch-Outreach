"""
Pearch Outreach — web app.

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
from fastapi.responses import (HTMLResponse, JSONResponse, RedirectResponse,
                               StreamingResponse)
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from markupsafe import Markup
from pydantic import BaseModel, Field

import db
import demo
import outreach
import prospect
import sources
from auth import PasswordMiddleware
from config import (ANTHROPIC_API_KEY, APP_NAME, APP_PASSWORD, APP_TAGLINE,
                    DAILY_SEND_CAP, SEND_ENABLED, STATIC_DIR, TEMPLATES_DIR)
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


templates.env.globals.update(
    score_widget=score_widget,
    app_name=APP_NAME,
    tagline=APP_TAGLINE,
    send_enabled=SEND_ENABLED,
    password_set=bool(APP_PASSWORD),
    ai_available=bool(ANTHROPIC_API_KEY),
    statuses=db.STATUSES,
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
    )


@app.get("/businesses", response_class=HTMLResponse)
def businesses(request: Request, q: str = "", status: str = "", region: str = "",
               state: str = "", industry: str = "", source: str = "",
               has_email: str = "", website_status: str = "", min_score: str = "",
               sort: str = "score", page_no: str = "1") -> HTMLResponse:
    min_score_value = _int_param(min_score)
    page_no = max(1, _int_param(page_no, 1))
    rows, total = db.list_businesses(
        q=q, status=status, region=region, state=state, industry=industry, source=source,
        has_email={"1": True, "0": False}.get(has_email),
        website_status=website_status,
        min_score=min_score_value, sort=sort,
        limit=PAGE_SIZE, offset=(page_no - 1) * PAGE_SIZE,
    )
    # Keep the raw string in the filter dict so the form redisplays what was
    # typed, rather than replacing an empty box with a 0.
    filters = {"q": q, "status": status, "region": region, "state": state,
               "industry": industry, "source": source, "has_email": has_email,
               "website_status": website_status, "min_score": min_score, "sort": sort}
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
    )


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


@app.post("/api/prospect/run")
def api_prospect_run(req: ProspectRequest) -> JSONResponse:
    query = {"industry": req.industry, "location": req.location,
             "limit": req.limit, "csv": req.csv}
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
                   min_score: str = "", sort: str = "score", limit: int = 50,
                   offset: int = 0) -> JSONResponse:
    rows, total = db.list_businesses(q=q, status=status, region=region, industry=industry,
                                     min_score=_int_param(min_score), sort=sort,
                                     limit=min(limit, 500), offset=offset)
    return JSONResponse({"total": total, "businesses": rows})


class BusinessPatch(BaseModel):
    status: str | None = None
    email: str | None = None
    phone: str | None = None
    notes: str | None = None
    industry: str | None = None
    do_not_contact: int | None = None


@app.post("/api/businesses/{business_id}")
def api_update_business(business_id: int, patch: BusinessPatch) -> JSONResponse:
    if not db.get_business(business_id):
        raise HTTPException(status_code=404, detail="No such business")
    data = {k: v for k, v in patch.model_dump().items() if v is not None}
    if data.get("status") and data["status"] not in db.STATUSES:
        raise HTTPException(status_code=400, detail=f"Unknown status: {data['status']}")
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
    removed = db.delete_sample_businesses()
    log.info("purged %s sample businesses", removed)
    return JSONResponse({"removed": removed, "remaining": db.stats()["total"]})


@app.post("/api/businesses/{business_id}/delete")
def api_delete_business(business_id: int) -> JSONResponse:
    if not db.get_business(business_id):
        raise HTTPException(status_code=404, detail="No such business")
    db.delete_business(business_id)
    return JSONResponse({"deleted": business_id})


@app.post("/api/enrich/missing")
def api_enrich_missing(limit: int = 20, recheck: bool = False) -> JSONResponse:
    """
    Re-check a batch of businesses that have a website but still no email.

    Each one costs several page fetches, so this is batched and reports
    `remaining` for the caller to loop on.
    """
    return JSONResponse(prospect.enrich_missing(limit=min(limit, 40), recheck=recheck))


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


@app.get("/api/export.csv")
def api_export(q: str = "", status: str = "", region: str = "", industry: str = "",
               min_score: str = "", has_email: str = "", sort: str = "score") -> StreamingResponse:
    rows, _ = db.list_businesses(q=q, status=status, region=region, industry=industry,
                                 min_score=_int_param(min_score),
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
