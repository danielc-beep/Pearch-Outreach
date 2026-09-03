#!/usr/bin/env python3
"""
Dump one raw Explorium record, so the field mapping can be confirmed.

sources/explorium.py maps Explorium's response onto our columns through a
table of candidate key names, written from the documented contract rather
than from a live response. This script shows what the API actually returns
and reports which of our columns came out empty — the ones whose candidates
need correcting.

Run it anywhere that has the key and can reach api.explorium.ai:

    EXPLORIUM_API_KEY=... python scripts/probe_explorium.py
    EXPLORIUM_API_KEY=... python scripts/probe_explorium.py "real estate" NSW

Costs one credit.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import httpx  # noqa: E402

import sources.explorium as ex  # noqa: E402


def main() -> int:
    key = os.getenv("EXPLORIUM_API_KEY", "")
    if not key:
        print("Set EXPLORIUM_API_KEY first.")
        return 1

    category = sys.argv[1] if len(sys.argv) > 1 else "loan brokers"
    state = sys.argv[2] if len(sys.argv) > 2 else "NSW"

    filters: dict = {"linkedin_category": {"values": [category]}}
    region = ex.AU_STATES.get(state, "")
    if region:
        filters["region_country_code"] = {"values": [region]}
    else:
        filters["country_code"] = {"values": ["AU"]}

    print(f"filters: {json.dumps(filters)}\n")
    body = {"mode": "full", "page": 1, "page_size": 1, "filters": filters}

    # The console documents v2, the public reference says v1. Try each and
    # report which one answers, so the source can be pinned to it.
    response = None
    for endpoint in ex.ENDPOINTS:
        print(f"POST {endpoint}")
        try:
            response = httpx.post(
                endpoint,
                headers={"Content-Type": "application/json", "api_key": key},
                json=body,
                timeout=30.0,
            )
        except httpx.HTTPError as e:
            print(f"  could not reach it: {e}")
            continue
        print(f"  HTTP {response.status_code}")
        if response.status_code == 404:
            print("  not this one, trying the next")
            continue
        print(f"\n>>> This is the live endpoint: {endpoint}\n")
        break
    else:
        print("\nNeither endpoint answered. Check the key and your network.")
        return 1

    if response is None or response.status_code >= 400:
        print(response.text[:1500] if response is not None else "no response")
        return 1

    payload = response.json()
    print("--- top-level keys ---")
    print(list(payload) if isinstance(payload, dict) else f"(a bare {type(payload).__name__})")

    rows = ex._first(payload, ex.RESULT_KEYS) if isinstance(payload, dict) else payload
    if not isinstance(rows, list) or not rows:
        print("\nNo records came back. Full response:\n")
        print(json.dumps(payload, indent=2)[:3000])
        return 1

    raw = rows[0]
    print("\n--- raw record ---")
    print(json.dumps(raw, indent=2)[:3000])

    print("\n--- how it maps onto our columns ---")
    mapped = ex._to_record(raw)
    for column, value in mapped.items():
        mark = "  " if value else "??"
        print(f"{mark} {column:12} {value!r}")

    empty = [c for c, v in mapped.items() if not v]
    if empty:
        print(
            f"\nEmpty columns: {', '.join(empty)}."
            "\nFind the right key in the raw record above and add it to"
            "\nCANDIDATES in sources/explorium.py."
        )
    else:
        print("\nEvery column mapped. The candidate table is correct.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
