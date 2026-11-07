"""Static checks on the frontend assets (no browser).

These guard the mobile-admin-polish fixes: every CSS custom property the UI
uses is defined, form controls get an explicit dark surface, and the phone
layout rules that stop horizontal overflow stay in place.
"""
import re
from pathlib import Path

STATIC = Path(__file__).resolve().parent.parent / "static"
CSS = (STATIC / "style.css").read_text(encoding="utf-8")
JS = (STATIC / "app.js").read_text(encoding="utf-8")


def _root_block():
    m = re.search(r":root\s*\{(.*?)\n\}", CSS, re.S)
    assert m, ":root block not found"
    return m.group(1)


def _rule(selector):
    """Return the declarations of the first rule whose last selector line is `selector`."""
    m = re.search(r"(?m)^" + re.escape(selector) + r"\s*\{([^}]*)\}", CSS)
    assert m, f"rule {selector!r} not found in style.css"
    return m.group(1)


def _contrast(fg, bg):
    def lum(h):
        c = [int(h[i:i + 2], 16) / 255 for i in (1, 3, 5)]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    a, b = sorted([lum(fg), lum(bg)], reverse=True)
    return (a + 0.05) / (b + 0.05)


# --- Bug 1: readable dark form controls -----------------------------------

def test_every_css_variable_used_is_defined():
    defined = set(re.findall(r"(--[\w-]+)\s*:", CSS))
    used = set(re.findall(r"var\((--[\w-]+)", CSS + JS))
    assert used - defined == set(), f"undefined CSS tokens: {sorted(used - defined)}"


def test_root_declares_dark_color_scheme():
    assert re.search(r"color-scheme:\s*dark", _root_block())


def test_controls_have_explicit_dark_surface():
    decl = _rule("textarea")
    for prop in ("background-color: var(--field)", "color: var(--ink)", "caret-color", "border:"):
        assert prop in decl, f"control rule missing {prop!r}"


def test_placeholder_is_not_dimmed_and_passes_aa():
    decl = _rule("::placeholder")
    assert "opacity: 1" in decl
    assert "var(--ink-3)" in decl
    assert _contrast("#938d80", "#202029") >= 4.5


def test_autofill_keeps_dark_surface():
    assert "-webkit-autofill" in CSS
    assert "-webkit-text-fill-color: var(--ink)" in CSS


def test_select_arrow_is_inline_svg():
    assert "data:image/svg+xml" in _rule("select")


def test_location_name_input_hints():
    m = re.search(r'<input[^>]*id="loc-add-name"[^>]*>', JS)
    assert m
    tag = m.group(0)
    for attr in ('autocapitalize="words"', 'enterkeyhint="done"', 'autocomplete="off"'):
        assert attr in tag, f"loc-add-name missing {attr}"


# --- Bug 2: no admin overflow at phone widths -------------------------------

def test_btn_xs_is_defined():
    assert re.search(r"\.btn-xs\s*\{", CSS)


def test_location_form_grid_can_shrink():
    assert "grid-template-columns:1fr 1fr" not in JS.replace(" ", "")
    assert 'class="loc-form-grid"' in JS
    assert "minmax(0, 1fr)" in _rule(".loc-form-grid")


def test_location_rows_wrap():
    assert "flex-wrap: wrap" in _rule(".location-row")
    assert "min-width: 0" in _rule(".location-row-main")


# --- Enhancement: horizontally scrolling header admin bar -------------------

def test_nav_actions_is_a_scroll_row():
    decl = _rule(".nav-actions")
    for prop in ("flex-wrap: nowrap", "overflow-x: auto", "min-width: 0",
                 "scroll-snap-type: x proximity", "overscroll-behavior-x: contain",
                 "scrollbar-width: none"):
        assert prop in decl, f".nav-actions missing {prop!r}"
    assert ".nav-actions::-webkit-scrollbar" in CSS
    assert ".nav-actions.has-more" in CSS


def test_nav_actions_scroll_binding_exists():
    assert "function bindScrollRow(" in JS
    assert "scrollIntoView({ inline: \"center\", block: \"nearest\" })" in JS


def test_header_top_padding_has_a_floor():
    # env(safe-area-inset-top, fallback) resolves to 0 on non-notched phones,
    # which left the admin bar touching the top of the screen.
    assert "max(var(--s3), env(safe-area-inset-top))" in _rule(".site-header")



# --- Icons: inline SVG, never OS emoji ---------------------------------------

def test_no_color_emoji_in_frontend():
    # Color emoji need an OS emoji font; Linux desktops without one show empty boxes.
    html = (STATIC / "index.html").read_text(encoding="utf-8")
    pattern = re.compile(r"[\U0001F000-\U0001FFFF]|[☁⚠✏✂]️?")
    for name, text in (("app.js", JS), ("index.html", html)):
        found = sorted({m.group() for m in pattern.finditer(text)})
        assert not found, f"{name} still uses emoji {found}; use icon(...) instead"


def test_icon_set_covers_the_ui():
    for name in ("disc", "cloud", "dice", "pin", "home", "folder", "edit", "trash", "image", "undo"):
        assert re.search(rf"\b{name}: '<", JS), f"ICONS.{name} missing"
    assert "function icon(" in JS
    assert re.search(r"(?m)^\.ico\s*\{", CSS)


def test_every_owned_poster_gets_a_medium_badge():
    # Not just titles owned both ways: disc-only and digital-only get one too.
    assert 'class="badge-medium"' in JS
    assert 't.has_disc ? icon("disc")' in JS and 't.has_digital ? icon("cloud")' in JS
    assert "badge-both" not in JS + CSS


# --- Movie Night mode grid, rating cap, certification override --------------

def test_movie_night_has_six_modes_in_order():
    modes = re.findall(r'class="movie-night-tab[^"]*"[^>]*data-mode="(\w+)"', JS)
    assert modes == ["forgotten_gems", "quick_picks", "never_watched",
                     "next_in_franchise", "start_franchise", "genre_mood"]
    assert 'role="tablist"' in JS and 'role="tab"' in JS and "aria-selected" in JS
    for label in ("Forgotten Gems", "Quick Picks", "Never Watched",
                  "Next in Series", "Start a Series", "Surprise Me"):
        assert f">{label}</button>" in JS


def test_movie_night_cap_markup():
    assert 'role="radiogroup"' in JS
    assert 'aria-label="Rating cap"' in JS
    assert "matinee.movieNight.maxCert" in JS
    assert "max_cert=" in JS
    assert "aria-checked" in JS
    for cert in ("G", "PG", "PG-13"):
        assert f'data-cert="{cert}"' in JS


def test_movie_night_cap_storage_guarded():
    # Every localStorage use must sit inside a try { ... } block.
    for m in re.finditer(r"localStorage", JS):
        preceding = JS[max(0, m.start() - 200):m.start()]
        assert re.search(r"try\s*\{[^}]*$", preceding), \
            f"localStorage at offset {m.start()} is not inside try {{"


def test_new_controls_have_tap_targets():
    assert "min-height: 44px" in _rule(".movie-night-cap-option")
    assert "min-height: 44px" in _rule(".cert-override")


def test_mobile_tabs_three_columns():
    m = re.search(r"@media \(max-width: 600px\)\s*\{(.*?)\n\}", CSS, re.S)
    assert m, "no max-width: 600px block"
    block = m.group(1)
    t = re.search(r"\.movie-night-tabs\s*\{([^}]*)\}", block)
    assert t and "grid-template-columns: repeat(3, 1fr)" in t.group(1)


def test_movie_night_unrated_sentence_guarded_by_hint():
    m = re.search(r"currentCap && !\(res && res\.hint && /unrated/i\.test\(res\.hint\)\)", JS)
    assert m, "UI 'Unrated films' sentence must be skipped when the server hint already says so"


def test_movie_night_ignores_stale_responses():
    assert "const myToken = ++pickToken" in JS
    assert len(re.findall(r"if \(myToken !== pickToken\) return", JS)) >= 2
    # the guard sits before any render of a pick (after the await, before currentPick is set)
    assert JS.index("if (myToken !== pickToken) return") < JS.index("currentPick = res.results[0]")


# --- AI search ("Ask") view ---------------------------------------------------

def _ask_js():
    m = re.search(r"// =+ Ask view\n(.*?)\n  // =+ ", JS, re.S)
    assert m, "Ask view section not found in app.js"
    return m.group(1)


def _fn_body(name):
    m = re.search(rf"function {name}\(.*?\n  \}}\n", JS, re.S)
    assert m, f"function {name} not found"
    return m.group(0)


def _tokens():
    return dict(re.findall(r"(--[\w-]+):\s*(#[0-9a-fA-F]{6})\b", _root_block()))


def test_ask_route_is_parsed_and_admin_only():
    assert re.search(r'segments\[0\] === "ask"', _fn_body("parseHash"))
    route = _fn_body("handleRoute")
    assert route.count("renderAsk(") == 1
    m = re.search(r"if \(([^()]*)\)\s*\{[^{}]*renderAsk\(view\)", route)
    assert m and "isAdmin" in m.group(1) and '"/ask"' in m.group(1), \
        "renderAsk must only be called in an isAdmin branch"
    assert "function renderAsk(container)" in JS
    assert "function titleRowHTML(t)" in JS


def test_ask_header_button_gated_on_flag():
    header = _fn_body("updateHeader")
    m = re.search(r"\$\{([^`]*?)\?\s*`\s*<a[^>]*id=\"btn-header-ask\"", header)
    assert m, "Ask header button missing or not conditional"
    assert "state.me" in m.group(1) and ".ask" in m.group(1)
    assert header.index("btn-header-ask") < header.index("btn-header-movie-night")
    assert '"/ask": "btn-header-ask"' in header
    assert re.search(r"\bchat: '<", JS), "ICONS.chat missing"


def test_ask_fetches_me_once():
    assert JS.count('"/admin/api/me"') == 1
    assert re.search(r"const mePromise = isAdmin \? loadMe\(\)", JS)


def test_me_does_not_block_first_paint_and_updates_header_late():
    assert re.search(r"Promise\.race\(\[mePromise,[^\]]*setTimeout\(r, 2500\)", JS)
    assert re.search(r"mePromise\.then\(.{0,60}updateHeader\(\)", JS, re.S)


def test_ask_no_refocus_after_answer_and_retry_gated():
    body = _fn_body("sendAsk")
    assert "focus(" not in body
    assert "[400, 403, 429].includes(st.errorStatus)" in _fn_body("askThreadHTML")


def test_ask_q_has_scroll_margin():
    assert "scroll-margin-top" in _rule(".ask-q")


def test_ask_code_has_no_browser_storage():
    code = _ask_js() + _fn_body("titleRowHTML")
    assert "localStorage" not in code and "sessionStorage" not in code


def test_ask_answer_text_is_escaped():
    code = _ask_js()
    inserts = re.findall(r"\$\{([^{}]*\b(?:answer|\w+\.a)\b[^{}]*)\}", code)
    assert inserts, "no answer insertion found"
    for expr in inserts:
        assert expr.strip().startswith("esc("), f"unescaped answer insertion: ${{{expr}}}"


def test_ask_title_row_escapes_server_strings():
    row = _fn_body("titleRowHTML")
    for field in ("t.title", "c.location_path", "c.service"):
        assert f"esc({field}" in row, f"{field} not escaped"
    assert "onerror=\"this.onerror=null;this.parentElement.innerHTML" not in row
    assert 'href="#/t/${' in row


def test_ask_instead_link_in_no_matches():
    grid = _fn_body("renderGridContent")
    assert "Ask instead" in grid and "state.me && state.me.ask" in grid


def test_ask_css_classes_defined():
    for sel in (".ask-view", ".ask-thread", ".ask-q", ".ask-a", ".title-row",
                ".title-row-thumb", ".title-row-loc", ".ask-input"):
        assert re.search(r"(?m)^" + re.escape(sel) + r"\s*\{", CSS), f"{sel} missing"
    assert "safe-area-inset-bottom" in _rule(".ask-input")
    thumb = _rule(".title-row-thumb")
    assert "width: 64px" in thumb and "height: 96px" in thumb


def test_ask_input_hints():
    m = re.search(r'<input[^>]*id="ask-q"[^>]*>', JS)
    assert m
    for attr in ('enterkeyhint="search"', 'maxlength="500"'):
        assert attr in m.group(0)


def test_title_row_text_passes_aa_on_card():
    tok = _tokens()
    rules = re.findall(r"(?m)^(\.title-row[\w-]*)\s*\{([^}]*)\}", CSS)
    colors = []
    for _sel, decl in rules:
        for var in re.findall(r"(?<![-\w])color:\s*var\((--[\w-]+)\)", decl):
            colors.append(var)
    assert colors, "no text colors on .title-row rules"
    for var in colors:
        assert var in tok, f"{var} not a hex token"
        assert _contrast(tok[var], "#16161c") >= 4.5, f"{var} fails AA on #16161c"
