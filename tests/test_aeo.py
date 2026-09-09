"""
What an answer engine can make of a business's website.

This is the half of the fit score the product is actually about, so these
tests are mostly about the difference between "we looked and there is nothing
there" and "we could not look" — the distinction this app keeps having to
relearn, and the one that decides whether a real business gets ranked top of
the call list on no evidence.
"""
from __future__ import annotations

import aeo


def page(*, schema="", faq="", blog="", meta=True, words=800, headings=()):
    parts = ["<html><head>"]
    if meta:
        parts.append('<meta name="description" content="' + "A local business. " * 3 + '">')
    if schema:
        parts.append('<script type="application/ld+json">' + schema + "</script>")
    parts.append("</head><body>")
    for h in headings:
        parts.append(f"<h2>{h}</h2>")
    if faq:
        parts.append(f"<h2>{faq}</h2>")
    if blog:
        parts.append(f'<a href="/{blog}">{blog.title()}</a>')
    parts.append("word " * words)
    parts.append("</body></html>")
    return "".join(parts)


def test_it_reads_structured_data_out_of_json_ld():
    html = page(schema='{"@context":"https://schema.org","@type":"Electrician","name":"X"}')
    assert aeo.audit([html])["aeo_schema"] == "electrician"


def test_it_reads_a_graph_of_types_not_just_the_first():
    html = page(schema='{"@graph":[{"@type":"LocalBusiness"},{"@type":["FAQPage","WebPage"]}]}')
    found = aeo.audit([html])["aeo_schema"].split(",")
    assert {"localbusiness", "faqpage", "webpage"} <= set(found)


def test_it_falls_back_to_microdata():
    html = '<div itemtype="https://schema.org/LocalBusiness">x</div>' + page()
    assert "localbusiness" in aeo.audit([html])["aeo_schema"]


def test_broken_structured_data_does_not_sink_the_audit():
    """A half-written JSON-LD block is common and is not a reason to learn nothing."""
    html = page(schema='{"@type": "LocalBusiness",,,}')
    result = aeo.audit([html])
    assert result["aeo_schema"] == ""
    assert result["aeo_words"] > 0


def test_an_faq_is_found_three_ways():
    """Schema, a heading that says so, or questions actually being asked."""
    by_schema = aeo.audit([page(schema='{"@type":"FAQPage"}')])
    by_words = aeo.audit([page(faq="Frequently asked questions")])
    by_shape = aeo.audit([page(headings=("How much does it cost?",
                                         "What areas do you cover?"))])
    assert by_schema["aeo_faq"] == by_words["aeo_faq"] == by_shape["aeo_faq"] == 1
    assert aeo.audit([page()])["aeo_faq"] == 0


def test_one_question_shaped_heading_is_not_an_faq():
    """A heading that happens to end in a question mark is not a Q&A page."""
    assert aeo.audit([page(headings=("Ready to get started?",))])["aeo_faq"] == 0


def test_publishing_is_noticed():
    assert aeo.audit([page(blog="blog")])["aeo_blog"] == 1
    assert aeo.audit([page(blog="news")])["aeo_blog"] == 1
    assert aeo.audit([page()])["aeo_blog"] == 0


def test_a_missing_meta_description_is_noticed():
    assert aeo.audit([page(meta=False)])["aeo_meta"] == 0
    assert aeo.audit([page(meta=True)])["aeo_meta"] == 1


def test_words_are_counted_off_the_text_not_the_markup():
    thin = aeo.audit([page(words=20)])
    assert thin["aeo_words"] < aeo.THIN_SITE_WORDS
    assert aeo.audit([page(words=900)])["aeo_words"] > aeo.THIN_SITE_WORDS


def test_no_pages_is_no_audit():
    assert aeo.audit([]) == {}
    assert aeo.audit(["", None]) == {}


# ---------- What we would say on the phone ----------

def test_the_findings_are_the_reason_to_ring_them():
    audited = {"aeo_audited_at": "t", **aeo.audit([page(meta=False, words=20)])}
    notes = aeo.findings(audited)
    assert any("No structured data" in n for n in notes)
    assert any("No questions answered" in n for n in notes)
    assert any("Nothing published" in n for n in notes)
    assert any("meta description" in n for n in notes)
    assert any("too thin" in n.lower() for n in notes)


def test_a_site_already_in_good_order_gives_nothing_to_open_on():
    good = {"aeo_audited_at": "t", **aeo.audit([page(
        schema='{"@type":"LocalBusiness"}', faq="Frequently asked questions",
        blog="blog", words=2000)])}
    assert aeo.findings(good) == []


def test_schema_that_says_nothing_local_reads_differently_from_none():
    some = {"aeo_audited_at": "t", **aeo.audit([page(schema='{"@type":"WebPage"}')])}
    notes = aeo.findings(some)
    assert any("nothing that says it is a local business" in n for n in notes)
    assert not any("No structured data at all" in n for n in notes)


def test_a_site_we_could_not_read_has_no_findings():
    """
    The rule this app keeps relearning. A stamp saying we went and looked is
    not a reading, and listing five things a blocked site "is missing" would
    put a working business at the top of the call list on nothing at all.
    """
    assert aeo.findings({"aeo_audited_at": "t", "aeo_pages": 0}) == []
    assert aeo.findings({}) == []


# ---------- Getting it onto the records ----------

def test_enrichment_audits_the_pages_it_already_downloaded(monkeypatch):
    """
    The audit has to be free. Enrichment already fetches the homepage and a
    few inside pages looking for an address; reading them a second time for
    AEO signals costs no requests and no extra time.
    """
    import enrich
    import httpx

    html = page(schema='{"@type":"LocalBusiness"}', blog="blog", words=900)
    calls = []

    def handler(request):
        calls.append(str(request.url))
        return httpx.Response(200, text=html, headers={"content-type": "text/html"})

    transport = httpx.MockTransport(handler)
    real_client = httpx.Client
    monkeypatch.setattr(httpx, "Client",
                        lambda **kw: real_client(**{**kw, "transport": transport}))

    found = enrich.enrich_from_website("https://acme.com.au")
    assert found["aeo_schema"] == "localbusiness"
    assert found["aeo_blog"] == 1
    assert found["aeo_audited_at"]


def test_the_sweep_reads_the_backlog_and_finishes(client, monkeypatch):
    """
    Records found before the audit existed carry a guess for half their score.
    The sweep is how that becomes a reading, and like every sweep here it has
    to terminate — including when every site in it refuses to be read.
    """
    import db
    import prospect

    for i in range(5):
        db.insert_business({"name": f"Co {i}", "domain": f"co{i}.com.au",
                            "website": f"https://co{i}.com.au",
                            "website_status": "live", "rating": 4.6,
                            "review_count": 30})
    monkeypatch.setattr(prospect.enrich, "enrich_from_website",
                        lambda url: {"enrich_error": "site blocked"})

    seen, sweep = 0, ""
    for _ in range(10):
        batch = prospect.audit_sites(limit=2, checked_before=sweep)
        seen += batch["checked"]
        sweep = batch["checked_before"]
        if not batch["remaining"]:
            break
    assert seen == 5
    assert prospect.audit_sites(limit=5, checked_before=sweep)["checked"] == 0


def test_the_sweep_does_not_overwrite_a_corrected_address(client, monkeypatch):
    """
    It is on the site for one reason. A rep who fixed an address by hand must
    not have it replaced because an audit happened to visit the same page.
    """
    import db
    import prospect

    bid = db.insert_business({"name": "Co", "domain": "co.com.au",
                              "website": "https://co.com.au", "website_status": "live",
                              "email": "the.right.one@co.com.au", "phone": "02 1111 1111"})
    monkeypatch.setattr(prospect.enrich, "enrich_from_website", lambda url: {
        "email": "info@scraped.com.au", "phone": "03 9999 9999",
        "aeo_audited_at": "t", "aeo_schema": "", "aeo_faq": 0, "aeo_blog": 0,
        "aeo_meta": 0, "aeo_pages": 2, "aeo_words": 90, "aeo_questions": 0})

    prospect.audit_sites(limit=5)
    after = db.get_business(bid)
    assert after["email"] == "the.right.one@co.com.au"
    assert after["phone"] == "02 1111 1111"
    assert after["aeo_pages"] == 2


def test_reading_one_site_moves_its_score(client, monkeypatch):
    import db
    import prospect

    bid = db.insert_business({"name": "Co", "domain": "co.com.au",
                              "website": "https://co.com.au", "website_status": "live",
                              "email": "a@co.com.au", "rating": 4.9, "review_count": 400,
                              "industry": "electrician", "masthead": "newcastleherald.com.au"})
    prospect.reenrich_score_only(bid)
    guessed = db.get_business(bid)["fit_score"]

    monkeypatch.setattr(prospect.enrich, "enrich_from_website",
                        lambda url: {"aeo_audited_at": "t", "aeo_schema": "",
                                     "aeo_faq": 0, "aeo_blog": 0, "aeo_meta": 0,
                                     "aeo_pages": 3, "aeo_words": 80, "aeo_questions": 0})
    got = client.post(f"/api/businesses/{bid}/audit").json()
    assert got["read"] is True
    # Everything missing, so the opportunity is bigger than the guess was.
    assert db.get_business(bid)["fit_score"] > guessed

    body = client.get(f"/businesses/{bid}").text
    assert "What we&#39;d fix" in body or "What we'd fix" in body
    assert "No structured data" in body
