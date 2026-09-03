"""
Explorium source, tested against a mocked API.

The live API is unreachable from the build environment, so these tests pin
the contract we send and prove the response mapping tolerates the field-name
variants in CANDIDATES. They cannot prove Explorium's real field names —
scripts/probe_explorium.py does that, once against a live key.
"""
from __future__ import annotations

import json

import httpx
import pytest

import sources.explorium as ex


def _mock(handler):
    transport = httpx.MockTransport(handler)
    original = httpx.Client
    ex.httpx.Client = lambda *a, **kw: original(*a, **{**kw, "transport": transport})
    return original


@pytest.fixture(autouse=True)
def restore():
    original = httpx.Client
    yield
    ex.httpx.Client = original


@pytest.fixture
def keyed(monkeypatch):
    monkeypatch.setattr(ex, "EXPLORIUM_API_KEY", "test-key")


# Explorium's documented spelling, as the MCP surfaces it.
PREFIXED = {
    "business_id": "abc123",
    "business_name": "It's Simple Finance",
    "business_domain": "itssimple.com.au",
    "business_website": "itssimple.com.au",
    "business_city_name": "sydney",
    "business_region": "new south wales",
    "business_country_name": "australia",
    "business_number_of_employees_range": "11-50",
    "business_yearly_revenue_range": "5M-10M",
    "business_naics_description": "Mortgage and Nonmortgage Loan Brokers",
    "business_sic_code_description": "Loan brokers",
    "business_business_description": "We are lending specialists helping Australians secure property.",
}

# The same record under bare key names, which the API may use instead.
BARE = {
    "id": "abc123",
    "name": "It's Simple Finance",
    "domain": "itssimple.com.au",
    "website": "itssimple.com.au",
    "city": "sydney",
    "region": "new south wales",
    "number_of_employees_range": "11-50",
    "yearly_revenue_range": "5M-10M",
    "naics_description": "Mortgage and Nonmortgage Loan Brokers",
    "sic_code_description": "Loan brokers",
    "description": "We are lending specialists helping Australians secure property.",
}


def test_unavailable_without_a_key(monkeypatch):
    monkeypatch.setattr(ex, "EXPLORIUM_API_KEY", "")
    ok, reason = ex.available()
    assert ok is False and "EXPLORIUM_API_KEY" in reason
    with pytest.raises(RuntimeError):
        ex.search({"category": "loan brokers"})


@pytest.mark.parametrize("raw", [PREFIXED, BARE], ids=["prefixed-keys", "bare-keys"])
def test_maps_either_key_spelling(keyed, raw):
    _mock(lambda request: httpx.Response(200, json={"data": [raw]}))
    rows = ex.search({"category": "loan brokers", "state": "NSW", "limit": 5})

    assert len(rows) == 1
    row = rows[0]
    assert row["name"] == "It's Simple Finance"
    assert row["domain"] == "itssimple.com.au"
    assert row["website"] == "https://itssimple.com.au"
    assert row["suburb"] == "Sydney"
    assert row["state"] == "NSW"                       # mapped from "new south wales"
    assert row["industry"] == "Mortgage and Nonmortgage Loan Brokers"
    assert row["size_band"] == "11-50"
    assert "5M-10M" in row["notes"]                    # revenue band kept as a note
    assert row["source_ref"] == "abc123"


def test_sends_the_key_and_the_documented_body(keyed):
    seen: list[dict] = []

    def handler(request):
        seen.append({"body": json.loads(request.content), "headers": dict(request.headers)})
        return httpx.Response(200, json={"data": [PREFIXED]})

    _mock(handler)
    ex.search({"category": "real estate", "state": "VIC", "size": "11-50", "limit": 10})

    assert seen[0]["headers"]["api_key"] == "test-key"
    body = seen[0]["body"]
    assert body["filters"]["linkedin_category"] == {"values": ["real estate"]}
    assert body["filters"]["region_country_code"] == {"values": ["AU-VIC"]}
    assert body["filters"]["company_size"] == {"values": ["11-50"]}
    assert body["page_size"] == 10


def test_state_and_country_filters_are_mutually_exclusive(keyed):
    """Explorium rejects a request carrying both."""
    seen: list[dict] = []

    def handler(request):
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"data": [PREFIXED]})

    _mock(handler)

    ex.search({"category": "real estate", "state": "NSW", "limit": 5})
    assert "country_code" not in seen[0]["filters"]
    assert "region_country_code" in seen[0]["filters"]

    ex.search({"category": "real estate", "state": "", "limit": 5})
    assert seen[1]["filters"]["country_code"] == {"values": ["AU"]}
    assert "region_country_code" not in seen[1]["filters"]


def test_pages_until_the_limit_is_met(keyed):
    seen: list[dict] = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        page = body["page"]
        return httpx.Response(200, json={"data": [
            dict(PREFIXED, business_id=f"p{page}-{i}", business_domain=f"p{page}x{i}.com.au")
            for i in range(body["page_size"])
        ]})

    _mock(handler)
    rows = ex.search({"category": "loan brokers", "limit": 250})

    # 250 is clamped to MAX_RESULTS, then fetched a page at a time.
    assert len(rows) == ex.MAX_RESULTS
    assert [b["page"] for b in seen] == [1, 2]


def test_a_short_page_ends_the_loop(keyed):
    """Fewer rows than asked for means there are no more to get."""
    seen: list[dict] = []

    def handler(request):
        body = json.loads(request.content)
        seen.append(body)
        count = body["page_size"] if body["page"] == 1 else 3
        return httpx.Response(200, json={"data": [
            dict(PREFIXED, business_id=f"p{body['page']}-{i}",
                 business_domain=f"p{body['page']}x{i}.com.au")
            for i in range(count)
        ]})

    _mock(handler)
    rows = ex.search({"category": "loan brokers", "limit": 200})
    assert len(rows) == 103
    assert [b["page"] for b in seen] == [1, 2]


def test_falls_back_to_v1_when_v2_is_not_there(keyed):
    """The console documents v2, the public reference v1 — try both."""
    tried: list[str] = []

    def handler(request):
        tried.append(str(request.url))
        if "/v2/" in str(request.url):
            return httpx.Response(404, json={"message": "not found"})
        return httpx.Response(200, json={"data": [PREFIXED]})

    _mock(handler)
    rows = ex.search({"category": "loan brokers", "limit": 5})

    assert len(rows) == 1
    assert any("/v2/" in u for u in tried)
    assert any("/v1/" in u for u in tried)


@pytest.mark.parametrize("envelope", ["data", "results", "businesses", "records"])
def test_tolerates_the_result_array_under_any_documented_key(keyed, envelope):
    _mock(lambda request: httpx.Response(200, json={envelope: [PREFIXED]}))
    assert len(ex.search({"category": "loan brokers", "limit": 3})) == 1


def test_a_bare_array_response_works(keyed):
    _mock(lambda request: httpx.Response(200, json=[PREFIXED]))
    assert len(ex.search({"category": "loan brokers", "limit": 3})) == 1


def test_an_unexpected_shape_points_at_the_probe_script(keyed):
    _mock(lambda request: httpx.Response(200, json={"unexpected": {"nested": "thing"}}))
    with pytest.raises(RuntimeError, match="probe_explorium"):
        ex.search({"category": "loan brokers", "limit": 3})


def test_an_api_error_surfaces_the_message(keyed):
    _mock(lambda request: httpx.Response(401, json={"message": "Invalid API key"}))
    with pytest.raises(RuntimeError, match="Invalid API key"):
        ex.search({"category": "loan brokers"})


def test_a_non_json_error_body_does_not_crash(keyed):
    _mock(lambda request: httpx.Response(502, text="<html>Bad Gateway</html>"))
    with pytest.raises(RuntimeError, match="502"):
        ex.search({"category": "loan brokers"})


def test_a_record_without_a_name_is_dropped(keyed):
    _mock(lambda request: httpx.Response(200, json={"data": [PREFIXED, {"business_id": "x"}]}))
    rows = ex.search({"category": "loan brokers", "limit": 10})
    assert [r["source_ref"] for r in rows] == ["abc123"]
