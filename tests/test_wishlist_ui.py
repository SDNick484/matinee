"""Playwright smoke test for the wish list at 412 px: Want to buy in the add flow, Want… and Add to a wish list… on a
title page, a copy that ends the want, and the Wanted view.

Same harness and skip guard as tests/test_people_ui.py (the CI "ui-smoke" job fails if any test here is skipped), but
against the real backend: a seeded database, TMDB answered from tests/fixtures/tmdb, and a stub Wish Lists app on a
loopback port (or port 1 for "down"). Only the TMDB search list is stubbed with page.route.
"""
import glob
import json
import os
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from conftest import seed_title  # noqa: E402
from test_wishlist import FakeTmdb, StubWishlist  # noqa: E402

SPIRITED_AWAY = {"tmdb_id": 129, "title": "Spirited Away", "year": 2001, "poster_path": None, "overview": "", "owned": False}


def _chromium_path():
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "", "/opt/pw-browsers"]
    for root in filter(None, roots):
        for pat in ("chromium-*/chrome-linux64/chrome", "chromium-*/chrome-linux/chrome"):
            hits = sorted(glob.glob(os.path.join(root, pat)))
            if hits:
                return hits[-1]
    return None  # let playwright use its own default


@pytest.fixture
def stub(monkeypatch):
    s = StubWishlist()
    monkeypatch.setattr("app.WISHLIST_URL", s.url)
    yield s
    s.close()


@pytest.fixture
def server(tmp_path, monkeypatch):
    from werkzeug.serving import make_server
    import app as core

    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app.LAN_EDIT", True)
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.WISHLIST_URL", "http://127.0.0.1:1")     # down, unless a test asks for the stub
    monkeypatch.setattr("app.TMDB_READ_TOKEN", "test-token")
    monkeypatch.setattr("app.tmdb_get", FakeTmdb())
    monkeypatch.setattr("app.http_bytes", lambda url, timeout=15: b"\xff\xd8jpeg " + url.encode())
    core.init_db()
    srv = make_server("127.0.0.1", 0, core.app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    t.join(timeout=5)


@pytest.fixture
def dark_knight(server):
    """The Dark Knight, owned only as Digital HD on Movies Anywhere."""
    import app as core
    con = core.connect()
    tid = seed_title(con, 155, copies=(), digital=("Digital HD",))
    con.execute("UPDATE copies SET service='Movies Anywhere' WHERE title_id=?", (tid,))
    con.commit()
    con.close()
    return tid


@pytest.fixture
def browser():
    with sync_api.sync_playwright() as p:
        try:
            b = p.chromium.launch(executable_path=_chromium_path())
        except Exception as e:  # noqa: BLE001 — no usable Chromium here
            pytest.skip(f"Chromium won't launch: {e}")
        yield b
        b.close()


def _page(browser, width=412):
    page = browser.new_page(viewport={"width": width, "height": 915})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return page, errors


def _rows(sql, *args):
    import app as core
    con = core.connect()
    try:
        return [tuple(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def _no_sideways_scroll(page):
    assert page.evaluate("document.documentElement.scrollWidth") <= page.viewport_size["width"]


def test_want_to_buy_in_the_add_flow_creates_the_title_with_no_copies(server, browser):
    page, errors = _page(browser)
    page.route("**/admin/api/tmdb/search?*", lambda r: r.fulfill(
        content_type="application/json", body=json.dumps({"results": [SPIRITED_AWAY], "q": "spirited"})))
    page.goto(f"{server}/admin#/add")
    page.fill("#tmdb-search-input", "spirited")
    page.click(".result-card")
    want_btn = page.locator("#btn-want-to-buy")
    want_btn.wait_for(state="visible")                       # Matinee doesn't have it: the button is offered
    assert page.locator("#btn-save-copy").bounding_box()["y"] >= 0
    _no_sideways_scroll(page)
    want_btn.click()
    page.click("#want-format-grid .select-chip[data-fmt='4K UHD']")
    assert page.locator("#want-format-grid .select-chip.active").inner_text() == "4K UHD"
    page.fill("#want-note", "the Studio Ghibli box")
    page.click("#btn-want-save")
    page.wait_for_selector(".detail-view .want-block.has-want")
    assert page.locator(".want-label").inner_text() == "Want to buy"
    assert "4K UHD" in page.locator(".want-block").inner_text() and "Studio Ghibli box" in page.locator(".want-block").inner_text()
    assert _rows("SELECT t.title, w.wanted_format, w.note FROM wants w JOIN titles t ON t.id=w.title_id") == [
        ("Spirited Away", "4K UHD", "the Studio Ghibli box")]
    assert _rows("SELECT count(*) FROM copies") == [(0,)]
    _no_sideways_scroll(page)
    assert errors == []


def test_the_want_to_buy_button_is_not_offered_for_a_title_already_in_matinee(server, browser, dark_knight):
    page, errors = _page(browser)
    page.route("**/admin/api/tmdb/search?*", lambda r: r.fulfill(
        content_type="application/json", body=json.dumps({"results": [dict(SPIRITED_AWAY, tmdb_id=155, title="The Dark Knight",
                                                                           year=2008, owned=True)], "q": "dark"})))
    page.goto(f"{server}/admin#/add")
    page.fill("#tmdb-search-input", "dark")
    with page.expect_response("**/admin/api/tmdb/155/owned"):        # the owned check has answered
        page.click(".result-card")
    page.wait_for_timeout(200)
    assert page.locator("#btn-save-copy").is_visible()
    assert not page.locator("#btn-want-to-buy").is_visible()
    assert errors == []


def test_want_and_add_to_a_wish_list_on_the_title_page(server, browser, dark_knight, stub):
    page, errors = _page(browser)
    page.goto(f"{server}/admin#/t/{dark_knight}")
    page.wait_for_selector("#want-block")
    assert page.locator(".want-label").inner_text() == "Not on the wish list"
    assert not page.locator("#btn-wish").count()               # nothing to push yet

    page.click("#btn-want")
    page.click("#want-format-grid .select-chip[data-fmt='4K UHD']")
    page.click("#btn-want-save")
    page.wait_for_selector("#want-block.has-want")
    assert page.locator(".want-label").inner_text() == "Wanted: 4K UHD"     # owned on Digital HD, so not "Want to buy"
    for sel in ("#btn-want", "#btn-wish", "#btn-want-remove"):
        assert page.locator(sel).bounding_box()["height"] >= 28
    _no_sideways_scroll(page)

    page.click("#btn-wish")
    page.wait_for_selector(".wish-person")
    assert page.locator(".wish-person").count() == 2           # Nick and Lauren, from the Wish Lists app
    page.click(".wish-person:has-text('Lauren')")
    page.click("#btn-wish-add")
    page.wait_for_function("document.querySelector('#want-block .want-pushed')")
    assert "Lauren" in page.locator(".want-pushed").inner_text()
    [(pid, item)] = stub.posts
    assert pid == 3 and item["url"] == "https://www.amazon.com/s?k=The+Dark+Knight+2008+4K+UHD"
    assert item["notes"] == "Movie · 4K UHD · TMDB 155"
    assert _rows("SELECT person_name, wanted_format FROM wish_items") == [("Lauren", "4K UHD")]
    _no_sideways_scroll(page)
    assert errors == []


def test_the_wish_list_button_is_absent_when_the_app_is_down(server, browser, dark_knight):
    page, errors = _page(browser)
    page.goto(f"{server}/admin#/t/{dark_knight}")
    page.click("#btn-want")
    page.click("#btn-want-save")                               # Any format
    page.wait_for_selector("#want-block.has-want")
    assert page.locator(".want-label").inner_text() == "Wanted: any format"
    assert page.locator("#btn-want-remove").count() == 1 and page.locator("#btn-wish").count() == 0
    assert errors == []


def test_a_copy_of_the_format_wanted_ends_the_want(server, browser, dark_knight):
    page, errors = _page(browser)
    page.goto(f"{server}/admin#/t/{dark_knight}")
    page.click("#btn-want")
    page.click("#want-format-grid .select-chip[data-fmt='4K UHD']")
    page.click("#btn-want-save")
    page.wait_for_selector("#want-block.has-want")

    page.click("#btn-add-copy")                                # a Blu-ray leaves the want
    page.click("#format-select-grid .select-chip[data-fmt='Blu-ray']")
    page.click("#btn-save-copy")
    page.wait_for_function("document.querySelectorAll('.copy-row').length === 2")
    assert page.locator("#want-block.has-want").count() == 1
    assert page.locator(".want-label").inner_text() == "Wanted: 4K UHD"

    page.click("#btn-add-copy")                                # a 4K UHD copy ends it
    page.click("#format-select-grid .select-chip[data-fmt='4K UHD']")
    page.click("#btn-save-copy")
    page.wait_for_function("document.querySelectorAll('.copy-row').length === 3")
    page.wait_for_selector("#want-block:not(.has-want)")
    assert page.locator(".want-label").inner_text() == "Not on the wish list"
    assert _rows("SELECT count(*) FROM wants") == [(0,)]
    assert errors == []


def test_remove_a_want(server, browser, dark_knight):
    page, errors = _page(browser)
    page.goto(f"{server}/admin#/t/{dark_knight}")
    page.click("#btn-want")
    page.click("#btn-want-save")
    page.wait_for_selector("#want-block.has-want")
    page.click("#btn-want-remove")
    page.wait_for_selector("#want-block:not(.has-want)")
    assert _rows("SELECT count(*) FROM wants") == [(0,)]
    assert errors == []


def test_the_wanted_view_lists_every_want_as_poster_rows(server, browser, dark_knight, stub):
    import app as core
    con = core.connect()
    sa = seed_title(con, 129, copies=())
    con.execute("INSERT INTO wants (title_id, wanted_format, note, created_at) VALUES (?, '4K UHD', 'the Ghibli box', "
                "'2026-11-01 10:00:00')", (sa,))
    con.execute("INSERT INTO wants (title_id, wanted_format, created_at) VALUES (?, NULL, '2026-11-12 10:00:00')",
                (dark_knight,))
    con.execute("INSERT INTO wish_items (title_id, person_id, person_name, item_id) VALUES (?, 3, 'Lauren', 55)", (sa,))
    con.commit()
    con.close()
    page, errors = _page(browser)
    page.goto(f"{server}/admin#/")
    page.wait_for_selector("#btn-header-wanted")
    page.click("#btn-header-wanted")
    page.wait_for_selector("#wanted-list .wanted-row")
    rows = page.locator(".wanted-row")
    assert rows.count() == 2
    assert "The Dark Knight" in rows.nth(0).inner_text() and "Any format" in rows.nth(0).inner_text()
    assert "Owned: Digital HD" in rows.nth(0).inner_text()
    assert "Spirited Away" in rows.nth(1).inner_text() and "4K UHD" in rows.nth(1).inner_text()
    assert "Lauren" in rows.nth(1).inner_text() and "the Ghibli box" in rows.nth(1).inner_text()
    _no_sideways_scroll(page)
    rows.nth(1).click()
    page.wait_for_selector(".detail-view")
    assert page.locator(".hero-title").inner_text() == "Spirited Away"
    assert errors == []


def test_the_public_pages_know_nothing_about_wants(server, browser, dark_knight):
    import app as core
    con = core.connect()
    con.execute("INSERT INTO wants (title_id, wanted_format, note) VALUES (?, '4K UHD', 'for Grandma Ann')", (dark_knight,))
    con.commit()
    con.close()
    page, errors = _page(browser)
    page.goto(f"{server}/#/t/{dark_knight}")                   # not /admin: the public view
    page.wait_for_selector(".detail-view")
    text = page.locator(".detail-view").inner_text().lower()
    assert "wish" not in text and "wanted" not in text and "grandma" not in text
    assert page.locator("#want-block").count() == 0
    assert page.locator("#btn-header-wanted").count() == 0
    assert errors == []


# ============================================================================== target price (schema v15)
def _open_want_form(page, fmt):
    page.click("#btn-want")
    if fmt:
        page.click(f"#want-format-grid .select-chip[data-fmt='{fmt}']")


def test_the_target_field_follows_the_format_and_is_saved(server, browser, dark_knight):
    page, errors = _page(browser)
    page.goto(f"{server}/admin#/t/{dark_knight}")
    _open_want_form(page, None)                                            # Any format: nothing to watch
    assert not page.locator("#want-target").is_visible()
    page.click("#want-format-grid .select-chip[data-fmt='4K UHD']")
    assert page.locator("#want-target").is_visible()
    assert "Target price, with shipping" in page.locator("label[for='want-target']").inner_text()
    assert "Leave blank to keep it on the list without watching the price." in page.locator(".want-target-group").inner_text()
    assert page.locator("#want-target").bounding_box()["height"] >= 44
    page.fill("#want-target", "12.50")
    page.click("#want-format-grid .select-chip[data-fmt='Digital HD']")   # digital: hidden and cleared
    assert not page.locator("#want-target").is_visible()
    page.click("#want-format-grid .select-chip[data-fmt='Blu-ray']")
    assert page.locator("#want-target").input_value() == ""
    page.fill("#want-target", "12.50")
    _no_sideways_scroll(page)
    page.click("#btn-want-save")
    page.wait_for_selector("#want-block .price-box")
    box = page.locator("#want-block .price-box")
    assert box.get_attribute("data-status") == "target" and box.inner_text().strip() == "Target $12.50"
    assert _rows("SELECT wanted_format, target_cents FROM wants") == [("Blu-ray", 1250)]
    _no_sideways_scroll(page)

    page.click("#btn-want")                                                # the field holds what is stored
    assert page.locator("#want-target").input_value() == "12.50"
    page.fill("#want-target", "")                                          # blank: keep it on the list, stop watching
    page.click("#btn-want-save")
    page.wait_for_selector("#want-block .price-box[data-status='none']")
    assert _rows("SELECT target_cents FROM wants") == [(None,)]
    assert errors == []


def test_the_price_box_without_a_target_opens_the_form(server, browser, dark_knight):
    page, errors = _page(browser)
    page.goto(f"{server}/admin#/t/{dark_knight}")
    _open_want_form(page, "4K UHD")
    page.click("#btn-want-save")
    page.wait_for_selector("#want-block .price-box")
    box = page.locator("#want-block .price-box")
    assert box.get_attribute("data-status") == "none" and "No target price set" in box.inner_text()
    set_one = box.locator(".price-box-set")
    assert set_one.inner_text().startswith("Set one") and set_one.bounding_box()["height"] >= 44
    set_one.click()
    page.wait_for_selector("#want-target")
    assert page.locator("#want-target").is_visible()
    page.fill("#want-target", "$24.99")
    page.click("#btn-want-save")
    page.wait_for_function("document.querySelector('#want-block .price-box').dataset.status === 'target'")
    assert page.locator("#want-block .price-box").inner_text().strip() == "Target $24.99"
    assert _rows("SELECT target_cents FROM wants") == [(2499,)]
    assert errors == []


def test_no_price_box_for_any_or_a_digital_format(server, browser, dark_knight):
    page, errors = _page(browser)
    page.goto(f"{server}/admin#/t/{dark_knight}")
    _open_want_form(page, None)
    page.click("#btn-want-save")
    page.wait_for_selector("#want-block.has-want")
    assert page.locator(".price-box").count() == 0
    _open_want_form(page, "Digital 4K")
    page.click("#btn-want-save")
    page.wait_for_function("document.querySelector('.want-label')?.innerText === 'Wanted: Digital 4K'")
    assert page.locator(".price-box").count() == 0
    assert errors == []


def test_a_refused_price_keeps_the_form_and_what_was_typed(server, browser, dark_knight):
    page, errors = _page(browser)
    page.goto(f"{server}/admin#/t/{dark_knight}")
    _open_want_form(page, "4K UHD")
    page.fill("#want-target", "1000")
    page.click("#btn-want-save")
    page.wait_for_selector("#toast.show")
    assert "999.99" in page.locator("#toast").inner_text()
    assert page.locator("#want-target").input_value() == "1000" and page.locator("#btn-want-save").is_enabled()
    assert _rows("SELECT count(*) FROM wants") == [(0,)]
    assert errors == []


def test_the_wanted_view_shows_the_price_box_per_row(server, browser, dark_knight):
    import app as core
    con = core.connect()
    heat = seed_title(con, 129, copies=())
    con.execute("INSERT INTO wants (title_id, wanted_format, target_cents, created_at) VALUES (?, '4K UHD', 1500, "
                "'2026-11-01 10:00:00')", (heat,))
    con.execute("INSERT INTO wants (title_id, wanted_format, created_at) VALUES (?, '4K UHD', '2026-11-12 10:00:00')",
                (dark_knight,))
    other = seed_title(con, 346648, copies=())
    con.execute("INSERT INTO wants (title_id, wanted_format, created_at) VALUES (?, NULL, '2026-11-20 10:00:00')", (other,))
    con.commit()
    con.close()
    page, errors = _page(browser)
    page.goto(f"{server}/admin#/wanted")
    page.wait_for_selector("#wanted-list .wanted-row")
    rows = page.locator(".wanted-row")
    assert rows.count() == 3
    assert rows.nth(0).locator(".price-box").count() == 0                   # Any format: no box
    none_box, target_box = rows.nth(1).locator(".price-box"), rows.nth(2).locator(".price-box")
    assert none_box.get_attribute("data-status") == "none" and "No target price set" in none_box.inner_text()
    assert target_box.get_attribute("data-status") == "target" and target_box.inner_text().strip() == "Target $15"
    for width in (360, 412):
        page.set_viewport_size({"width": width, "height": 915})
        _no_sideways_scroll(page)

    rows.nth(1).locator(".price-box-set").click()                          # opens the form, does not open the title
    page.wait_for_selector("#want-target")
    assert page.url.endswith("#/wanted")
    page.fill("#want-target", "19.99")
    page.click("#btn-want-save")
    page.wait_for_function("document.querySelectorAll('.wanted-row')[1]?.querySelector('.price-box')?.dataset.status === 'target'")
    assert page.url.endswith("#/wanted")
    assert _rows("SELECT target_cents FROM wants WHERE title_id=?", dark_knight) == [(1999,)]

    rows.nth(2).locator(".result-title").click()                           # the title still opens the title page
    page.wait_for_selector(".detail-view")
    assert page.locator(".hero-title").inner_text() == "Spirited Away"
    assert page.locator("#want-block .price-box").inner_text().strip() == "Target $15"
    assert errors == []


def test_the_public_pages_carry_no_target_price(server, browser, dark_knight):
    import app as core
    con = core.connect()
    con.execute("INSERT INTO wants (title_id, wanted_format, target_cents) VALUES (?, '4K UHD', 1348)", (dark_knight,))
    con.commit()
    con.close()
    page, errors = _page(browser)
    page.goto(f"{server}/#/t/{dark_knight}")
    page.wait_for_selector(".detail-view")
    assert page.locator(".price-box").count() == 0
    assert "13.48" not in page.content() and "target" not in page.locator(".detail-view").inner_text().lower()
    assert errors == []
