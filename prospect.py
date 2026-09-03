"""
The prospecting pipeline.

    source.search(query)  →  normalise  →  score  →  dedupe/upsert  →  log

Every source goes through exactly this path, so a record from Google Places
and a record pasted from a CSV end up with the same shape, the same region
mapping, and the same fit score.
"""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from typing import Any

import db
import sources
from config import region_for_postcode
import enrich
from enrich import enrich_from_website, guess_industry
from scoring import apply_score
from util import clean_email, clean_phone, domain_of, normalise_url, parse_address, truncate

log = logging.getLogger(__name__)

# Enrichment fetches two pages per business, so it is the slow part of a run.
# Cap how many we do inline; the rest can be enriched later from the UI.
MAX_INLINE_ENRICH = 25

# Enrichment is almost entirely waiting on other people's servers, so more
# workers costs little and finishes far sooner.
ENRICH_WORKERS = 16


def normalise(record: dict[str, Any]) -> dict[str, Any]:
    """Coerce a raw source record into our column shape."""
    out = dict(record)

    out["name"] = (out.get("name") or "").strip()
    out["website"] = normalise_url(out.get("website"))
    out["domain"] = out.get("domain") or domain_of(out["website"])
    out["email"] = clean_email(out.get("email"))
    out["phone"] = clean_phone(out.get("phone"))

    # Fill suburb/state/postcode from a one-line address when missing.
    if out.get("address") and not (out.get("postcode") and out.get("state")):
        parsed = parse_address(out["address"])
        for key, value in parsed.items():
            if value and not out.get(key):
                out[key] = value

    state, region = region_for_postcode(out.get("postcode"))
    if state and not out.get("state"):
        out["state"] = state
    if region and not out.get("region"):
        out["region"] = region

    if not out.get("industry"):
        guess = guess_industry(out.get("category"), out.get("name"), out.get("description"))
        if guess:
            out["industry"] = guess

    if out.get("description"):
        out["description"] = truncate(out["description"])
    if out.get("rating") is not None:
        try:
            out["rating"] = round(float(out["rating"]), 1)
        except (TypeError, ValueError):
            out["rating"] = None
    if out.get("review_count") is not None:
        try:
            out["review_count"] = int(out["review_count"])
        except (TypeError, ValueError):
            out["review_count"] = None

    return out


def enrich_record(record: dict[str, Any]) -> dict[str, Any]:
    """Visit the website and merge anything new. Never overwrites known values."""
    if not record.get("website"):
        return record
    try:
        found = enrich_from_website(record["website"])
    except Exception as e:                      # a hostile site is not our problem
        log.debug("enrich failed for %s: %s", record.get("website"), e)
        return record
    # website_status is the point of the visit as much as the email is, so it
    # is merged even when the fetch failed and everything else came back empty.
    if found.get("website_status"):
        record["website_status"] = found["website_status"]
    error = found.pop("enrich_error", None)
    note = found.pop("enrich_note", "")
    if error:
        # A site that could not be fetched has still been attempted; without
        # this the record is picked again by every subsequent batch.
        record["enriched_at"] = db.now()
        record["_enrich_note"] = error
    contacts = found.pop("all_emails", [])
    if note:
        record["_enrich_note"] = note
    for key, value in found.items():
        if value and not record.get(key):
            record[key] = value
    if contacts:
        record["_extra_emails"] = [e for e in contacts if e != record.get("email")][:4]
    record["enriched_at"] = db.now()
    return record


def run(source_key: str, query: dict[str, Any], *, enrich: bool = True) -> dict[str, Any]:
    """
    Execute one prospecting run and write the results to the database.

    Returns a summary dict: counts plus the businesses touched, so the UI can
    show what just landed without a second query.
    """
    info = sources.get_source(source_key)
    if not info.available:
        raise RuntimeError(info.unavailable_reason)

    run_id = db.start_run(source_key, query)
    try:
        raw = info.search(query)
    except Exception as e:
        db.finish_run(run_id, found=0, new=0, dupes=0, status="error", error=str(e))
        raise

    records = [normalise(r) for r in raw]
    records = [r for r in records if r["name"]]

    if enrich:
        targets = [r for r in records if r.get("website") and not r.get("email")][:MAX_INLINE_ENRICH]
        if targets:
            with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as pool:
                enriched = list(pool.map(enrich_record, targets))
            by_name = {id(t): e for t, e in zip(targets, enriched)}
            records = [by_name.get(id(r), r) for r in records]

    new_count = dupe_count = 0
    touched: list[dict[str, Any]] = []
    for record in records:
        extra_emails = record.pop("_extra_emails", [])
        record["source"] = source_key
        apply_score(record)
        business_id, created = db.upsert_business(record)
        if created:
            new_count += 1
            db.log_activity(business_id, "created", f"Found via {info.label}")
        else:
            dupe_count += 1
            # A re-run can turn up detail we didn't have; rescore on what's stored now.
            stored = db.get_business(business_id) or {}
            merged = apply_score({**stored})
            db.update_business(business_id, {
                "fit_score": merged["fit_score"],
                "score_reasons": merged["score_reasons"],
            })
        for email in extra_emails:
            db.add_contact(business_id, {"email": email, "source": "website"})
        touched.append(db.get_business(business_id) or {})

    db.finish_run(run_id, found=len(records), new=new_count, dupes=dupe_count,
                  result_ids=[int(b['id']) for b in touched if b.get('id')])
    return {
        "run_id": run_id,
        "source": source_key,
        "source_label": info.label,
        "found": len(records),
        "new": new_count,
        "duplicates": dupe_count,
        "businesses": sorted(touched, key=lambda b: b.get("fit_score", 0), reverse=True),
    }


def reenrich(business_id: int) -> dict[str, Any] | None:
    """Re-run enrichment + scoring for a single stored business."""
    business = db.get_business(business_id)
    if not business:
        return None
    enriched = enrich_record(dict(business))
    extra_emails = enriched.pop("_extra_emails", [])
    note = enriched.pop("_enrich_note", "")
    apply_score(enriched)
    db.update_business(business_id, enriched)
    for email in extra_emails:
        db.add_contact(business_id, {"email": email, "source": "website"})
    db.log_activity(business_id, "enriched",
                    note or f"Found {enriched.get('email')} on the website")
    return db.get_business(business_id)


def enrich_missing(limit: int = 20, recheck: bool = False) -> dict[str, Any]:
    """
    Visit the websites of businesses with no email on file, looking for one.

    Batched, because each business costs several page fetches and a big batch
    outlives a hosting proxy's patience. Highest-scoring first, so the most
    useful addresses arrive in the first batch rather than the last.
    """
    targets, outstanding = db.businesses_needing_enrichment(limit, recheck=recheck)
    if not targets:
        return {"checked": 0, "found": 0, "remaining": 0, "businesses": []}

    with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as pool:
        enriched = list(pool.map(lambda b: enrich_record(dict(b)), targets))

    found = 0
    updated: list[dict[str, Any]] = []
    for record in enriched:
        business_id = int(record["id"])
        extra_emails = record.pop("_extra_emails", [])
        note = record.pop("_enrich_note", "")
        apply_score(record)
        db.update_business(business_id, record)
        for email in extra_emails:
            db.add_contact(business_id, {"email": email, "source": "website"})
        if record.get("email"):
            found += 1
            db.log_activity(business_id, "enriched", f"Found {record['email']} on the website")
        elif note:
            db.log_activity(business_id, "enriched", note)
        updated.append(db.get_business(business_id) or {})

    log.info("enrich_missing checked %s, found %s emails", len(targets), found)
    return {"checked": len(targets), "found": found,
            "remaining": max(0, outstanding - len(targets)), "businesses": updated}


def rescore_all() -> int:
    """Rescore every business — run this after editing the ICP in config.py."""
    count = 0
    for business in list(db.iter_all()):
        scored = apply_score(dict(business))
        db.update_business(int(business["id"]), {
            "fit_score": scored["fit_score"],
            "score_reasons": scored["score_reasons"],
        })
        count += 1
    return count


def verify_websites(limit: int = 25, recheck: bool = False) -> dict[str, Any]:
    """
    Check that each business's website actually serves a page.

    A prospect whose website does not resolve is not a prospect — the record is
    stale, the domain is dead, or it was never real.

    Deliberately batched. Visiting a few hundred sites takes minutes, and a
    hosting proxy will cut the request off long before that and answer with an
    HTML error page — so this does a bounded batch and reports how many remain,
    and the caller loops. Progress survives an interruption because each batch
    commits before returning.
    """
    if recheck:
        rows, _ = db.list_businesses(limit=limit)
        targets = [b for b in rows if b.get("website")]
        outstanding = 0
    else:
        targets, outstanding = db.businesses_needing_website_check(limit)

    if not targets:
        return {"checked": 0, "live": 0, "unreachable": 0, "remaining": 0}

    with ThreadPoolExecutor(max_workers=ENRICH_WORKERS) as pool:
        statuses = list(pool.map(lambda b: enrich.website_is_live(b["website"]), targets))

    live = unreachable = 0
    for business, status in zip(targets, statuses):
        if not status:
            continue
        db.update_business(int(business["id"]), {"website_status": status})
        if status == "live":
            live += 1
        else:
            unreachable += 1
            db.log_activity(int(business["id"]), "verified",
                            f"{business['website']} did not respond")

    log.info("verified %s websites: %s live, %s unreachable",
             len(targets), live, unreachable)
    return {"checked": len(targets), "live": live, "unreachable": unreachable,
            "remaining": max(0, outstanding - len(targets))}


def reenrich_score_only(business_id: int) -> dict[str, Any] | None:
    """Rescore one business from what's already stored — no HTTP, no waiting."""
    business = db.get_business(business_id)
    if not business:
        return None
    scored = apply_score(dict(business))
    db.update_business(business_id, {
        "fit_score": scored["fit_score"],
        "score_reasons": scored["score_reasons"],
    })
    return db.get_business(business_id)
