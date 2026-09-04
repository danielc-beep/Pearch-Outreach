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
    assert "#16224A" in UNPROTECTED_HTML
    assert "ACM Outreach Database is locked" in UNPROTECTED_HTML


def test_the_swarm_is_configured_to_stay_readable(client):
    js = client.get("/static/fairy-lights.js").text
    # Capped below the display's density: at full retina the fill rate is what
    # decides between 60fps and 18.
    assert "Math.min(window.devicePixelRatio || 1, 1.5)" in js
    # Still stops for reduced motion and for a hidden tab.
    assert "prefers-reduced-motion" in js
    assert "visibilitychange" in js


def test_body_copy_is_bright_enough_to_read_over_the_field(client):
    css = client.get("/static/app.css").text
    assert "--text:         #F4F9FF;" in css
    assert "--text-muted:   #C3D4E8;" in css
    # Panels sit at 90% so a light passing behind a paragraph is a shimmer,
    # not a competing mark.
    assert "rgba(31,46,92,0.90)" in css


def test_the_swarm_flocks_and_avoids_the_panels(client):
    js = client.get("/static/fairy-lights.js").text
    # The three boid rules, over a grid rather than every pair.
    for rule in ("W_ALIGN", "W_COHERE", "W_SEPARATE", "refillGrid"):
        assert rule in js, rule
    # Panels are obstacles, and a light inside one is never drawn.
    assert "W_AVOID" in js
    assert "p.hidden = true" in js
    assert ".topbar, .footer, .card, .table-wrap, .searchbar, .source-card, .notice" in js
    # Panel positions move under a fixed canvas, so they are re-read on scroll.
    assert "addEventListener('scroll'" in js


def test_panels_carry_no_backdrop_filter(client):
    """
    The swarm steers around every panel, so the only thing behind one is the
    static sky. A backdrop blur would make the browser redo that blur on every
    animated frame — it cost two thirds of the frame rate on the table page.
    """
    import re
    css = client.get("/static/app.css").text
    # The word appears in a comment explaining the absence; look for the
    # declaration itself.
    assert not re.search(r"^\s*(-webkit-)?backdrop-filter\s*:", css, re.M)
    assert "backdrop-filter: blur" not in css


def test_the_sticky_header_is_opaque(client):
    """It could be translucent only while a blur smeared what passed under it."""
    assert "background: #101A3E;" in client.get("/static/app.css").text


def test_the_search_bar_is_the_one_light_surface(client):
    css = client.get("/static/app.css").text
    assert "max-width: 860px; margin: 0 auto; background: #FFFFFF;" in css
    # Two class names deep, or the dark input[type=text] rule further down the
    # file wins on specificity and paints near-white text on the white bar.
    assert ".searchbar .field input, .searchbar .field select {" in css
    assert "color: #16224A;" in css
    assert ".searchbar .field input::placeholder { color: #96A2BA; }" in css


def test_the_search_bar_fields_are_only_capped_while_it_is_a_row(client):
    """Stacked on a phone, a cap leaves the dividers at three different lengths."""
    css = client.get("/static/app.css").text
    assert "@media (min-width: 761px) {" in css
    assert ".searchbar .field-masthead { flex: 1.5; }" in css
    # And nothing sets those widths inline, where no media query could reach.
    home = client.get("/").text
    assert "max-width:280px" not in home
    assert "max-width:150px" not in home


def test_the_search_bar_chevron_clears_the_text(client):
    """
    The native arrow is drawn immediately after the selected text, so a long
    masthead name ran straight under it.
    """
    css = client.get("/static/app.css").text
    assert "appearance: none; -webkit-appearance: none;" in css
    assert "background-position: right 2px top 50%;" in css
    # Room reserved for it, and the text ellipsised before it gets there.
    assert "padding-right: 20px;" in css
    assert "text-overflow: ellipsis;" in css
