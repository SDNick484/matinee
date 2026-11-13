"""Playwright smoke test for lists on the title page and in the admin header at phone width (spec
2026-11-12-lists-design §5).

Same harness and skip guard as tests/test_people_ui.py (whose fixtures it reuses), but against the real app and
database rather than stubbed routes: the CI "ui-smoke" job runs this file and fails if any test here is skipped.

Scenario: Elf is on Watch Soon and Christmas (11-25 to 01-01); Paddington 2 is on no list.
"""
import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import app as core  # noqa: E402
from conftest import seed_title  # noqa: E402
import test_people_ui as harness  # noqa: E402

server, browser = harness.server, harness.browser   # the same fixtures (a live app on a temp database, Chromium)

LONG = ["Halloween Spooky Season Picks", "Thanksgiving Family Afternoon", "Summer Road Trip Movies",
        "Rainy Saturday Matinee Ideas", "Grandma Ann's Favourites", "Back to School Week"]


def _seed(extra_lists=()):
    con = core.connect()
    elf, paddington = seed_title(con, 10719), seed_title(con, 346648)
    soon = con.execute("SELECT id FROM lists WHERE kind = 'watch_soon'").fetchone()[0]
    xmas = con.execute("INSERT INTO lists (name, kind, season_start, season_end, sort_order) "
                       "VALUES ('Christmas', 'seasonal', '11-25', '01-01', 1)").lastrowid
    con.executemany("INSERT INTO list_titles (list_id, title_id) VALUES (?, ?)", [(soon, elf), (xmas, elf)])
    for i, name in enumerate(extra_lists):
        lid = con.execute("INSERT INTO lists (name, kind, sort_order) VALUES (?, 'custom', ?)", (name, i + 2)).lastrowid
        con.execute("INSERT INTO list_titles (list_id, title_id) VALUES (?, ?)", (lid, elf))
    con.commit()
    con.close()
    return {"elf": elf, "paddington": paddington, "soon": soon, "xmas": xmas}


def _page(browser, url, width=412):
    page = browser.new_page(viewport={"width": width, "height": 915})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_selector(".detail-view")
    return page, errors


def _no_hscroll(page, width):
    sw = page.evaluate("document.documentElement.scrollWidth")
    assert sw <= width, f"horizontal scroll at {width}px: scrollWidth={sw}"


def test_anonymous_title_page_shows_chips_and_a_chip_opens_its_list(server, browser):
    ids = _seed()
    page, errors = _page(browser, f"{server}/#/t/{ids['elf']}")
    chips = page.locator("#list-chips .list-chip")
    assert [chips.nth(i).inner_text().strip() for i in range(chips.count())] == ["Watch Soon", "Christmas"]
    assert page.locator("#btn-title-lists").count() == 0               # no editor control for a visitor
    _no_hscroll(page, 412)
    chips.nth(1).click()
    dialog = page.locator(".list-dialog")
    dialog.wait_for()
    assert "Christmas" in dialog.locator(".modal-title").inner_text()
    assert "Nov 25 – Jan 1" in dialog.locator(".list-sub").inner_text()
    rows = dialog.locator(".list-title-row")
    assert rows.count() == 1 and "Elf" in rows.first.inner_text()
    assert rows.first.bounding_box()["height"] >= 44
    # a title on no list shows no chip row at all to a visitor
    page.goto(f"{server}/#/t/{ids['paddington']}")
    page.wait_for_selector(".detail-view .hero-title")
    page.wait_for_function("document.querySelector('.hero-title').textContent.includes('Paddington')")
    assert page.locator("#list-chips").count() == 0
    assert errors == []


def test_many_long_chips_wrap_without_horizontal_scroll(server, browser):
    ids = _seed(LONG)
    for width in (412, 360):
        page, errors = _page(browser, f"{server}/admin#/t/{ids['elf']}", width)
        chips = page.locator("#list-chips .list-chip")
        assert chips.count() == 2 + len(LONG)
        tops = {round(chips.nth(i).bounding_box()["y"]) for i in range(chips.count())}
        assert len(tops) > 1, "the chips did not wrap onto more than one line"
        for i in range(chips.count()):
            box = chips.nth(i).bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= width, chips.nth(i).inner_text()
        _no_hscroll(page, width)
        assert errors == []
        page.close()


def test_membership_sheet_fits_one_screen_and_saves(server, browser):
    ids = _seed(LONG)
    page, errors = _page(browser, f"{server}/admin#/t/{ids['paddington']}")
    assert page.locator("#list-chips .list-chip").count() == 0
    page.click("#btn-title-lists")
    dialog = page.locator(".list-membership-dialog")
    dialog.wait_for()
    toggles = dialog.locator(".list-toggle")
    assert toggles.count() == 2 + len(LONG)
    assert all(toggles.nth(i).bounding_box()["height"] >= 44 for i in range(toggles.count()))
    assert all(toggles.nth(i).get_attribute("aria-pressed") == "false" for i in range(toggles.count()))
    box = dialog.bounding_box()
    assert box["y"] >= 0 and box["y"] + box["height"] <= 915, box
    assert page.evaluate("(d) => d.scrollHeight <= d.clientHeight", dialog.element_handle()), "the sheet scrolls"
    assert dialog.locator("#btn-modal-cancel").bounding_box()["height"] >= 44
    _no_hscroll(page, 412)

    toggles.first.click()                                                      # Watch Soon on
    page.wait_for_selector('.list-toggle[data-list-id="%d"][aria-pressed="true"]' % ids["soon"])
    con = core.connect()
    assert con.execute("SELECT list_id FROM list_titles WHERE title_id = ?",
                       (ids["paddington"],)).fetchall()[0][0] == ids["soon"]
    toggles.first.click()                                                      # and off again
    page.wait_for_selector('.list-toggle[data-list-id="%d"][aria-pressed="false"]' % ids["soon"])
    assert con.execute("SELECT count(*) FROM list_titles WHERE title_id = ?", (ids["paddington"],)).fetchone()[0] == 0
    dialog.locator('.list-toggle[data-list-id="%d"]' % ids["xmas"]).click()     # Christmas on, then Done
    page.wait_for_selector('.list-toggle[data-list-id="%d"][aria-pressed="true"]' % ids["xmas"])
    page.click("#btn-modal-cancel")
    page.wait_for_selector("#list-chips .list-chip")                           # the page re-rendered
    assert page.locator("#list-chips .list-chip").inner_text().strip() == "Christmas"
    con.close()
    assert errors == []


def test_lists_manager_in_the_admin_header(server, browser):
    ids = _seed()
    page, errors = _page(browser, f"{server}/admin#/t/{ids['elf']}")
    page.click("#btn-header-lists")
    dialog = page.locator(".lists-dialog")
    dialog.wait_for()
    rows = dialog.locator(".lists-row")
    assert rows.count() == 2
    soon = rows.nth(0)
    assert soon.locator(".lists-name").get_attribute("readonly") is not None
    assert soon.locator('[data-act="delete"]').count() == 0
    assert rows.nth(1).locator('[data-field="season_start"]').input_value() == "11-25"
    page.fill("#list-add-name", "Halloween")
    page.fill("#list-add-start", "10-01")
    page.fill("#list-add-end", "10-31")
    page.click("#add-list-form button[type=submit]")
    page.wait_for_function("document.querySelectorAll('.lists-dialog .lists-row').length === 3")
    con = core.connect()
    row = con.execute("SELECT kind, season_start, season_end FROM lists WHERE name = 'Halloween'").fetchone()
    assert tuple(row) == ("seasonal", "10-01", "10-31")
    con.close()
    for button in dialog.locator("button").all():
        if button.is_visible():
            assert button.bounding_box()["height"] >= 44, button.inner_text()
    _no_hscroll(page, 412)
    assert errors == []
