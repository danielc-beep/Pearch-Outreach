from util import (clean_email, clean_phone, domain_of, find_emails, find_phone,
                  normalise_url, parse_address)


def test_domain_strips_scheme_www_and_case():
    assert domain_of("WWW.Example.COM.AU/contact") == "example.com.au"
    assert domain_of("https://sub.acme.com.au") == "sub.acme.com.au"
    assert domain_of("not a url") == ""


def test_normalise_url_adds_scheme_and_trims():
    assert normalise_url("acme.com.au/") == "https://acme.com.au"
    assert normalise_url("") == ""


def test_phone_formats():
    assert clean_phone("+61 2 4979 5000") == "02 4979 5000"
    assert clean_phone("0412345678") == "0412 345 678"
    assert clean_phone("123") == ""


def test_find_phone_handles_real_page_text():
    assert find_phone("Call us on (02) 4979 5000 today") == "02 4979 5000"
    assert find_phone("no number here") == ""


def test_emails_prefer_role_addresses_and_drop_junk():
    found = find_emails("noreply@x.com sales@acme.com.au info@acme.com.au")
    assert found[0] == "info@acme.com.au"
    assert "noreply@x.com" not in found
    assert clean_email("someone@sentry.io") == ""


def test_parse_australian_address():
    assert parse_address("12 Hunter St, Newcastle NSW 2300") == {
        "suburb": "Newcastle", "state": "NSW", "postcode": "2300",
    }


def test_cloudflare_obfuscated_email_is_recovered():
    """Cloudflare hides every address behind a XOR blob; sites using it look empty."""
    from util import deobfuscate
    plain, key = "info@hunterbrokers.com.au", 0x2a
    encoded = format(key, "02x") + "".join(format(ord(c) ^ key, "02x") for c in plain)
    html = f'<a href="/cdn-cgi/l/email-protection" data-cfemail="{encoded}">email us</a>'
    assert find_emails(deobfuscate(html)) == [plain]


def test_html_entity_encoded_email_is_recovered():
    from util import deobfuscate
    encoded = "&#105;&#110;&#102;&#111;&#64;darbylegal.com.au"
    assert find_emails(deobfuscate(encoded)) == ["info@darbylegal.com.au"]


def test_at_and_dot_spelled_out_is_recovered():
    from util import deobfuscate
    for text, expected in [
        ("sales [at] hunterbrokers [dot] com [dot] au", "sales@hunterbrokers.com.au"),
        ("hello (at) darbylegal (dot) com (dot) au", "hello@darbylegal.com.au"),
        ("admin [ at ] merewetherdental [ dot ] com [ dot ] au", "admin@merewetherdental.com.au"),
    ]:
        assert find_emails(deobfuscate(text)) == [expected], text


def test_deobfuscation_leaves_plain_addresses_alone():
    from util import deobfuscate
    assert find_emails(deobfuscate('<a href="mailto:info@acme.com.au">us</a>')) == ["info@acme.com.au"]


def test_deobfuscation_survives_junk_input():
    from util import deobfuscate
    for junk in ("", "<p>no emails here</p>", 'data-cfemail="zzzz"'):
        assert find_emails(deobfuscate(junk)) == []
