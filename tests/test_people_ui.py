"""Playwright smoke test for "who watched" on the title view at phone widths.

Same harness and skip guard as tests/test_ask_ui.py: skipped when playwright or Chromium is missing, and the CI
"ui-smoke" job fails if any test here is skipped. /admin/api/me, /admin/api/people and /admin/api/titles/1 are
stubbed with page.route; the POST and PUT bodies the page sends are recorded and asserted exactly.
"""
import glob
import json
import os
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

ME = {"editor": True, "email": "lan", "tmdb": False, "ask": True}
PEOPLE = [
    {"id": 1, "name": "Nick", "kind": "adult", "color": "green", "sort_order": 0, "active": True, "watch_count": 41},
    {"id": 2, "name": "Lauren", "kind": "adult", "color": "blue", "sort_order": 1, "active": True, "watch_count": 38},
    {"id": 3, "name": "Midge", "kind": "kid", "color": "red", "sort_order": 2, "active": True, "watch_count": 29},
    {"id": 4, "name": "Alice", "kind": "kid", "color": "pink", "sort_order": 3, "active": True, "watch_count": 27},
    {"id": 5, "name": "Paige", "kind": "kid", "color": "purple", "sort_order": 4, "active": True, "watch_count": 27},
]
TITLE = {
    "id": 1, "title": "The Dark Knight", "year": 2008, "runtime": 152, "user_rating": 9.5, "seen_by": [1, 2],
    "genres": [], "copies": [], "watch_count": 2, "last_watched": "2026-11-11",
    "watches": [
        {"id": 88, "watched_date": "2026-11-11", "date_bucket": None, "notes": "girls at Grandma's",
         "attendance": "recorded", "attendees": [1, 2]},
        {"id": 12, "watched_date": "2015-06-01", "date_bucket": "over_10_years", "notes": None,
         "attendance": "assumed_adults", "attendees": [1, 2]},
    ],
}


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


def _json(route, body, status=200):
    route.fulfill(status=status, content_type="application/json", body=json.dumps(body))


def _settled(page, posts, n):
    """Wait for the n-th log POST to land and the title view to finish re-rendering after it."""
    for _ in range(100):
        if len(posts) >= n:
            break
        page.wait_for_timeout(20)
    assert len(posts) == n
    page.wait_for_function("document.querySelector('#who-watched-row .person-toggle') && "
                           "!document.getElementById('view').textContent.includes('Loading movie details')")
    page.wait_for_timeout(50)


def _open(browser, server, people, width=412, put_status=200):
    """Open the title view with stubbed APIs. Returns (page, posts, puts, errors)."""
    page = browser.new_page(viewport={"width": width, "height": 915})
    errors, posts, puts = [], [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/admin/api/me", lambda r: _json(r, ME))
    page.route("**/admin/api/people", lambda r: _json(r, {"people": people}))
    page.route("**/admin/api/titles?*", lambda r: _json(r, {"titles": []}))

    def title(route):
        if route.request.method == "GET":
            _json(route, TITLE)
        else:
            route.fallback()

    def watches(route):
        posts.append(route.request.post_data_json)
        _json(route, {"ok": True, "watch": {"id": 89, "attendance": "recorded", "attendees": posts[-1].get("attendees", [])}})

    def attendees(route):
        puts.append((route.request.url.split("/admin/api/")[1], route.request.post_data_json))
        if put_status != 200:
            _json(route, {"error": "A watch needs at least one person."}, put_status)
        else:
            ids = route.request.post_data_json["attendees"]
            _json(route, {"ok": True, "watch": {"id": 12, "attendance": "recorded", "attendees": ids}})

    page.route("**/admin/api/titles/1", title)
    page.route("**/admin/api/titles/1/watches", watches)
    page.route("**/admin/api/titles/1/watches/12/attendees", attendees)
    page.goto(f"{server}/admin#/t/1")
    page.wait_for_selector(".detail-view")
    return page, posts, puts, errors


def test_who_watched_log_and_row_editor_at_412_and_360(server, browser):
    page, posts, puts, errors = _open(browser, server, PEOPLE)

    toggles = page.locator("#who-watched-row .person-toggle")
    assert toggles.count() == 5
    assert [toggles.nth(i).get_attribute("aria-pressed") for i in range(5)] == ["true"] * 5
    # the row sits directly under "+ Watched Today"
    assert page.locator("#btn-watched-today").bounding_box()["y"] < page.locator("#who-watched-row").bounding_box()["y"]
    assert all(toggles.nth(i).bounding_box()["height"] >= 44 for i in range(5))

    # turn Midge, Alice and Paige off: attendees [1, 2]
    for pid in (3, 4, 5):
        page.click(f'#who-watched-row .person-toggle[data-person-id="{pid}"]')
    page.click("#btn-watched-today")
    _settled(page, posts, 1)
    assert set(posts[0]) == {"watched_date", "attendees"} and posts[0]["attendees"] == [1, 2]

    # everyone off disables every log button (and a log is never sent)
    for pid in (1, 2, 3, 4, 5):
        page.click(f'#who-watched-row .person-toggle[data-person-id="{pid}"]')
    assert page.is_disabled("#btn-watched-today") and page.is_disabled("#btn-log-watch")
    assert all(page.locator(".btn-watch-bucket").nth(i).is_disabled() for i in range(6))
    page.click('#who-watched-row .person-toggle[data-person-id="5"]')
    page.click('.btn-watch-bucket[data-bucket="past_year"]')
    _settled(page, posts, 2)
    assert posts[-1] == {"date_bucket": "past_year", "attendees": [5]}
    page.fill("#input-watch-date", "2026-11-01")
    page.click("#btn-log-watch")
    _settled(page, posts, 3)
    assert posts[-1] == {"watched_date": "2026-11-01", "attendees": [1, 2, 3, 4, 5]}

    # saved watches: recorded shows dots; the old one shows ? and "assumed adults"
    recorded, old = page.locator('.watch-row[data-watch-id="88"]'), page.locator('.watch-row[data-watch-id="12"]')
    assert recorded.locator(".person-dot").count() == 2 and "assumed" not in recorded.inner_text()
    assert old.locator(".person-unknown").inner_text() == "?"
    assert "assumed adults" in old.inner_text()

    # row editor: toggles preselected N, L; turn Midge on and Save
    old.locator(".watch-who-btn").click()
    ed = old.locator(".watch-who-editor .person-toggle")
    assert [ed.nth(i).get_attribute("aria-pressed") for i in range(5)] == ["true", "true", "false", "false", "false"]
    old.locator('.watch-who-editor .person-toggle[data-person-id="3"]').click()
    old.locator(".watch-who-save").click()
    page.wait_for_selector('.watch-row[data-watch-id="12"]:not(.is-editing)')
    assert puts == [("titles/1/watches/12/attendees", {"attendees": [1, 2, 3]})]
    assert old.locator(".person-dot").count() == 3 and "assumed" not in old.inner_text()

    widths = {}
    for w in (412, 360):
        page.set_viewport_size({"width": w, "height": 915})
        page.wait_for_timeout(50)
        widths[w] = page.evaluate("document.documentElement.scrollWidth")
        assert widths[w] <= w, f"horizontal scroll at {w}px: scrollWidth={widths[w]}"
    # and with the editor open at 360
    page.locator('.watch-row[data-watch-id="88"] .watch-who-btn').click()
    page.wait_for_timeout(50)
    widths["editor@360"] = page.evaluate("document.documentElement.scrollWidth")
    assert widths["editor@360"] <= 360
    print(f"scrollWidth: {widths}")
    assert errors == []


def test_row_editor_cancel_error_and_inactive_person(server, browser):
    people = [dict(p, active=p["id"] != 5) for p in PEOPLE]          # Paige is inactive
    page, posts, puts, errors = _open(browser, server, people, width=360, put_status=400)
    assert page.locator("#who-watched-row .person-toggle").count() == 4    # log row: active people only

    row = page.locator('.watch-row[data-watch-id="12"]')
    row.locator(".watch-who-btn").click()
    row.locator(".watch-who-cancel").click()
    assert row.locator(".watch-who-editor").count() == 0 and puts == []

    # an inactive attendee still shows as a dot and stays selected in the editor
    page.route("**/admin/api/titles/1", lambda r: _json(r, {
        **TITLE, "watches": [dict(TITLE["watches"][1], attendance="recorded", attendees=[1, 5])]}))
    page.reload()
    page.wait_for_selector(".detail-view")
    row = page.locator('.watch-row[data-watch-id="12"]')
    assert row.locator(".person-dot").count() == 2
    row.locator(".watch-who-btn").click()
    ed = row.locator(".watch-who-editor .person-toggle")
    assert ed.count() == 5 and ed.nth(4).get_attribute("aria-pressed") == "true"
    row.locator(".watch-who-save").click()
    page.wait_for_selector(".watch-who-error:not(:empty)")
    assert puts == [("titles/1/watches/12/attendees", {"attendees": [1, 5]})]
    assert "at least one person" in row.locator(".watch-who-error").inner_text()
    assert page.evaluate("document.documentElement.scrollWidth") <= 360
    assert errors == []


def test_no_people_means_todays_title_view(server, browser):
    page, posts, puts, errors = _open(browser, server, [])
    assert page.locator("#who-watched-row").count() == 0 and page.locator(".watch-who-btn").count() == 0
    page.click("#btn-watched-today")
    page.wait_for_timeout(100)
    assert posts == [{"watched_date": posts[0]["watched_date"]}]
    assert errors == []
