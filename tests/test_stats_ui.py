"""Playwright smoke test for the ratings breakdown on #/stats and the grid's `score` filter (spec
2026-11-18-rating-breakdown-design), at phone width.

Same harness and skip guard as tests/test_lists_ui.py, against the real app and a temporary database: the CI
"ui-smoke" job runs this file and fails if any test here is skipped.

Scenario: The Dark Knight and Elf rated 10.0, Paddington 2 9.5, Harry Potter and the Chamber of Secrets unrated.
"""
import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import app as core  # noqa: E402
from conftest import seed_title  # noqa: E402
import test_people_ui as harness  # noqa: E402

server, browser = harness.server, harness.browser   # the same fixtures (a live app on a temp database, Chromium)


def _seed():
    con = core.connect()
    ids = {"dark": seed_title(con, 155, rating=10.0), "elf": seed_title(con, 10719, rating=10.0),
           "padd": seed_title(con, 346648, rating=9.5), "hp": seed_title(con, 672)}
    con.close()
    return ids


def _page(browser, server, hash, editor=True, width=412):
    page = browser.new_page(viewport={"width": width, "height": 915})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server}/{'admin' if editor else ''}{hash}")
    return page, errors


def _stats(browser, server, editor=True):
    page, errors = _page(browser, server, "#/stats", editor)
    page.wait_for_selector(".stats-grid-overview")
    return page, errors


def _expect_ids(page, ids):
    """Wait until the grid shows exactly these titles (a hash change re-renders it after the URL changes)."""
    want = sorted(ids)
    page.wait_for_function("""(want) => {
        const got = [...document.querySelectorAll('#grid-container .movie-card')].map(e => +e.dataset.id);
        return JSON.stringify(got.sort((a, b) => a - b)) === JSON.stringify(want);
    }""", arg=want, timeout=5000)


def test_stats_rows_per_score_and_tap_through(server, browser):
    ids = _seed()
    page, errors = _stats(browser, server)
    rows = page.locator(".stat-rating-row")
    assert rows.count() == 2
    assert [" ".join(rows.nth(i).inner_text().split()) for i in range(2)] == ["★ 10.0 2", "★ 9.5 1"]
    assert rows.nth(0).get_attribute("href") == "#/?score=10"
    assert rows.nth(1).get_attribute("href") == "#/?score=9.5"
    widths = [rows.nth(i).locator(".stat-progress-fill").evaluate("e => e.style.width") for i in range(2)]
    assert widths == ["100%", "50%"]
    for i in range(2):
        assert rows.nth(i).bounding_box()["height"] >= 44
    assert page.evaluate("document.documentElement.scrollWidth") == 412

    rows.nth(0).click()
    page.wait_for_url("**#/?score=10")
    _expect_ids(page, {ids["dark"], ids["elf"]})
    assert " ".join(page.locator("#popover-rating .filter-btn").inner_text().split()).startswith("Rating: ★ 10.0")
    assert errors == []


def test_rating_menu_replaces_and_clears_score(server, browser):
    ids = _seed()
    page, errors = _page(browser, server, "#/?score=9.5")
    _expect_ids(page, {ids["padd"]})
    page.click("#popover-rating .filter-btn")
    exact = page.locator('#popover-rating input[data-facet="score"]')
    assert exact.is_checked()
    assert not page.locator('#popover-rating input[data-facet="rating"][data-val=""]').is_checked()
    page.click('#popover-rating input[data-facet="rating"][data-val="8+"]')
    page.wait_for_url("**#/?rating=8%2B")
    _expect_ids(page, {ids["dark"], ids["elf"], ids["padd"]})
    page.close()

    page, errors = _page(browser, server, "#/?score=9.5")
    page.wait_for_selector('#popover-rating')
    page.click("#popover-rating .filter-btn")
    page.click('#popover-rating input[data-facet="score"]')
    page.wait_for_url("**#/")
    _expect_ids(page, set(ids.values()))
    page.close()

    page, errors = _page(browser, server, "#/?score=10")
    page.wait_for_selector('#popover-rating')
    page.click("#filter-reset-btn")
    page.wait_for_url("**#/")
    _expect_ids(page, set(ids.values()))
    assert errors == []


def test_no_ratings_no_rows(server, browser):
    con = core.connect()
    seed_title(con, 10719)
    con.close()
    page, errors = _stats(browser, server)
    assert page.locator(".stat-rating-row").count() == 0
    assert "No ratings yet" in page.locator(".stats-activity-grid").inner_text()
    assert errors == []


def test_public_view_has_the_same_rows(server, browser, monkeypatch):
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    ids = _seed()
    page, errors = _stats(browser, server, editor=False)
    rows = page.locator(".stat-rating-row")
    assert rows.count() == 2
    rows.nth(0).click()
    page.wait_for_url("**#/?score=10")
    _expect_ids(page, {ids["dark"], ids["elf"]})
    assert errors == []
