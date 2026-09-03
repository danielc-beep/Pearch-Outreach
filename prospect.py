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
from enrich import enrich_from_website, guess_industry
from scoring import apply_score
from util import clean_email, clean_phone, domain_of, normalise_url, parse_address, truncate

log = logging.getLogger(__name__)

# Enrichment fetches two pages per business, so it is the slow part of a run.
# Cap how many we do inline; the rest can be enriched later from the UI.
MAX_INLINE_ENRICH = 25


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
    found.pop("enrich_error", None)
    contacts = found.pop("all_emails", [])
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
            with ThreadPoolExecutor(max_workers=8) as pool:
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
    apply_score(enriched)
    db.update_business(business_id, enriched)
    for email in extra_emails:
        db.add_contact(business_id, {"email": email, "source": "website"})
    db.log_activity(business_id, "enriched", "Re-checked the website for contact details")
    return db.get_business(business_id)


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
