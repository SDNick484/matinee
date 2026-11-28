"""Playwright test for the poster fallback on the grid and the title page (issue #70).

A poster that fails to load is replaced by a placeholder built with DOM calls, so a title with an apostrophe still
gets its placeholder, and a title that holds markup shows that text literally and never runs it.

Same harness and skip guard as tests/test_people_ui.py (whose fixtures it reuses), against the real app and database:
the CI "ui-smoke" job runs this file and fails if any test here is skipped.
"""
import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import app as core  # noqa: E402
from conftest import seed_title  # noqa: E402
import test_people_ui as harness  # noqa: E402

server, browser = harness.server, harness.browser   # the same fixtures (a live app on a temp database, Chromium)


def _seed_missing_poster(name):
    """Seed Elf under `name` with a poster file that is not on disk, so its <img> 404s."""
    con = core.connect()
    tid = seed_title(con, 10719)
    con.execute("UPDATE titles SET title = ?, poster_file = 'missing-poster.jpg' WHERE id = ?", (name, tid))
    con.commit()
    con.close()
    return tid


def _open(browser, url):
    page = browser.new_page(viewport={"width": 412, "height": 915})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    return page, errors


def test_apostrophe_title_gets_its_placeholder_on_grid_and_title_page(server, browser):
    tid = _seed_missing_poster("Schindler's List")
    page, errors = _open(browser, f"{server}/#/")
    ph = page.locator(".grid .poster-ph", has_text="Schindler's List")
    ph.first.wait_for(timeout=10000)
    assert page.locator(".grid img.poster-img").count() == 0          # the broken image is gone
    assert errors == []

    page.goto(f"{server}/#/t/{tid}")
    page.wait_for_selector(".detail-view .poster-ph", timeout=10000)
    assert "Schindler's List" in page.locator(".detail-view .poster-ph").first.inner_text()
    assert errors == []
    page.close()


def test_markup_in_a_title_is_shown_as_text_and_never_run(server, browser):
    payload = "<img src=x onerror=window.__x=1>"
    tid = _seed_missing_poster(payload)
    page, errors = _open(browser, f"{server}/#/")
    ph = page.locator(".grid .poster-ph", has_text=payload)
    ph.first.wait_for(timeout=10000)
    assert page.evaluate("window.__x") is None
    assert page.locator(".grid .poster-ph img").count() == 0

    page.goto(f"{server}/#/t/{tid}")
    page.wait_for_selector(".detail-view .poster-ph", timeout=10000)
    assert payload in page.locator(".detail-view .poster-ph").first.inner_text()
    assert page.evaluate("window.__x") is None
    assert page.locator(".detail-view .poster-ph img").count() == 0
    assert errors == []
    page.close()
