"""
Demo seeding for a database that boots up empty.

A free Render instance has no persistent disk, so the SQLite file is gone
every time the service redeploys or wakes from sleep. Without this, anyone
opening a shared demo URL would land on an empty dashboard and see nothing
of what the tool does.

Only ever runs when the database has no businesses at all, so it can never
overwrite or duplicate real prospecting work.
"""
from __future__ import annotations

import logging

import db
import prospect
from config import PEARCH_DEMO_SEED

log = logging.getLogger(__name__)

# Three runs across different states and trades, so the dashboard's region and
# industry breakdowns have something to show.
DEMO_RUNS = [
    ("mortgage broker", "Newcastle NSW", 22),
    ("dental", "Bendigo VIC", 14),
    ("builder", "Wollongong NSW", 10),
]


def seed_if_empty() -> int:
    """Fill an empty database with sample businesses. Returns how many added."""
    if not PEARCH_DEMO_SEED:
        return 0
    if db.stats()["total"] > 0:
        return 0

    added = 0
    for industry, location, limit in DEMO_RUNS:
        try:
            # enrich=False: the sample source invents the websites, so there is
            # nothing real to fetch and no reason to make the boot wait on HTTP.
            result = prospect.run(
                "sample",
                {"industry": industry, "location": location, "limit": limit},
                enrich=False,
            )
            added += result["new"]
        except Exception as e:      # a failed seed must never stop the app booting
            log.warning("demo seed failed for %s in %s: %s", industry, location, e)

    # Give the pipeline something other than a single column of "new".
    contactable, _ = db.list_businesses(has_email=True, limit=5)
    for business, status in zip(contactable, ["qualified", "qualified", "qualified",
                                              "researching", "researching"]):
        db.update_business(int(business["id"]), {"status": status})
        db.log_activity(int(business["id"]), "updated", f"status → {status}")

    log.info("demo seed added %s sample businesses", added)
    return added
