"""Playwright test for the collection search and the grid's scroll position (issue #79).

Typing in the search box updates the results without rebuilding the box, so focus (and a phone's keyboard)
survives a pause in typing; the URL follows with history.replaceState, the facets are fetched once, and a slow
response never overwrites a newer one. The grid restores its scroll once, when coming back from a title.

Same harness and skip guard as tests/test_people_ui.py (whose fixtures it reuses), against the real app and
database: the CI "ui-smoke" job runs this file and fails if any test here is skipped.
"""
import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import app as core  # noqa: E402
from conftest import FIXTURES_TMDB, seed_title  # noqa: E402
import test_people_ui as harness  # noqa: E402

server, browser = harness.server, harness.browser   # the same fixtures (a live app on a temp database, Chromium)

DEBOUNCE_PAUSE_MS = 800   # well past the 350 ms search debounce


def _seed_all():
    """Seed every recorded movie, so the grid is several screens tall; returns {title: id}."""
    import json

    con = core.connect()
    ids = {}
    for f in sorted(FIXTURES_TMDB.glob("movie-*.json")):
        d = json.loads(f.read_text())
        ids[d["title"]] = seed_title(con, d["id"])
    con.close()
    return ids


def _open(browser, url, height=915):
    page = browser.new_page(viewport={"width": 412, "height": height})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    page.wait_for_selector(".movie-card", timeout=10000)
    return page, errors


def _card_titles(page):
    return page.locator(".movie-card .card-title").all_inner_texts()


def test_typing_keeps_focus_and_does_not_rebuild_the_search_box(server, browser):
    _seed_all()
    page, errors = _open(browser, f"{server}/")
    requests = []
    page.on("request", lambda r: requests.append(r.url))
    history_before = page.evaluate("history.length")
    page.evaluate("window.__box = document.getElementById('grid-search')")

    page.click("#grid-search")
    page.keyboard.type("dark")
    page.wait_for_function("document.querySelectorAll('.movie-card').length === 1")   # the debounce has run
    page.wait_for_timeout(DEBOUNCE_PAUSE_MS)
    assert page.evaluate("document.activeElement === window.__box && window.__box.isConnected")
    page.keyboard.type(" knight")
    page.wait_for_timeout(DEBOUNCE_PAUSE_MS)

    assert page.evaluate("document.activeElement === window.__box && window.__box.isConnected")
    assert page.input_value("#grid-search") == "dark knight"
    assert _card_titles(page) == ["The Dark Knight"]
    assert page.evaluate("history.length") - history_before <= 1
    assert page.evaluate("location.hash") == "#/?q=dark+knight"
    assert [u for u in requests if "/facets" in u] == []    # a search-only change does not refetch the facets
    assert errors == []
    page.close()


def test_clearing_the_search_brings_every_title_back(server, browser):
    _seed_all()
    page, errors = _open(browser, f"{server}/")
    total = page.locator(".movie-card").count()
    page.fill("#grid-search", "dark")
    page.wait_for_function("document.querySelectorAll('.movie-card').length === 1")
    page.click("#grid-clear-search")
    page.wait_for_function(f"document.querySelectorAll('.movie-card').length === {total}")
    assert page.input_value("#grid-search") == ""
    assert page.locator("#grid-clear-search").is_hidden()
    assert page.evaluate("location.hash") in ("#/", "")
    assert errors == []
    page.close()


def test_a_hash_typed_by_hand_still_searches(server, browser):
    _seed_all()
    page, errors = _open(browser, f"{server}/")
    page.evaluate("location.hash = '#/?q=toy&sort=year'")
    page.wait_for_function("document.querySelectorAll('.movie-card').length < 10")
    assert page.input_value("#grid-search") == "toy"
    assert page.input_value("#grid-sort") == "year"
    assert all("Toy" in t for t in _card_titles(page))
    assert page.locator("#grid-clear-search").is_visible()
    page.evaluate("location.hash = '#/'")
    page.wait_for_function("document.querySelectorAll('.movie-card').length > 20")
    assert page.input_value("#grid-search") == ""
    assert errors == []
    page.close()


def test_a_slow_response_never_overwrites_a_newer_one(server, browser):
    _seed_all()
    page, errors = _open(browser, f"{server}/")
    held = []

    def hold_toy(route):
        if route.request.url.endswith("q=toy"):
            held.append(route)   # answered only when the test says so
        else:
            route.continue_()

    page.route("**/titles?*", hold_toy)
    page.fill("#grid-search", "toy")
    page.wait_for_function("document.querySelectorAll('.movie-card').length > 0")
    page.wait_for_timeout(DEBOUNCE_PAUSE_MS)
    assert len(held) == 1
    page.fill("#grid-search", "toy story")
    page.wait_for_function("document.querySelectorAll('.movie-card').length === 3")
    held[0].continue_()   # the older, broader answer arrives last
    page.wait_for_timeout(500)
    assert sorted(_card_titles(page)) == ["Toy Story", "Toy Story 2", "Toy Story 3"]
    assert errors == []
    page.close()


def _card_in_view(page):
    """Click the first poster whose top edge is on screen."""
    idx = page.evaluate(
        """() => [...document.querySelectorAll('.movie-card')].findIndex((c) => {
            const r = c.getBoundingClientRect(); return r.top > 60 && r.bottom < innerHeight; })"""
    )
    assert idx >= 0
    page.locator(".movie-card").nth(idx).click()


def test_back_from_a_title_restores_the_scroll_once_and_a_later_filter_starts_at_the_top(server, browser):
    _seed_all()
    page, errors = _open(browser, f"{server}/", height=600)
    assert page.evaluate("document.documentElement.scrollHeight") > 1500   # long enough to scroll
    page.evaluate("window.scrollTo(0, 700)")
    page.wait_for_function("window.scrollY === 700")
    _card_in_view(page)
    page.wait_for_selector(".detail-view", timeout=10000)

    page.go_back()
    page.wait_for_selector(".movie-card", timeout=10000)
    page.wait_for_function("window.scrollY === 700")

    page.select_option("#grid-sort", "year")   # a later sort change starts at the top
    page.wait_for_function("window.scrollY === 0")
    assert page.input_value("#grid-sort") == "year"

    page.evaluate("window.scrollTo(0, 500)")
    page.fill("#grid-search", "harry")   # and so does a later search
    page.wait_for_function("document.querySelectorAll('.movie-card').length === 4")
    page.wait_for_function("window.scrollY === 0")
    assert errors == []
    page.close()
