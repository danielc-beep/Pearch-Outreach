"""
What counts as a live website, and what a slow one may cost.

Both rules here come from production behaviour. Real, working Australian
businesses were being marked dead, and a batch that ran long recorded
nothing at all.
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


@pytest.mark.parametrize("status", [200, 201, 301, 302])
def test_a_normal_response_is_live(monkeypatch, status):
    _client(monkeypatch, lambda r: httpx.Response(status, text="hi"))
    assert enrich.website_is_live("https://realbusiness.com.au") == "live"


@pytest.mark.parametrize("status", [401, 403, 404, 405, 429, 500, 503])
def test_a_server_that_answers_at_all_is_live(monkeypatch, status):
    """
    The question is whether the business exists, not whether its homepage is
    happy. A Cloudflare 403 at a datacentre IP is the commonest answer a real
    Australian small-business site gives us, and calling that dead is what put
    working businesses behind a "site dead" badge.
    """
    _client(monkeypatch, lambda r: httpx.Response(status, text=""))
    assert enrich.website_is_live("https://behind-cloudflare.com.au") == "live"


def test_no_dns_is_unreachable(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("Name or service not known")
    _client(monkeypatch, handler)
    assert enrich.website_is_live("https://not-a-real-domain-xyzq.com.au") == "unreachable"


def test_a_timeout_is_unreachable_only_after_a_retry(monkeypatch):
    attempts = []

    def handler(request):
        attempts.append(str(request.url))
        raise httpx.ConnectTimeout("too slow")
    _client(monkeypatch, handler)

    assert enrich.website_is_live("https://slow.com.au") == "unreachable"
    # Two tries on the URL given, then two on the www variant.
    assert len(attempts) == 4


def test_a_www_variant_is_tried_when_the_apex_fails(monkeypatch):
    def handler(request):
        if request.url.host.startswith("www."):
            return httpx.Response(200, text="hi")
        raise httpx.ConnectError("no apex record")
    _client(monkeypatch, handler)
    assert enrich.website_is_live("https://apexless.com.au") == "live"


def test_no_url_is_neither(monkeypatch):
    assert enrich.website_is_live("") == ""
    assert enrich.website_is_live(None) == ""


def test_the_timeouts_are_set_for_a_server_outside_australia():
    # Seven seconds passed locally and failed in production.
    assert enrich.TIMEOUT.read >= 15
    assert enrich.TIMEOUT.connect >= 8
    # And one business cannot spend the whole batch.
    assert enrich.PAGE_BUDGET <= 20
