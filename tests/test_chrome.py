"""The site chrome: the ACM lockup and the light field on every page."""

PAGES = ("/", "/businesses", "/prospect", "/campaigns", "/outbox", "/suppressions")


def test_the_acm_mark_is_the_logo_on_every_page(client):
    for path in PAGES:
        html = client.get(path).text
        # The wordmark is the real vector, not the letters "ACM" set in a font.
        assert 'class="acm"' in html, path
        assert 'aria-label="ACM"' in html, path
        assert "Outreach" in html, path


def test_the_light_field_is_present_and_out_of_the_way(client):
    html = client.get("/").text
    assert '<canvas id="fairy-lights"' in html
    # Decoration, so it is hidden from assistive tech and cannot be clicked.
    assert 'id="fairy-lights" aria-hidden="true"' in html
    assert "/static/fairy-lights.js" in html


def test_the_sky_sits_behind_the_content(client):
    css = client.get("/static/app.css").text
    assert "#fairy-lights { position: fixed; inset: 0; z-index: -1; pointer-events: none; }" in css
    # body must stay transparent or it paints over both background layers.
    assert "background: transparent;" in css


def test_the_locked_page_uses_the_dark_ground():
    from auth import UNPROTECTED_HTML
    assert "#050A1C" in UNPROTECTED_HTML
    assert "ACM Outreach Database is locked" in UNPROTECTED_HTML
