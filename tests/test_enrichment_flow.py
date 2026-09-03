"""
End to end through the scraper: a Cloudflare-protected site that publishes its
address only on /our-team, in the encoding Cloudflare substitutes.

This is the shape of site that returned nothing before — a bot User-Agent got
blocked, the address was encoded, and /our-team was never followed.
"""
import httpx

import enrich

HOME = '''<html><head><title>Hunter Brokers — Newcastle mortgage broker</title>
<meta name="description" content="Newcastle home loan specialists."></head>
<body><a href="/our-team">Meet the team</a>
<a href="https://facebook.com/hunterbrokers">fb</a>
<p>Call (02) 4979 5000</p></body></html>'''

plain, key = "info@hunterbrokers.com.au", 0x2a
CF = format(key, "02x") + "".join(format(ord(c) ^ key, "02x") for c in plain)
TEAM = f'<html><body><a data-cfemail="{CF}" href="/cdn-cgi/l/email-protection">email</a></body></html>'

def _handler_factory(seen):
  def handler(request):
    seen.append((str(request.url), request.headers.get("user-agent", "")[:30]))
    path = request.url.path
    if path in ("", "/"):
        return httpx.Response(200, text=HOME, headers={"content-type": "text/html"})
    if path == "/our-team":
        return httpx.Response(200, text=TEAM, headers={"content-type": "text/html"})
    return httpx.Response(404)
  return handler


def test_scraper_recovers_a_hidden_address(monkeypatch):
    seen: list[tuple[str, str]] = []
    transport = httpx.MockTransport(_handler_factory(seen))
    original = httpx.Client
    monkeypatch.setattr(enrich.httpx, "Client",
                        lambda *a, **kw: original(*a, **{**kw, "transport": transport}))

    result = enrich.enrich_from_website("https://hunterbrokers.com.au")

    assert result["email"] == plain            # Cloudflare encoding reversed
    assert result["phone"] == "02 4979 5000"
    assert result["industry"] == "Home loans"  # guessed from the page text
    assert result["facebook"]
    assert "Newcastle home loan" in result["description"]
    assert result["website_status"] == "live"

    visited = " ".join(url for url, _ in seen)
    assert "/our-team" in visited              # followed a non-"contact" link
    assert seen[0][1].startswith("Mozilla/5.0 (Macintosh")   # presents as a browser


def test_a_site_with_no_address_says_so(monkeypatch):
    def handler(request):
        return httpx.Response(200, text="<html><body>No email here</body></html>",
                              headers={"content-type": "text/html"})
    transport = httpx.MockTransport(handler)
    original = httpx.Client
    monkeypatch.setattr(enrich.httpx, "Client",
                        lambda *a, **kw: original(*a, **{**kw, "transport": transport}))

    result = enrich.enrich_from_website("https://nothinghere.com.au")
    assert "email" not in result
    assert "No email published" in result["enrich_note"]


def test_an_unreachable_site_reports_an_error(monkeypatch):
    def handler(request):
        raise httpx.ConnectError("refused")
    transport = httpx.MockTransport(handler)
    original = httpx.Client
    monkeypatch.setattr(enrich.httpx, "Client",
                        lambda *a, **kw: original(*a, **{**kw, "transport": transport}))

    result = enrich.enrich_from_website("https://down.com.au")
    assert result["enrich_error"] == "could not fetch site"
    # The status is the useful half: a domain that serves nothing is not a
    # prospect, whether it is dead or was never real.
    assert result["website_status"] == "unreachable"
