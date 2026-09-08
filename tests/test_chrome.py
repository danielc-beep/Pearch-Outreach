"""The site chrome: the ACM lockup and the light field on every page."""

PAGES = ("/", "/businesses", "/prospect", "/crm", "/outbox", "/suppressions")


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


def test_the_heading_flourish_is_the_exception_not_the_rule(client):
    """
    Twelve of eighteen headings were the same construction — plain words plus
    an italic serif flourish. A device applied without exception is what reads
    as machine-written, so most headings now say the plain thing.
    """
    import pathlib
    import re
    templates = pathlib.Path(__file__).resolve().parent.parent / "templates"
    headings = []
    for path in templates.glob("*.html"):
        headings += re.findall(r"<h2>(.*?)</h2>", path.read_text(), re.S)
    literal = [h for h in headings if "{{" not in h]
    flourished = [h for h in literal if "<em>" in h]
    assert len(literal) >= 10
    assert len(flourished) <= 4, flourished


def test_the_interface_responds_to_being_touched(client):
    css = client.get("/static/app.css").text
    assert ".btn:active:not(:disabled)" in css          # buttons depress
    assert "@keyframes score-fill" in css               # score bars fill
    assert "@keyframes rise" in css                     # things enter
    assert "@keyframes leave-right" in css              # the queue has a direction
    js = client.get("/static/app.js").text
    assert "animationDelay" in js                       # staggered entrance
    assert "requestAnimationFrame(step)" in js          # numbers count up


def test_none_of_it_moves_under_reduced_motion(client):
    css = client.get("/static/app.css").text
    tail = css[css.index("Micro-interaction"):]
    assert "@media (prefers-reduced-motion: reduce)" in tail
    assert "animation: none !important;" in tail
    assert "prefers-reduced-motion" in client.get("/static/app.js").text


# ---------- One name, one layout ----------

def _classes_that_set_display(css: str) -> dict[str, list[int]]:
    """
    Top-level rules that set `display` on a bare class, and where.

    Two blocks refining the same class is ordinary CSS — a base rule and a
    later one adding a transition or a border. Two blocks each deciding what
    a class's *layout* is means two components are fighting over one name,
    and the loser is whichever the browser reads first.
    """
    import re
    from collections import defaultdict
    seen = defaultdict(list)
    depth = 0
    names: list[str] = []
    start_line = 0
    for number, line in enumerate(css.splitlines(), 1):
        stripped = line.strip()
        if depth == 0 and "{" in stripped and not stripped.startswith(("/*", "*", "@")):
            names = [m.group(1) for m in
                     (re.fullmatch(r"\.([a-zA-Z0-9_-]+)", sel.strip())
                      for sel in stripped.split("{")[0].split(","))
                     if m]
            start_line = number
        if names and depth <= 1 and re.search(r"(^|[;{\s])display\s*:", stripped):
            for name in names:
                seen[name].append(start_line)
            names = []
        depth += stripped.count("{") - stripped.count("}")
        if depth == 0:
            names = []
    return seen


def test_no_two_components_lay_out_the_same_class_name():
    """
    The CRM's kanban was given `.board`, which the dashboard's work board
    already owned — one said `display: flex`, the other `display: grid`. The
    later rule won everywhere and turned the work board into four narrow
    columns wrapping one word per line, on the live site and in no test.
    """
    from pathlib import Path
    css = Path(__file__).resolve().parent.parent / "static" / "app.css"
    clashes = {name: lines for name, lines in _classes_that_set_display(css.read_text()).items()
               if len(set(lines)) > 1}
    assert not clashes, (
        "these class names have their layout set by more than one rule block: "
        + "; ".join(f"{name} (lines {', '.join(map(str, sorted(set(lines))))})"
                    for name, lines in sorted(clashes.items())))


# ---------- A fix that reaches the browser ----------

def test_the_stylesheet_carries_its_own_fingerprint(client):
    """
    Without a version on the URL the browser keeps the copy it already has,
    and a CSS fix that shipped to the server leaves the page looking broken —
    which from the outside is indistinguishable from not having fixed it.
    """
    import re
    body = client.get("/").text
    match = re.search(r'href="/static/app\.css\?v=([0-9a-f]{10})"', body)
    assert match, "app.css is linked without a fingerprint"


def test_every_script_carries_one_too(client):
    import re
    body = client.get("/").text
    for name in ("app.js", "fairy-lights.js"):
        assert re.search(rf'src="/static/{re.escape(name)}\?v=[0-9a-f]{{10}}"', body), name


def test_the_sign_in_page_gets_the_same_treatment(monkeypatch):
    """It is the first page anyone loads, so it is the first one to go stale."""
    import importlib
    import auth, config, app as app_module
    from fastapi.testclient import TestClient
    monkeypatch.setenv("PEARCH_PASSWORD", "s3cret")
    importlib.reload(config); importlib.reload(auth); importlib.reload(app_module)
    body = TestClient(app_module.app).get("/login").text
    assert "/static/app.css?v=" in body
    monkeypatch.delenv("PEARCH_PASSWORD", raising=False)
    importlib.reload(config); importlib.reload(auth); importlib.reload(app_module)


def test_the_fingerprint_follows_the_file(tmp_path, monkeypatch):
    import app as app_module
    first = app_module.asset("app.css")
    css = app_module.STATIC_DIR / "app.css"
    original = css.read_bytes()
    try:
        css.write_bytes(original + b"\n/* a change */\n")
        assert app_module.asset("app.css") != first
    finally:
        css.write_bytes(original)
    assert app_module.asset("app.css") == first


def test_a_missing_asset_does_not_break_the_page():
    import app as app_module
    assert app_module.asset("not-here.css") == "/static/not-here.css"


def test_the_stylesheet_carries_nothing_no_page_uses():
    """
    Rules for classes nothing renders.

    Not tidiness. A dead `.steps { display: grid }` sat in the stylesheet
    after the component using it was removed, and the next thing to want that
    name inherited a four-column grid — a carousel laid out as four columns of
    one word each, on a page that passed every other test. Dead CSS is not
    inert; it is a trap with a name on it.

    A class counts as used when a template or a script mentions it, or when
    one of them builds it — `feed-{{ a.kind }}` and `'is-' + tone` are how
    several of these are applied.
    """
    import re
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    css = (root / "static" / "app.css").read_text()

    declared: dict[str, int] = {}
    depth = 0
    for number, line in enumerate(css.splitlines(), 1):
        stripped = line.strip()
        if depth == 0 and "{" in stripped and not stripped.startswith(("/*", "*", "@")):
            for selector in stripped.split("{")[0].split(","):
                found = re.match(r"\.([a-zA-Z][a-zA-Z0-9_-]*)", selector.strip())
                if found:
                    declared.setdefault(found.group(1), number)
        depth += stripped.count("{") - stripped.count("}")

    markup = "\n".join(p.read_text() for p in
                       list((root / "templates").glob("*.html")) +
                       list((root / "static").glob("*.js")))

    def used(name: str) -> bool:
        if re.search(r"\b" + re.escape(name) + r"\b", markup):
            return True
        stem = name.rsplit("-", 1)[0] + "-"
        return bool(re.search(re.escape(stem) + r"(\{\{|['\"]\s*\+|\$\{)", markup))

    dead = {name: line for name, line in declared.items() if not used(name)}
    assert not dead, ("these rules style nothing, and the names are free for the "
                      "next component to trip over: "
                      + ", ".join(f".{n} (line {l})" for n, l in sorted(dead.items(),
                                                                       key=lambda kv: kv[1])))
