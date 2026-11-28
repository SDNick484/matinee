"""Playwright smoke test for the shared modal (static/js/modal.js) at 412 px: every modal and sheet is a labelled dialog,
takes focus in, keeps Tab inside, closes on Escape and hands focus back to what opened it. Also the search result as a
button, the filter popovers as labelled groups, reduced motion, and the 44 px tap targets on a touch screen.

Same harness and skip guard as tests/test_wishlist_ui.py (a live app on a temp database, a stub Wish Lists app, Chromium);
the CI "ui-smoke" job runs this file and fails if any test here is skipped.

Scenario: The Dark Knight is owned as Digital HD on Movies Anywhere; Heat is on a shelf in 4K UHD and on YouTube.
"""
import json

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

from test_play_core import seed_film, seed_shelf  # noqa: E402
import test_wishlist_ui as harness  # noqa: E402

server, browser, stub, dark_knight = harness.server, harness.browser, harness.stub, harness.dark_knight   # the same fixtures
SPIRITED_AWAY = harness.SPIRITED_AWAY

HEAT_YT = "hT9vXq2LmPa"
TABBABLES = """(sel) => {
  const d = document.querySelector(sel);
  return [...d.querySelectorAll('a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), '
    + 'select:not([disabled]), textarea:not([disabled]), summary, [tabindex]:not([tabindex="-1"])')]
    .filter((el) => el.getClientRects().length > 0 && getComputedStyle(el).visibility !== 'hidden');
}"""
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4890000000d49444154789c63f8cfc0f01f0005000201"
    "a8a1a1370000000049454e44ae426082")


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


def _focus_in(page, sel):
    return page.evaluate("(sel) => document.querySelector(sel).contains(document.activeElement)", sel)


def _holds_and_gives_back(page, dialog_sel, opener_sel):
    """The dialog is labelled, holds focus through Tab and Shift+Tab, and Escape closes it with focus back on the opener."""
    dialog = page.locator(dialog_sel)
    dialog.wait_for()
    assert dialog.get_attribute("role") == "dialog" and dialog.get_attribute("aria-modal") == "true"
    label = dialog.get_attribute("aria-labelledby")
    assert label and page.locator(f"[id='{label}']").inner_text().strip(), "the dialog has no heading to name it"
    assert _focus_in(page, dialog_sel), "focus did not move into the dialog"

    n = page.evaluate(f"(sel) => ({TABBABLES})(sel).length", dialog_sel)
    assert n >= 1
    page.evaluate(f"(sel) => {{ const t = ({TABBABLES})(sel); t[t.length - 1].focus(); }}", dialog_sel)
    page.keyboard.press("Tab")
    assert page.evaluate(f"(sel) => document.activeElement === ({TABBABLES})(sel)[0]", dialog_sel), "Tab from the last control left the first"
    page.keyboard.press("Shift+Tab")
    assert page.evaluate(f"(sel) => {{ const t = ({TABBABLES})(sel); return document.activeElement === t[t.length - 1]; }}",
                         dialog_sel), "Shift+Tab from the first control did not wrap to the last"
    for _ in range(n + 2):
        page.keyboard.press("Tab")
        assert _focus_in(page, dialog_sel), "Tab left the dialog"

    page.keyboard.press("Escape")
    page.wait_for_function("(sel) => !document.querySelector(sel)", arg=dialog_sel)
    assert page.evaluate("(sel) => document.activeElement.matches(sel)", opener_sel), "focus did not return to the opener"


def _open_by_click(page, opener_sel):
    page.locator(opener_sel).first.click()


def _stub_search(page, *results):
    page.route("**/admin/api/tmdb/search?*", lambda r: r.fulfill(
        content_type="application/json", body=json.dumps({"results": list(results), "q": "x"})))


def test_copy_modal_holds_focus_and_returns_it(server, browser):
    page, errors = _page(browser)
    _stub_search(page, SPIRITED_AWAY)
    page.goto(f"{server}/admin#/add")
    page.fill("#tmdb-search-input", "spirited")
    _open_by_click(page, ".result-card")
    _holds_and_gives_back(page, ".modal-dialog", ".result-card")
    assert errors == []


def test_copy_modal_replaced_by_want_returns_focus_to_the_search_result(server, browser):
    page, errors = _page(browser)
    _stub_search(page, SPIRITED_AWAY)
    page.goto(f"{server}/admin#/add")
    page.fill("#tmdb-search-input", "spirited")
    _open_by_click(page, ".result-card")
    page.locator("#btn-want-to-buy").wait_for(state="visible")
    page.click("#btn-want-to-buy")
    page.locator("#want-form").wait_for()
    assert page.locator(".modal-overlay").count() == 1                 # replaced, not stacked
    _holds_and_gives_back(page, ".modal-dialog", ".result-card")
    assert errors == []


def test_the_copy_forms_close_and_cancel_and_backdrop(server, browser, dark_knight):
    page, errors = _page(browser)
    page.goto(f"{server}/admin#/t/{dark_knight}")
    page.wait_for_selector("#btn-add-copy")
    for how in ("cancel", "close", "backdrop"):
        page.click("#btn-add-copy")
        page.locator("#copy-form").wait_for()
        if how == "cancel":
            page.click("#btn-modal-cancel")
        elif how == "close":
            page.click("#btn-modal-close")
        else:
            page.mouse.click(3, 3)
        assert page.locator(".modal-overlay").count() == 0, how
        assert page.evaluate("document.activeElement.id") == "btn-add-copy", how
    # a press that starts inside the dialog and ends on the backdrop is a text selection, not a tap outside
    page.click("#btn-add-copy")
    box = page.locator(".modal-dialog").bounding_box()
    page.mouse.move(box["x"] + 20, box["y"] + 20)
    page.mouse.down()
    page.mouse.move(3, 3)
    page.mouse.up()
    assert page.locator(".modal-overlay").count() == 1
    assert errors == []


def test_edit_copy_modal(server, browser, dark_knight):
    page, errors = _page(browser)
    page.goto(f"{server}/admin#/t/{dark_knight}")
    _open_by_click(page, ".btn-edit-copy")
    page.locator("#edit-copy-form").wait_for()
    _holds_and_gives_back(page, ".modal-dialog", ".btn-edit-copy")
    assert errors == []


def test_want_and_wish_list_modals(server, browser, dark_knight, stub):
    import app as core
    con = core.connect()
    con.execute("INSERT INTO wants (title_id, wanted_format) VALUES (?, '4K UHD')", (dark_knight,))
    con.commit()
    con.close()
    page, errors = _page(browser)
    page.goto(f"{server}/admin#/t/{dark_knight}")
    page.wait_for_selector("#btn-wish")
    _open_by_click(page, "#btn-want")
    page.locator("#want-form").wait_for()
    _holds_and_gives_back(page, ".modal-dialog", "#btn-want")
    _open_by_click(page, "#btn-wish")
    page.locator("#wish-form").wait_for()
    _holds_and_gives_back(page, ".modal-dialog", "#btn-wish")
    assert errors == []


def test_movie_night_modal_and_batch_review_and_cover_preview(server, browser, dark_knight):
    page, errors = _page(browser)
    page.goto(f"{server}/admin")
    page.wait_for_selector(".movie-card")
    _open_by_click(page, "#btn-header-movie-night")
    page.locator(".movie-night-dialog").wait_for()
    _holds_and_gives_back(page, ".movie-night-dialog", "#btn-header-movie-night")

    page.route("**/admin/api/upc/*", lambda r: r.fulfill(content_type="application/json", body=json.dumps({"results": []})))
    page.goto(f"{server}/admin#/add")
    page.wait_for_selector("#tab-scan")
    page.evaluate("localStorage.setItem('matinee_scan_queue', JSON.stringify(['883929637581']))")
    page.click("#tab-scan")
    page.locator("#btn-batch-review").click()
    page.wait_for_selector("#btn-batch-import-all")
    _holds_and_gives_back(page, ".modal-dialog", "#btn-batch-review")
    assert page.locator("#btn-batch-review").is_visible()
    page.locator("#btn-batch-review").click()
    close = page.locator("#btn-modal-close")
    assert close.get_attribute("aria-label") == "Close"                # the batch modal's close button has a name
    close.click()
    assert page.locator(".modal-overlay").count() == 0

    page.goto(f"{server}/admin#/t/{dark_knight}")
    page.wait_for_selector("#btn-change-cover")
    page.focus("#btn-change-cover")
    page.set_input_files("#input-cover", files=[{"name": "cover.png", "mimeType": "image/png", "buffer": PNG}])
    page.locator(".cover-preview-dialog").wait_for()
    _holds_and_gives_back(page, ".cover-preview-dialog", "#btn-change-cover")
    assert errors == []


def test_list_and_membership_sheets(server, browser, dark_knight):
    import app as core
    con = core.connect()
    xmas = con.execute("INSERT INTO lists (name, kind, season_start, season_end, sort_order) "
                       "VALUES ('Christmas', 'seasonal', '11-25', '01-01', 1)").lastrowid
    con.execute("INSERT INTO list_titles (list_id, title_id) VALUES (?, ?)", (xmas, dark_knight))
    con.commit()
    con.close()
    page, errors = _page(browser)
    page.goto(f"{server}/admin#/t/{dark_knight}")
    page.wait_for_selector("#btn-title-lists")
    _open_by_click(page, "#btn-title-lists")
    page.locator(".list-membership-dialog").wait_for()
    _holds_and_gives_back(page, ".list-membership-dialog", "#btn-title-lists")
    _open_by_click(page, "#list-chips .list-chip")
    page.locator(".list-dialog").wait_for()
    _holds_and_gives_back(page, ".list-dialog", ".list-chip")
    assert errors == []


def test_calendar_day_sheet(server, browser, dark_knight):
    import app as core
    con = core.connect()
    con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, '2025-11-14')", (dark_knight,))
    con.commit()
    con.close()
    page, errors = _page(browser)
    page.goto(f"{server}/#/calendar/2025-11")
    page.wait_for_selector("#calendar-grid .cal-num")
    _open_by_click(page, 'button.cal-day[data-date="2025-11-14"]')
    page.locator("#calendar-sheet").wait_for()
    _holds_and_gives_back(page, "#calendar-sheet", 'button.cal-day[data-date="2025-11-14"]')
    assert errors == []


def test_play_sheet_over_movie_night_closes_alone(server, browser, monkeypatch):
    import app as core
    monkeypatch.setattr("app.HA_PLAY_WEBHOOK_URL", "http://127.0.0.1:1/api/webhook/unused")
    con = core.connect()
    shelf = seed_shelf(con)
    heat = seed_film(con, "Heat", 1995, "R", copies=[("physical", "4K UHD", None, None, shelf),
                                                     ("digital", "Digital HD", "Apple TV", HEAT_YT, None)])
    con.close()
    page, errors = _page(browser)
    page.goto(f"{server}/admin")
    page.wait_for_selector(".movie-card")
    play = page.locator(f'.movie-card[data-id="{heat["id"]}"] [data-play-title]')
    play.click()
    page.locator(".play-sheet").wait_for()
    _holds_and_gives_back(page, ".play-sheet", f'.movie-card[data-id="{heat["id"]}"] [data-play-title]')

    # over Movie Night, Escape closes the sheet and leaves Movie Night open, with focus back inside it
    page.click("#btn-header-movie-night")
    page.click('.movie-night-tab[data-mode="never_watched"]')
    page.wait_for_selector(".movie-night-card")
    page.evaluate("""() => {
      const b = document.createElement('button'); b.type = 'button'; b.id = 'probe'; b.setAttribute('data-play-title', %d);
      document.querySelector('#movie-night-content').appendChild(b);
    }""" % heat["id"])
    page.focus("#probe")
    page.evaluate("document.querySelector('#probe').click()")
    page.locator(".play-sheet").wait_for()
    page.keyboard.press("Escape")
    page.wait_for_function("!document.querySelector('.play-sheet')")
    assert page.locator(".movie-night-dialog").count() == 1
    assert page.evaluate("document.activeElement.id") == "probe"
    page.keyboard.press("Escape")
    assert page.locator(".movie-night-dialog").count() == 0
    assert errors == []


def test_a_search_result_is_a_button_and_works_from_the_keyboard(server, browser):
    page, errors = _page(browser)
    _stub_search(page, SPIRITED_AWAY)
    page.goto(f"{server}/admin#/add")
    page.fill("#tmdb-search-input", "spirited")
    card = page.locator(".result-card")
    card.wait_for()
    assert card.evaluate("el => el.tagName") == "BUTTON"
    card.focus()
    page.keyboard.press("Enter")
    page.locator("#copy-form").wait_for()
    assert errors == []


def test_filter_popovers_are_labelled_groups(server, browser, dark_knight):
    page, errors = _page(browser)
    page.goto(f"{server}/admin")
    page.wait_for_selector(".filter-popover")
    menus = page.locator("#popover-medium .popover-menu, #popover-genre .popover-menu, #popover-watched .popover-menu, "
                         "#popover-rating .popover-menu")
    assert menus.count() >= 3
    for i in range(menus.count()):
        assert menus.nth(i).get_attribute("role") == "group"
        assert (menus.nth(i).get_attribute("aria-label") or "").strip()
    assert page.locator(".filter-popover [role=menu]:not(#grid-export)").count() == 0
    assert page.locator(".filter-popover > .filter-btn[aria-haspopup=true]:not(.export-btn)").count() == 0
    assert errors == []


def test_reduced_motion_removes_transitions_and_animations(server, browser):
    page, _ = _page(browser)
    page.emulate_media(reduced_motion="reduce")
    page.goto(f"{server}/admin")
    page.wait_for_selector(".filter-btn")
    assert page.evaluate("getComputedStyle(document.querySelector('.filter-btn')).transitionDuration") in ("0s", "1e-05s")
    page.emulate_media(reduced_motion="no-preference")
    assert page.evaluate("getComputedStyle(document.querySelector('.filter-btn')).transitionDuration") == "0.15s"


def test_small_controls_reach_44_px_on_a_touch_screen(server, browser, dark_knight):
    import app as core
    con = core.connect()
    con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, '2025-11-14')", (dark_knight,))
    con.commit()
    con.close()
    ctx = browser.new_context(viewport={"width": 412, "height": 915}, has_touch=True, is_mobile=True)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/admin/api/tmdb/search?*", lambda r: r.fulfill(
        content_type="application/json", body=json.dumps({"results": [], "q": "x"})))

    def check(sel):
        loc = page.locator(sel)
        assert loc.count(), sel
        for i in range(loc.count()):
            box = loc.nth(i).bounding_box()
            assert box and box["height"] >= 43.5 and box["width"] >= 43.5, (sel, box)

    page.goto(f"{server}/admin#/?watched=unwatched")
    page.locator("#filter-reset-btn").wait_for()
    check("#filter-reset-btn")
    page.fill("#grid-search", "dark")
    check("#grid-clear-search")
    page.goto(f"{server}/admin#/t/{dark_knight}")
    page.wait_for_selector(".nav-arrow-btn")
    check(".nav-arrow-btn")
    check(".btn-sm")
    page.click("#btn-add-copy")
    page.locator("#copy-form").wait_for()
    check(".select-chip")
    check(".toggle-btn")
    page.click("#btn-modal-close")
    page.goto(f"{server}/admin#/add")
    page.fill("#tmdb-search-input", "dark")
    assert errors == []
    ctx.close()
