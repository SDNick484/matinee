"""Playwright smoke test for the Settings page (spec 2026-11-19-calendar-settings-design §4) at phone width
(412 and 360 px).

Same harness and skip guard as tests/test_people_ui.py (whose fixtures it reuses), against the real app and a
temporary database: the CI "ui-smoke" job runs this file and fails if any test here is skipped.

Scenario: Nick and Lauren are the household, "Media Cabinet" is a shelf holding no copies, and Watch Soon is the
only list.
"""
import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import app as core  # noqa: E402
import test_people_ui as harness  # noqa: E402

server, browser = harness.server, harness.browser   # the same fixtures (a live app on a temp database, Chromium)
VIEWPORTS = pytest.mark.parametrize("width", [412, 360], ids=["412px", "360px"])


@pytest.fixture
def seeded(server):
    con = core.connect()
    con.execute("INSERT INTO people (name, kind, color, sort_order, active) VALUES ('Nick', 'adult', 'green', 0, 1)")
    con.execute("INSERT INTO people (name, kind, color, sort_order, active) VALUES ('Lauren', 'adult', 'blue', 1, 1)")
    con.execute("INSERT INTO locations (name) VALUES ('Media Cabinet')")
    con.commit()
    con.close()
    return server


def _open(browser, server, hash="#/settings", editor=True, width=412):
    page = browser.new_page(viewport={"width": width, "height": 915})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server}/{'admin' if editor else ''}{hash}")
    return page, errors


def _settings(browser, server, width=412):
    page, errors = _open(browser, server, width=width)
    page.wait_for_selector("#settings-people-body .people-row")
    page.wait_for_selector("#settings-locations-body .location-row")
    page.wait_for_selector("#settings-lists-body .lists-row")
    return page, errors


@VIEWPORTS
def test_three_sections_in_order_with_no_side_scroll(seeded, browser, width):
    page, errors = _settings(browser, seeded, width)
    assert page.inner_text(".settings-view h1").strip() == "Settings"
    headings = page.eval_on_selector_all(".settings-section > h2", "els => els.map(e => e.textContent.trim())")
    assert headings == ["People", "Locations", "Lists"]
    assert page.locator("#settings-people-body .people-row").count() == 2
    assert page.locator("#settings-locations-body .location-row").count() == 1
    assert page.locator("#settings-lists-body .lists-row").count() == 1       # Watch Soon, built in
    assert page.evaluate("document.documentElement.scrollWidth") <= width
    assert page.locator(".modal-overlay").count() == 0                          # a page, not a pop-up
    assert errors == []


def test_every_control_is_a_44_px_target(seeded, browser):
    page, errors = _settings(browser, seeded)
    small = page.evaluate("""() => [...document.querySelectorAll('.settings-view button, .settings-view input:not([type=hidden]), .settings-view select')]
        .filter(el => el.offsetParent !== null)
        .map(el => [el.getAttribute('aria-label') || el.textContent.trim() || el.id, el.getBoundingClientRect().height])
        .filter(([, h]) => h < 44)""")
    # the rename / delete buttons of a location are the manager's existing 36 px "xs" buttons; everything else is 44
    assert [name for name, _ in small if "Rename" not in name and "Delete" not in name] == [], small
    assert errors == []


def test_add_a_person_a_location_and_a_list_from_the_page(seeded, browser):
    page, errors = _settings(browser, seeded)

    page.fill("#person-add-name", "Midge")
    page.select_option("#person-add-kind", "kid")
    page.click("#add-person-form button[type=submit]")
    page.wait_for_function("document.querySelectorAll('#settings-people-body .people-row').length === 3")

    page.fill("#loc-add-name", "Shelf 2")
    page.select_option("#loc-add-parent", label="Media Cabinet")
    page.click("#add-location-form button[type=submit]")
    page.wait_for_function("document.querySelectorAll('#settings-locations-body .location-row').length === 2")

    page.fill("#list-add-name", "Halloween")
    page.fill("#list-add-start", "10-01")
    page.fill("#list-add-end", "10-31")
    page.click("#add-list-form button[type=submit]")
    page.wait_for_function("document.querySelectorAll('#settings-lists-body .lists-row').length === 2")

    con = core.connect()
    assert tuple(con.execute("SELECT kind FROM people WHERE name = 'Midge'").fetchone()) == ("kid",)
    assert tuple(con.execute("SELECT parent_id IS NOT NULL FROM locations WHERE name = 'Shelf 2'").fetchone()) == (1,)
    assert tuple(con.execute("SELECT season_start, season_end FROM lists WHERE name = 'Halloween'").fetchone()) == ("10-01", "10-31")
    con.close()
    # a location write redraws its own section only: the others keep their DOM and the page stays put
    assert page.locator("#settings-locations-body .location-path", has_text="Shelf 2").count() == 1
    assert page.locator("#settings-people-body .people-row").count() == 3
    assert page.locator(".settings-view").count() == 1
    assert errors == []


def test_the_header_has_a_gear_and_no_people_locations_or_lists_buttons(seeded, browser):
    page, errors = _open(browser, seeded, "#/")
    page.wait_for_selector("#btn-header-settings")
    gear = page.locator("#btn-header-settings")
    assert gear.get_attribute("aria-label") == "Settings" and gear.get_attribute("href") == "#/settings"
    assert gear.get_attribute("aria-current") is None
    ids = page.eval_on_selector_all("#nav-actions > *", "els => els.map(e => e.id)")
    assert ids[-2:] == ["btn-header-add", "btn-header-settings"]                # after Add Movie, at the end
    for gone in ("btn-header-people", "btn-header-locations", "btn-header-lists"):
        assert page.locator(f"#{gone}").count() == 0
    labels = page.eval_on_selector_all("#nav-actions > *", "els => els.map(e => e.textContent.trim())")
    assert not {"People", "Locations", "Lists"} & set(labels)
    box = gear.bounding_box()
    assert box["width"] >= 44 and box["height"] >= 44
    gear.click()
    page.wait_for_selector(".settings-view")
    assert page.locator("#btn-header-settings").get_attribute("aria-current") == "page"
    assert errors == []


def test_a_public_visitor_gets_the_sign_in_panel_and_no_gear(seeded, browser):
    page, errors = _open(browser, seeded, editor=False)
    page.wait_for_selector(".empty-state")
    assert page.inner_text(".empty-state h2").strip() == "Editor Sign-in Required"
    assert page.locator(".empty-state a.btn").get_attribute("href") == "/admin#/settings"
    assert page.locator(".settings-view").count() == 0 and page.locator("#btn-header-settings").count() == 0
    assert errors == []
