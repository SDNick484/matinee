"""Playwright smoke test for the copy details form and title view at phone widths (412 and 360 px).

Same harness and skip guard as tests/test_people_ui.py: skipped when playwright or Chromium is missing, and the CI
"ui-smoke" job fails if any test here is skipped. Unlike that file nothing is stubbed: a real server, a real database,
and The Dark Knight (TMDB 155) with a 4K UHD disc on Family Room > Media Cabinet > Shelf 1.

Scenario: an editor opens "More" on the disc's Edit form and enters edition "Steelbook", code unredeemed, purchased
2026-10-31, price 24.99; the database gets 2499 cents, the title view shows the edition to everyone and the code,
date and price to editors only.
"""
import glob
import os
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

WIDTHS = (412, 360)


def _chromium_path():
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "", "/opt/pw-browsers"]
    for root in filter(None, roots):
        for pat in ("chromium-*/chrome-linux64/chrome", "chromium-*/chrome-linux/chrome"):
            hits = sorted(glob.glob(os.path.join(root, pat)))
            if hits:
                return hits[-1]
    return None  # let playwright use its own default


@pytest.fixture
def server(tmp_path, monkeypatch):
    from werkzeug.serving import make_server
    import app as core

    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app.LAN_EDIT", True)
    monkeypatch.setattr("app._standalone_db", None)
    core.init_db()
    srv = make_server("127.0.0.1", 0, core.app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    t.join(timeout=5)


@pytest.fixture
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=_chromium_path())
        except Exception as e:  # noqa: BLE001 — no usable Chromium here
            pytest.skip(f"Chromium won't launch: {e}")
        yield b
        b.close()


@pytest.fixture
def dark_knight(server):
    from app import db
    from conftest import seed_title
    con = db()
    con.execute("INSERT INTO locations (id, parent_id, name) VALUES (1, NULL, 'Family Room'), "
                "(2, 1, 'Media Cabinet'), (3, 2, 'Shelf 1')")
    tid = seed_title(con, 155, copies=("4K UHD",))
    con.execute("UPDATE copies SET location_id=3 WHERE title_id=?", (tid,))
    con.commit()
    return tid


def copy_row(tid, kind="physical"):
    from app import db
    r = db().execute("SELECT edition, code_status, purchased_on, price_cents FROM copies "
                     "WHERE title_id=? AND kind=? ORDER BY id DESC", (tid, kind)).fetchone()
    return tuple(r) if r else None


def open_page(browser, url, width):
    page = browser.new_page(viewport={"width": width, "height": 915})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_selector(".detail-view")
    return page, errors


def no_sideways_scroll(page):
    """Neither the page nor an open modal scrolls horizontally."""
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "page scrolls sideways"
    assert page.evaluate("(() => { const d = document.querySelector('.modal-dialog'); "
                         "return !d || d.scrollWidth <= d.clientWidth; })()"), "the modal scrolls sideways"


def one_screen(page):
    """The modal fits without its own vertical scrollbar."""
    assert page.evaluate("(() => { const d = document.querySelector('.modal-dialog'); "
                         "return d.scrollHeight <= d.clientHeight; })()"), "the form needs scrolling"


def edit_disc(page):
    page.click(".btn-edit-copy")
    page.wait_for_selector("#edit-copy-form")


# ------------------------------------------------------------------------------------- money helpers

@pytest.mark.parametrize("text, cents", [
    ("24.99", 2499), ("$24.99", 2499), ("  24.99 ", 2499), ("24", 2400), ("24.9", 2490), (".99", 99),
    ("0", 0), ("0.00", 0), ("1,499.00", 149900), ("100000", 10_000_000),
    ("", None), ("   ", None), ("$", "invalid"), ("abc", "invalid"), ("-5", "invalid"),
    ("24.999", "invalid"), ("2,49", "invalid"), ("1e3", "invalid"), ("24.99.1", "invalid"),
])
def test_dollars_to_cents(server, browser, text, cents):
    page = browser.new_page()
    page.goto(f"{server}/admin")
    got = page.evaluate("async (t) => { const m = await import('/static/js/core.js'); const v = m.dollarsToCents(t); "
                        "return v === undefined ? 'invalid' : v; }", text)
    assert got == cents


@pytest.mark.parametrize("cents, text", [(2499, "$24.99"), (0, "$0.00"), (5, "$0.05"), (149900, "$1,499.00"),
                                         (10_000_000, "$100,000.00"), (None, "")])
def test_cents_to_dollars(server, browser, cents, text):
    page = browser.new_page()
    page.goto(f"{server}/admin")
    assert page.evaluate("async (c) => (await import('/static/js/core.js')).centsToDollars(c)", cents) == text


# ------------------------------------------------------------------------------------- the edit form

@pytest.mark.parametrize("width", WIDTHS)
def test_edit_form_fits_with_more_closed_and_open(server, browser, dark_knight, width):
    page, errors = open_page(browser, f"{server}/admin#/t/{dark_knight}", width)
    edit_disc(page)
    assert page.locator("#edit-more").evaluate("d => d.open") is False   # a plain copy starts closed
    no_sideways_scroll(page)
    one_screen(page)
    page.click("#edit-more summary")
    for sel in ("#edit-edition", "#edit-code-grid .select-chip", "#edit-purchased", "#edit-price"):
        assert page.locator(sel).first.is_visible(), sel
    no_sideways_scroll(page)
    assert all(page.locator(s).bounding_box()["height"] >= 44 for s in ("#edit-edition", "#edit-purchased", "#edit-price"))
    assert errors == []


@pytest.mark.parametrize("width", WIDTHS)
def test_dark_knight_steelbook_is_saved_and_shown(server, browser, dark_knight, width):
    page, errors = open_page(browser, f"{server}/admin#/t/{dark_knight}", width)
    edit_disc(page)
    page.click("#edit-more summary")
    page.fill("#edit-edition", "Steelbook")
    page.click("#edit-code-grid .select-chip[data-code=unredeemed]")
    page.fill("#edit-purchased", "2026-10-31")
    page.fill("#edit-price", "$24.99")
    page.click("#btn-edit-save")
    page.wait_for_selector("#edit-copy-form", state="detached")
    page.wait_for_selector(".copy-edition")
    assert copy_row(dark_knight) == ("Steelbook", "unredeemed", "2026-10-31", 2499)
    row = page.locator(".copy-row").first
    assert row.locator(".copy-edition").inner_text() == "Steelbook"
    extra = row.locator(".copy-extra").inner_text()
    assert "Unredeemed" in extra and "Oct 31, 2026" in extra and "$24.99" in extra
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # reopening shows what was saved, with More already open
    edit_disc(page)
    assert page.locator("#edit-more").evaluate("d => d.open") is True
    assert page.input_value("#edit-edition") == "Steelbook"
    assert page.input_value("#edit-purchased") == "2026-10-31"
    assert page.input_value("#edit-price") == "24.99"
    assert page.locator("#edit-code-grid .select-chip.active").get_attribute("data-code") == "unredeemed"
    no_sideways_scroll(page)
    assert errors == []


def test_clearing_the_fields_clears_them(server, browser, dark_knight):
    page, _ = open_page(browser, f"{server}/admin#/t/{dark_knight}", 412)
    edit_disc(page)
    page.click("#edit-more summary")
    page.fill("#edit-edition", "Steelbook")
    page.fill("#edit-price", "24.99")
    page.click("#btn-edit-save")
    page.wait_for_selector(".copy-edition")
    edit_disc(page)
    page.fill("#edit-edition", "")
    page.fill("#edit-price", "")
    page.click("#btn-edit-save")
    page.wait_for_selector(".copy-edition", state="detached")
    assert copy_row(dark_knight) == (None, "none", None, None)


def test_a_bad_price_is_refused_in_the_form_and_nothing_is_sent(server, browser, dark_knight):
    page, errors = open_page(browser, f"{server}/admin#/t/{dark_knight}", 412)
    puts = []
    page.on("request", lambda r: puts.append(r.url) if r.method == "PUT" else None)
    edit_disc(page)
    page.click("#edit-more summary")
    page.fill("#edit-price", "abc")
    page.click("#btn-edit-save")
    page.wait_for_selector("#toast, .toast", state="visible")
    assert "price" in page.locator("#toast, .toast").first.inner_text().lower()
    assert page.locator("#edit-copy-form").count() == 1   # still open
    assert puts == [] and copy_row(dark_knight) == (None, "none", None, None)
    assert errors == []


def test_switching_a_disc_to_digital_hides_the_code_and_sends_none(server, browser, dark_knight):
    from app import db
    db().execute("UPDATE copies SET code_status='unredeemed' WHERE title_id=?", (dark_knight,))
    db().commit()
    page, errors = open_page(browser, f"{server}/admin#/t/{dark_knight}", 412)
    edit_disc(page)
    assert page.locator("#edit-code-group").is_visible()
    page.click("#edit-kind-toggle .toggle-btn[data-kind=digital]")
    assert not page.locator("#edit-code-group").is_visible()
    page.click("#btn-edit-save")
    page.wait_for_selector("#edit-copy-form", state="detached")
    assert copy_row(dark_knight, "digital")[1] == "none"
    assert errors == []


# ------------------------------------------------------------------------------------- the add form

@pytest.mark.parametrize("width", WIDTHS)
def test_add_form_fits_and_saves_a_steelbook(server, browser, dark_knight, width):
    page, errors = open_page(browser, f"{server}/admin#/t/{dark_knight}", width)
    page.click("#btn-add-copy")
    page.wait_for_selector("#copy-form")
    assert page.locator("#add-more").evaluate("d => d.open") is False
    no_sideways_scroll(page)
    one_screen(page)
    page.click("#add-more summary")
    no_sideways_scroll(page)
    page.fill("#add-edition", "Criterion Collection")
    page.click("#add-code-grid .select-chip[data-code=redeemed]")
    page.fill("#add-purchased", "2026-11-01")
    page.fill("#add-price", "39.99")
    page.click("#btn-save-copy")
    page.wait_for_selector("#copy-form", state="detached")
    assert copy_row(dark_knight) == ("Criterion Collection", "redeemed", "2026-11-01", 3999)
    assert errors == []


def test_add_form_digital_has_no_code_chips(server, browser, dark_knight):
    page, _ = open_page(browser, f"{server}/admin#/t/{dark_knight}", 412)
    page.click("#btn-add-copy")
    page.wait_for_selector("#copy-form")
    page.click("#add-more summary")
    assert page.locator("#add-code-group").is_visible()
    page.click("#kind-toggle .toggle-btn[data-kind=digital]")
    assert not page.locator("#add-code-group").is_visible()
    assert page.locator("#add-edition").is_visible()   # edition, date and price still apply
    page.fill("#add-edition", "Extended Edition")
    page.click("#btn-save-copy")
    page.wait_for_selector("#copy-form", state="detached")
    assert copy_row(dark_knight, "digital") == ("Extended Edition", "none", None, None)


# ------------------------------------------------------------------------------------- what visitors see

def test_a_visitor_sees_the_edition_and_nothing_else(server, browser, dark_knight):
    from app import db
    db().execute("UPDATE copies SET edition='Steelbook', code_status='unredeemed', purchased_on='2026-10-31', "
                 "price_cents=2499 WHERE title_id=?", (dark_knight,))
    db().commit()
    page, errors = open_page(browser, f"{server}/#/t/{dark_knight}", 412)
    assert page.locator(".copy-row .copy-edition").inner_text() == "Steelbook"
    assert page.locator(".copy-extra").count() == 0
    html = page.content()
    for secret in ("nredeemed", "Oct 31, 2026", "24.99", "2499"):
        assert secret not in html, secret
    assert page.locator(".btn-edit-copy").count() == 0
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    assert errors == []


# ------------------------------------------------------------------------------------- Stats line and grid filter
# Scenario: The Dark Knight's 4K UHD disc has an unredeemed code; Amelie (TMDB 194) has a plain Blu-ray.

@pytest.fixture
def two_titles(server, dark_knight):
    from app import db
    from conftest import seed_title
    con = db()
    con.execute("UPDATE copies SET code_status='unredeemed' WHERE title_id=?", (dark_knight,))
    seed_title(con, 194)
    con.commit()
    return dark_knight


@pytest.mark.parametrize("width", WIDTHS)
def test_stats_line_links_to_the_filtered_grid(server, browser, two_titles, width):
    page = browser.new_page(viewport={"width": width, "height": 915})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server}/admin#/stats")
    page.wait_for_selector(".stats-grid-overview")
    line = page.locator("#stats-codes")
    assert line.inner_text().strip() == "Unredeemed digital codes: 1"
    assert line.locator("a").get_attribute("href") == "#/?code=unredeemed"
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    line.locator("a").click()
    page.wait_for_selector("#filter-code-btn")
    assert page.locator("#filter-code-btn").inner_text().strip() == "Unredeemed codes ✕"
    assert "code=unredeemed" in page.url
    page.wait_for_function("document.querySelectorAll('#grid-container img, #grid-container .poster-ph').length === 1")
    grid_text = page.locator("#grid-container").inner_text()
    assert "The Dark Knight" in grid_text and "Am" not in grid_text   # Amelie has no code
    assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
    # the chip clears just that filter
    page.click("#filter-code-btn")
    page.wait_for_function("!location.hash.includes('code=')")
    page.wait_for_function("document.querySelectorAll('#grid-container img, #grid-container .poster-ph').length === 2")
    assert page.locator("#filter-code-btn").count() == 0
    assert errors == []


def test_stats_line_is_plain_text_at_zero(server, browser, dark_knight):
    page = browser.new_page(viewport={"width": 412, "height": 915})
    page.goto(f"{server}/admin#/stats")
    page.wait_for_selector(".stats-grid-overview")
    line = page.locator("#stats-codes")
    assert line.inner_text().strip() == "Unredeemed digital codes: 0"
    assert line.locator("a").count() == 0


def test_reset_clears_the_code_filter_with_the_others(server, browser, two_titles):
    page = browser.new_page(viewport={"width": 412, "height": 915})
    page.goto(f"{server}/admin#/?code=unredeemed&watched=unwatched")
    page.wait_for_selector("#filter-code-btn")
    page.click("#filter-reset-btn")
    page.wait_for_function("!location.hash.includes('code=') && !location.hash.includes('watched=')")
    assert page.locator("#filter-code-btn").count() == 0


def test_an_editor_code_value_the_server_refuses_falls_back_to_the_full_grid(server, browser, two_titles):
    page = browser.new_page(viewport={"width": 412, "height": 915})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server}/admin#/?code=redeemed")
    page.wait_for_function("!location.hash.includes('code=')")
    page.wait_for_function("document.querySelectorAll('#grid-container img, #grid-container .poster-ph').length === 2")
    assert page.locator("#filter-code-btn").count() == 0
    assert errors == []


def test_a_visitor_has_no_stats_line_and_loses_the_code_filter(server, browser, two_titles):
    page = browser.new_page(viewport={"width": 412, "height": 915})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server}/#/stats")
    page.wait_for_selector(".stats-grid-overview")
    assert page.locator("#stats-codes").count() == 0
    assert "nredeemed" not in page.content()
    page.goto(f"{server}/#/?code=unredeemed")
    page.wait_for_function("!location.hash.includes('code=')")
    page.wait_for_function("document.querySelectorAll('#grid-container img, #grid-container .poster-ph').length === 2")
    assert page.locator("#filter-code-btn").count() == 0
    assert "Unable to load" not in page.content()
    assert errors == []
