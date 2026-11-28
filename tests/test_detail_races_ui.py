"""Playwright test for the title page's races (issue #80), at phone width (412 px).

Same harness and skip guard as tests/test_copy_details_ui.py: skipped when playwright or Chromium is missing, and the
CI "ui-smoke" job fails if any test here is skipped. A real server and database; the browser's requests are held with
a route handler and released by the test, so each order of events is the one asserted, not a timing guess.

Scenarios, with The Dark Knight (TMDB 155) and Inception (TMDB 27205):
- "Refresh from TMDB", then Back before the answer: the grid stays on screen and the late answer draws nothing.
- A double click on "+ Watched Today" creates one watch.
- "Save Copy" the moment the form opens, before the owned-copies answer: the copy is saved and the page does not reload.
- A deep link paints the title, and sets the browser tab's title, before the collection (Prev/Next) request resolves.
"""
import glob
import os
import re
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

WIDTH = 412


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
def titles(server):
    from app import db
    from conftest import seed_title
    con = db()
    ids = {"dark_knight": seed_title(con, 155, copies=("4K UHD",)), "inception": seed_title(con, 27205)}
    return ids


def new_page(browser):
    page = browser.new_page(viewport={"width": WIDTH, "height": 915})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    return page, errors


def hold(page, pattern, held):
    """Park every request matching `pattern` (a glob or regex) in `held` until the test releases it."""
    page.route(pattern, lambda route: held.append(route))


def wait_held(page, held, n=1):
    for _ in range(100):
        if len(held) >= n:
            return
        page.wait_for_timeout(50)
    raise AssertionError(f"only {len(held)} of {n} requests arrived")


def watch_count(tid):
    from app import db
    return db().execute("SELECT COUNT(*) FROM watches WHERE title_id=?", (tid,)).fetchone()[0]


def copy_count(tid):
    from app import db
    return db().execute("SELECT COUNT(*) FROM copies WHERE title_id=?", (tid,)).fetchone()[0]


def test_refresh_then_back_leaves_the_grid(server, browser, titles):
    page, errors = new_page(browser)
    refreshes = []
    hold(page, "**/admin/api/titles/*/refresh", refreshes)
    page.goto(f"{server}/admin#/")
    page.click(".movie-card >> text=The Dark Knight")
    page.wait_for_selector(".detail-view .hero-title")
    page.click("#btn-refresh-tmdb")
    wait_held(page, refreshes)
    page.click("#btn-back")
    page.wait_for_selector(".movie-card")
    refreshes[0].fulfill(status=200, content_type="application/json", body="{}")
    page.wait_for_timeout(500)   # long enough for a late redraw to show up
    assert page.locator(".detail-view").count() == 0
    assert page.locator(".movie-card").count() == 2
    assert page.evaluate("location.hash") == "#/"
    assert page.title() == "Matinee"
    assert errors == []


def test_double_click_on_watched_today_logs_one_watch(server, browser, titles):
    tid = titles["dark_knight"]
    page, errors = new_page(browser)
    posts = []
    hold(page, "**/admin/api/titles/*/watches", posts)
    page.goto(f"{server}/admin#/t/{tid}")
    page.wait_for_selector("#btn-watched-today")
    page.dblclick("#btn-watched-today")
    wait_held(page, posts)
    assert page.locator("#btn-watched-today").is_disabled()
    page.wait_for_timeout(300)   # a second request, were there one, has arrived by now
    assert len(posts) == 1
    posts[0].continue_()
    page.wait_for_selector(".watch-row")
    assert page.locator("#btn-watched-today").is_enabled()   # the page was drawn again, its buttons live
    assert watch_count(tid) == 1
    assert page.locator(".watch-row").count() == 1
    assert errors == []


def test_save_copy_before_the_owned_answer_saves_and_does_not_reload(server, browser, titles):
    tid = titles["dark_knight"]
    page, errors = new_page(browser)
    owned = []
    hold(page, "**/admin/api/tmdb/*/owned", owned)
    page.goto(f"{server}/admin#/t/{tid}")
    page.wait_for_selector("#btn-add-copy")
    page.evaluate("window.__still_here = true")
    page.click("#btn-add-copy")
    page.wait_for_selector("#copy-form")
    wait_held(page, owned)   # the form is up, the owned-copies answer is not
    page.click("#btn-save-copy")
    page.wait_for_selector("#copy-form", state="detached")
    assert page.evaluate("window.__still_here === true"), "the page reloaded"
    assert copy_count(tid) == 2
    owned[0].continue_()   # a late answer must not break a closed form
    page.wait_for_selector(".copy-row >> nth=1")
    assert page.evaluate("window.__still_here === true")
    assert errors == []


def test_deep_link_paints_before_the_collection_arrives(server, browser, titles):
    tid = titles["dark_knight"]
    page, errors = new_page(browser)
    collection = []
    hold(page, re.compile(r"/admin/api/titles\?sort=title"), collection)
    page.goto(f"{server}/admin#/t/{tid}")
    page.wait_for_selector(".detail-view .hero-title")   # painted while the collection request is still out
    wait_held(page, collection)
    assert page.locator(".hero-title").inner_text() == "The Dark Knight"
    assert page.title() == "The Dark Knight (2008) · Matinee"
    assert page.locator("#btn-prev-movie").get_attribute("aria-disabled") == "true"
    assert page.locator("#btn-next-movie").get_attribute("aria-disabled") == "true"
    collection[0].continue_()
    page.wait_for_function("document.getElementById('btn-next-movie').getAttribute('aria-disabled') !== 'true'")
    assert page.get_attribute("#btn-next-movie", "href") == f"#/t/{titles['inception']}"
    assert "Inception" in page.get_attribute("#btn-next-movie", "title")
    assert page.get_attribute("#btn-prev-movie", "aria-disabled") == "true"   # sorted without "The": Dark Knight is first
    # Back to the collection puts the plain name back.
    page.click("#btn-back")
    page.wait_for_selector(".movie-card")
    assert page.title() == "Matinee"
    assert errors == []
