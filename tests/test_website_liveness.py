"""
What a website check may honestly conclude.

Every rule here comes from production. Real, working Australian businesses
were being marked dead — an electrician with a live site, a phone number and
an email in its header sat in the database behind a "site dead" badge, with a
scoring penalty on top and an offer to delete it. The reason was that every
way of failing to read a page collapsed into one answer.

So there are four answers now, and only one of them is evidence against a
business:

    live         we read a page
    blocked      a server answered and would not let us read it
    unreachable  the name does not resolve — nobody is there
    error        we could not tell today
"""
from __future__ import annotations

import httpx
import pytest

import enrich


def _client(monkeypatch, handler):
    transport = httpx.MockTransport(handler)
    original = httpx.Client
    monkeypatch.setattr(enrich.httpx, "Client",
                        lambda *a, **kw: original(*a, **{**kw, "transport": transport}))


def _resolves(monkeypatch, answer=True):
    """DNS is the one definite signal, so tests say what it found."""
    monkeypatch.setattr(enrich, "_name_resolves", lambda host: answer)


def _html(status=200, body="<html>hi</html>"):
    return httpx.Response(status, text=body, headers={"content-type": "text/html"})


@pytest.mark.parametrize("status", [200, 201])
def test_a_page_we_can_read_is_live(monkeypatch, status):
    _client(monkeypatch, lambda r: _html(status))
    assert enrich.website_is_live("https://realbusiness.com.au") == "live"


@pytest.mark.parametrize("status", [401, 403, 405, 429, 500, 503])
def test_a_server_that_refuses_us_is_not_a_dead_business(monkeypatch, status):
    """
    The commonest answer a real Australian small-business site gives a
    datacentre IP is a Cloudflare 403. Calling that dead is the bug: it says
    something about their firewall and nothing about their business.
    """
    _client(monkeypatch, lambda r: httpx.Response(status, text=""))
    _resolves(monkeypatch)
    assert enrich.website_is_live("https://behind-cloudflare.com.au") == "blocked"


def test_a_page_that_is_not_html_still_proves_somebody_is_there(monkeypatch):
    _client(monkeypatch, lambda r: httpx.Response(200, text="{}", headers={
        "content-type": "application/json"}))
    _resolves(monkeypatch)
    assert enrich.website_is_live("https://api-only.com.au") == "blocked"


def test_only_a_name_that_does_not_resolve_is_unreachable(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("Name or service not known")
    _client(monkeypatch, handler)
    _resolves(monkeypatch, False)
    assert enrich.website_is_live("https://not-a-real-domain-xyzq.com.au") == "unreachable"


def test_a_timeout_on_a_domain_that_resolves_is_not_a_finding(monkeypatch):
    """
    A slow host, a firewall dropping packets, a proxy in the way. None of that
    is the business's fault and none of it is evidence, so the check says it
    does not know rather than writing something down.
    """
    attempts = []

    def handler(request):
        attempts.append(str(request.url))
        raise httpx.ConnectTimeout("too slow")
    _client(monkeypatch, handler)
    _resolves(monkeypatch)

    assert enrich.website_is_live("https://slow.com.au") == "error"
    # Two tries on the URL given, then two on the www variant.
    assert len(attempts) == 4


def test_a_www_variant_is_tried_when_the_apex_fails(monkeypatch):
    def handler(request):
        if request.url.host.startswith("www."):
            return _html()
        raise httpx.ConnectError("no apex record")
    _client(monkeypatch, handler)
    _resolves(monkeypatch)
    assert enrich.website_is_live("https://apexless.com.au") == "live"


def test_the_www_variant_is_tried_even_after_the_apex_answers_badly(monkeypatch):
    """One host 403s and the other serves the site — commoner than it sounds."""
    def handler(request):
        if request.url.host.startswith("www."):
            return _html()
        return httpx.Response(403, text="")
    _client(monkeypatch, handler)
    _resolves(monkeypatch)
    assert enrich.website_is_live("https://picky.com.au") == "live"


def test_no_url_is_neither(monkeypatch):
    assert enrich.website_is_live("") == ""
    assert enrich.website_is_live(None) == ""


def test_the_timeouts_are_set_for_a_server_outside_australia():
    # Seven seconds passed locally and failed in production.
    assert enrich.TIMEOUT.read >= 15
    assert enrich.TIMEOUT.connect >= 8
    # And one business cannot spend the whole batch.
    assert enrich.PAGE_BUDGET <= 20


# ---------- What the rest of the app does with the answer ----------

def test_only_an_unresolvable_domain_costs_a_business_points():
    """
    Docking a score for a WAF was marking a working electrician down for
    having a firewall.
    """
    import scoring
    base = {"name": "Acme", "website": "https://acme.com.au", "email": "a@acme.com.au",
            "phone": "0400 000 000", "industry": "electrician", "suburb": "Canberra",
            "rating": 4.8, "review_count": 120, "masthead": "canberratimes.com.au"}
    scores = {status: scoring.score_business({**base, "website_status": status})[0]
              for status in ("live", "blocked", "error", "unreachable")}
    assert scores["live"] == scores["blocked"] == scores["error"]
    assert scores["unreachable"] < scores["live"]


def test_the_scraper_and_the_checker_cannot_disagree(monkeypatch):
    """
    They used to. enrich_from_website called any failure "unreachable" while
    website_is_live called any answer "live", and the one that ran during
    prospecting was the wrong one.
    """
    _client(monkeypatch, lambda r: httpx.Response(403, text=""))
    _resolves(monkeypatch)
    assert enrich.website_is_live("https://waf.com.au") == "blocked"
    assert enrich.enrich_from_website("https://waf.com.au")["website_status"] == "blocked"
