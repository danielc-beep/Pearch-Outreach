"""
Email capture, the part of the pipeline that decides whether a prospect is
usable at all. Each case is a way real Australian small-business sites hide or
publish an address.
"""
from __future__ import annotations

import httpx
import pytest

import enrich


def _serve(pages: dict[str, str], monkeypatch, status: int = 200):
    # httpx reports the root as "/", so accept either spelling for it.
    routes = {(path or "/"): body for path, body in pages.items()}

    def handler(request):
        body = routes.get(request.url.path or "/")
        if body is None:
            return httpx.Response(404)
        return httpx.Response(status, text=body, headers={"content-type": "text/html"})
    transport = httpx.MockTransport(handler)
    original = httpx.Client
    monkeypatch.setattr(enrich.httpx, "Client",
                        lambda *a, **kw: original(*a, **{**kw, "transport": transport}))


def test_structured_data_email_is_found_when_the_page_shows_only_a_form(monkeypatch):
    """The WordPress case: a contact form on screen, the address in JSON-LD."""
    _serve({"": '''<html><head><script type="application/ld+json">
        {"@context":"https://schema.org","@type":"LocalBusiness","name":"Cars Connect",
         "email":"mailto:sales@carsconnect.com.au"}</script></head>
        <body><form>Send us a message</form></body></html>'''}, monkeypatch)

    result = enrich.enrich_from_website("https://carsconnect.com.au")
    assert result["email"] == "sales@carsconnect.com.au"


def test_an_address_on_the_businesss_own_domain_wins(monkeypatch):
    """A webmaster's gmail in the footer must not beat the real inbox."""
    _serve({"": '''<html><body>
        built by webdev@gmail.com — enquiries: info@cardiffselectcars.com.au
        </body></html>'''}, monkeypatch)

    result = enrich.enrich_from_website("https://cardiffselectcars.com.au")
    assert result["email"] == "info@cardiffselectcars.com.au"


def test_a_contact_page_is_followed_when_the_homepage_has_nothing(monkeypatch):
    _serve({
        "": '<html><body><a href="/contact-us">Contact us</a></body></html>',
        "/contact-us": '<html><body>admin@newcastleautotech.com.au</body></html>',
    }, monkeypatch)

    result = enrich.enrich_from_website("https://newcastleautotech.com.au")
    assert result["email"] == "admin@newcastleautotech.com.au"


def test_a_blocked_site_says_so_rather_than_reporting_no_address(monkeypatch):
    """403 and "nothing published" are different problems with different fixes."""
    _serve({"": "<html><body>go away</body></html>"}, monkeypatch, status=403)

    result = enrich.enrich_from_website("https://blocked.com.au")
    assert "refused the request" in result.get("enrich_note", "") \
        or result.get("enrich_error")


def test_a_site_with_genuinely_no_address_says_that_instead(monkeypatch):
    _serve({"": "<html><body>Call us on (02) 4979 5000</body></html>"}, monkeypatch)

    result = enrich.enrich_from_website("https://nothing.com.au")
    assert "email" not in result
    assert "No email published" in result["enrich_note"]
    assert result["phone"] == "02 4979 5000"      # the visit still earned something


@pytest.mark.parametrize("markup,expected", [
    ('<a href="mailto:info@acme.com.au">Email</a>', "info@acme.com.au"),
    ("&#105;&#110;&#102;&#111;&#64;acme.com.au", "info@acme.com.au"),
    ("info [at] acme [dot] com [dot] au", "info@acme.com.au"),
], ids=["mailto", "html-entities", "spelled-out"])
def test_every_obfuscation_still_yields_the_address(markup, expected, monkeypatch):
    _serve({"": f"<html><body>{markup}</body></html>"}, monkeypatch)
    assert enrich.enrich_from_website("https://acme.com.au")["email"] == expected
