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


def _mark_stale(page):
    """Tag the current title view so a later wait can tell a fresh render from the pre-action DOM."""
    page.evaluate("document.querySelector('.detail-view').dataset.stale = '1'")


def _settled(page, posts, n):
    """Wait for the n-th log POST, then for a title view that replaced the one marked by _mark_stale."""
    for _ in range(250):
        if len(posts) >= n:
            break
        page.wait_for_timeout(20)
    assert len(posts) == n
    page.wait_for_function("document.querySelector('.detail-view') && !document.querySelector('.detail-view[data-stale]')"
                           " && document.querySelector('#who-watched-row .person-toggle')")


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
    _mark_stale(page)
    page.click("#btn-watched-today")
    _settled(page, posts, 1)
    assert set(posts[0]) == {"watched_date", "attendees"} and posts[0]["attendees"] == [1, 2]

    # everyone off disables every log button (and a log is never sent)
    for pid in (1, 2, 3, 4, 5):
        page.click(f'#who-watched-row .person-toggle[data-person-id="{pid}"]')
    assert page.is_disabled("#btn-watched-today") and page.is_disabled("#btn-log-watch")
    assert all(page.locator(".btn-watch-bucket").nth(i).is_disabled() for i in range(6))
    page.click('#who-watched-row .person-toggle[data-person-id="5"]')
    _mark_stale(page)
    page.click('.btn-watch-bucket[data-bucket="past_year"]')
    _settled(page, posts, 2)
    assert posts[-1] == {"date_bucket": "past_year", "attendees": [5]}
    page.fill("#input-watch-date", "2026-11-01")
    _mark_stale(page)
    page.click("#btn-log-watch")
    _settled(page, posts, 3)
    assert posts[-1] == {"watched_date": "2026-11-01", "attendees": [1, 2, 3, 4, 5]}

    # saved watches: recorded shows dots; the old one shows ? and "assumed adults"
    recorded, old = page.locator('.watch-row[data-watch-id="88"]'), page.locator('.watch-row[data-watch-id="12"]')
    assert recorded.locator(".person-dot").count() == 2 and "assumed" not in recorded.inner_text()
    # the dots button's accessible name comes from its content, not an overriding aria-label (WCAG 2.5.3)
    assert recorded.locator(".watch-who-btn").get_attribute("aria-label") is None
    assert "Nick, watched" in recorded.locator(".watch-who-btn").inner_html()
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
    for _ in range(250):
        if posts:
            break
        page.wait_for_timeout(20)
    assert posts == [{"watched_date": posts[0]["watched_date"]}]
    assert errors == []


def test_expanded_row_keeps_its_place_after_cancel_and_save(server, browser):
    page, posts, puts, errors = _open(browser, server, PEOPLE)
    # reopen with four watches so the 4th sits behind "Show all"
    extra = {"id": 12, "watched_date": "2010-01-01", "date_bucket": None, "notes": None,
             "attendance": "recorded", "attendees": [1, 2]}
    four = [dict(TITLE["watches"][0], id=90 + i) for i in range(3)] + [extra]
    page.route("**/admin/api/titles/1", lambda r: _json(r, {**TITLE, "watches": four}))
    page.reload()
    page.wait_for_selector(".detail-view")
    row = page.locator('.watch-row[data-watch-id="12"]')
    assert not row.is_visible()
    page.click("#btn-toggle-watches")
    assert row.is_visible()

    row.locator(".watch-who-btn").click()
    row.locator(".watch-who-cancel").click()
    assert row.locator(".watch-who-editor").count() == 0 and row.is_visible()

    row.locator(".watch-who-btn").click()
    row.locator('.watch-who-editor .person-toggle[data-person-id="3"]').click()
    row.locator(".watch-who-save").click()
    page.wait_for_selector('.watch-row[data-watch-id="12"]:not(.is-editing)')
    assert puts == [("titles/1/watches/12/attendees", {"attendees": [1, 2, 3]})]
    assert row.is_visible() and row.locator(".person-dot").count() == 3
    assert errors == []


# --- Task 9: Movie Night audience and grid filters ---------------------------------------------------------

AUDIENCE_KEY = "matinee.movieNight.audience"
PICK = {"id": 7, "title": "Paddington 2", "year": 2017, "runtime": 104, "formats": ["Blu-ray"], "genres": [],
        "certification": "PG", "reason": "Nobody in the audience has seen it", "seen_by": ["Nick", "Lauren"]}


def _wait(page, cond, what="condition"):
    """Poll a python-side condition without a fixed sleep (the same loop as _settled)."""
    for _ in range(250):
        if cond():
            return
        page.wait_for_timeout(20)
    raise AssertionError(f"timed out waiting for {what}")


def _open_grid(browser, server, people, stored=None, rec=None, width=412, anonymous_me=None):
    """Open the grid with stubbed APIs. `rec(n, url)` returns (status, body) for the n-th recommendations call."""
    page = browser.new_page(viewport={"width": width, "height": 915})
    errors, recs, titles = [], [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    if stored is not None:
        page.add_init_script(f"try {{ localStorage.setItem({AUDIENCE_KEY!r}, {stored!r}); }} catch (e) {{}}")
    page.route("**/admin/api/me", lambda r: _json(r, ME))
    page.route("**/admin/api/people", lambda r: _json(r, {"people": people}))
    page.route("**/admin/api/facets", lambda r: _json(r, {}))

    def on_titles(route):
        titles.append(route.request.url)
        _json(route, {"titles": []})

    def on_rec(route):
        recs.append(route.request.url)
        status, body = rec(len(recs), route.request.url) if rec else (200, {"results": [PICK]})
        _json(route, body, status)

    page.route("**/admin/api/titles?*", on_titles)
    page.route("**/admin/api/recommendations?*", on_rec)
    page.goto(f"{server}/admin#/")
    page.wait_for_selector("#btn-header-movie-night")
    return page, recs, titles, errors


def _open_picker(page, recs, n=1):
    page.click("#btn-header-movie-night")
    _wait(page, lambda: len(recs) >= n, f"{n} recommendations call(s)")


def test_movie_night_audience_kids_custom_and_no_overflow(server, browser):
    page, recs, _, errors = _open_grid(browser, server, PEOPLE)
    _open_picker(page, recs)
    assert "audience=" not in recs[0]                           # Everyone is the default and sends nothing
    row = page.locator('[aria-label="Who\'s watching"]')
    assert [row.locator('[role="radio"]').nth(i).inner_text() for i in range(4)] == ["Everyone", "Adults", "Kids", "Custom"]
    assert page.locator('#modal-root [aria-label="Rating cap"]').bounding_box()["y"] > row.bounding_box()["y"]
    assert all(row.locator('[role="radio"]').nth(i).bounding_box()["height"] >= 44 for i in range(4))

    row.locator('[data-audience="kids"]').click()
    _wait(page, lambda: len(recs) == 2, "kids request")
    assert recs[1].startswith("http") and "/admin/api/recommendations?" in recs[1] and "audience=kids" in recs[1]
    page.wait_for_selector(".movie-night-seen-by .person-dot")             # Nick and Lauren have seen it
    assert page.locator(".movie-night-seen-by .person-dot").count() == 2
    assert page.evaluate(f"localStorage.getItem({AUDIENCE_KEY!r})") == "kids"

    row.locator('[data-audience="custom"]').click()
    _wait(page, lambda: len(recs) == 3, "custom request")
    toggles = page.locator("#movie-night-audience-custom .person-toggle")
    assert toggles.count() == 5 and all(toggles.nth(i).bounding_box()["height"] >= 44 for i in range(5))
    assert "audience=1%2C2%2C3%2C4%2C5" in recs[2] or "audience=1,2,3,4,5" in recs[2]
    toggles.nth(0).click()                                                    # Nick off -> 2,3,4,5
    _wait(page, lambda: len(recs) == 4, "custom edit request")
    assert "audience=2%2C3%2C4%2C5" in recs[3] or "audience=2,3,4,5" in recs[3]
    assert page.evaluate(f"localStorage.getItem({AUDIENCE_KEY!r})") == "2,3,4,5"

    # every dot off: the pick button is disabled and nothing blank goes out
    for i in (1, 2, 3, 4):
        toggles.nth(i).click()
    page.wait_for_function("document.getElementById('btn-movie-night-roll').disabled")
    assert page.is_disabled("#btn-movie-night-view")
    page.wait_for_timeout(100)
    assert len(recs) == 4 or all("audience=&" not in u and not u.endswith("audience=") for u in recs)
    assert not any(u.endswith("audience=") or "audience=&" in u for u in recs)

    widths = {}
    for w in (412, 360):
        page.set_viewport_size({"width": w, "height": 915})
        page.wait_for_timeout(50)
        widths[w] = page.evaluate("document.documentElement.scrollWidth")
        widths[f"dialog@{w}"] = page.evaluate("document.querySelector('.movie-night-dialog').scrollWidth")
        assert widths[w] <= w and widths[f"dialog@{w}"] <= w, widths
    print(f"movie night scrollWidth: {widths}")
    assert errors == []


def test_movie_night_stale_audience_falls_back(server, browser):
    """A stored audience the server rejects (here: the first call answers 400 Unknown person) falls back once."""
    def rec(n, url):
        if n == 1:
            return 400, {"error": "Unknown person: 99. People: Nick, Lauren, Midge, Alice, Paige."}
        return 200, {"results": [PICK]}

    # "3" is an active person locally, but the server (stub) no longer knows the audience it names
    page, recs, _, errors = _open_grid(browser, server, PEOPLE, stored="3", rec=rec)
    _open_picker(page, recs, 2)
    assert "audience=3" in recs[0] and "audience=everyone" in recs[1]
    page.wait_for_selector(".movie-night-title")
    assert page.locator('[data-audience="everyone"]').get_attribute("aria-checked") == "true"
    text = page.locator("#movie-night-content").inner_text()
    assert "Unknown person" not in text and "Failed to load" not in text
    assert page.evaluate(f"localStorage.getItem({AUDIENCE_KEY!r})") == "everyone"
    assert errors == []


def test_movie_night_stale_audience_with_inactive_id_is_filtered(server, browser):
    people = [dict(p, active=p["id"] != 5) for p in PEOPLE]        # Paige is deactivated
    page, recs, _, errors = _open_grid(browser, server, people, stored="4,5")
    _open_picker(page, recs)
    assert "audience=4" in recs[0] and "5" not in recs[0].split("audience=")[1]    # only Alice goes out
    toggles = page.locator("#movie-night-audience-custom .person-toggle")
    assert toggles.count() == 4                                    # no resurrected Paige
    # nothing left after filtering: Everyone
    page.close()
    page, recs, _, errors = _open_grid(browser, server, people, stored="5")
    _open_picker(page, recs)
    assert "audience=" not in recs[0]
    assert page.locator('[data-audience="everyone"]').get_attribute("aria-checked") == "true"
    assert errors == []


def test_movie_night_second_400_is_shown(server, browser):
    def rec(n, url):
        return 400, {"error": "No people are set up yet. Add them first."}

    page, recs, _, errors = _open_grid(browser, server, PEOPLE, stored="3", rec=rec)
    _open_picker(page, recs, 2)
    page.wait_for_function("document.getElementById('movie-night-content').innerText.includes('No people are set up yet')")
    assert len(recs) == 2 and "audience=3" in recs[0] and "audience=everyone" in recs[1]
    # a further 400 after the one allowed fallback: still a visible error, no third request
    page.click('[data-audience="kids"]')
    page.wait_for_function("document.getElementById('movie-night-content').innerText.includes('No people are set up yet')")
    assert len(recs) == 3 and "audience=kids" in recs[2]
    assert errors == []


def test_movie_night_unchanged_without_people_or_for_anonymous(server, browser):
    page, recs, _, errors = _open_grid(browser, server, [])
    _open_picker(page, recs)
    assert page.locator('[aria-label="Who\'s watching"]').count() == 0 and "audience" not in recs[0]
    assert page.locator(".movie-night-seen-by").count() == 0
    page.close()

    anon = browser.new_page(viewport={"width": 412, "height": 915})
    urls = []
    anon.route("**/api/me", lambda r: _json(r, {"editor": False}))
    anon.route("**/admin/api/**", lambda r: _json(r, {"error": "no"}, 401))
    anon.route("**/api/titles?*", lambda r: _json(r, {"titles": []}))
    anon.route("**/api/facets", lambda r: _json(r, {}))

    def on_rec(route):
        urls.append(route.request.url)
        _json(route, {"results": [PICK]})

    anon.route("**/api/recommendations?*", on_rec)
    anon.goto(f"{server}/")
    anon.wait_for_selector("#btn-header-movie-night")
    anon.click("#btn-header-movie-night")
    _wait(anon, lambda: urls, "anonymous request")
    assert "audience" not in urls[0] and "/admin/" not in urls[0]
    assert anon.locator('[aria-label="Who\'s watching"]').count() == 0
    assert errors == []


def test_grid_unseen_by_and_unknown_attendance_filters(server, browser):
    page, _, titles, errors = _open_grid(browser, server, PEOPLE)
    _wait(page, lambda: titles, "first grid load")
    assert not any("unseen_by" in u for u in titles)
    page.click("#popover-watched .filter-btn")
    group = page.locator("#popover-watched")
    labels = [t.strip() for t in group.locator('input[data-facet="unseen_by"] + span').all_inner_texts()]
    assert labels == ["Everyone", "Adults", "Kids", "Nick", "Lauren", "Midge", "Alice", "Paige"]
    assert group.locator('input[data-facet="attendance"]').count() == 1
    assert all(group.locator(".popover-item").nth(i).bounding_box()["height"] >= 44
               for i in range(group.locator(".popover-item").count()))

    n = len(titles)
    page.click('#popover-watched label:has(input[data-val="kids"]) span')
    _wait(page, lambda: len(titles) > n, "unseen_by request")
    assert "/admin/api/titles?" in titles[-1] and "unseen_by=kids" in titles[-1]
    assert page.evaluate("location.hash") == "#/?unseen_by=kids"
    assert "Unseen by Kids" in page.locator("#popover-watched .filter-btn").inner_text()
    assert "active" in page.locator("#popover-watched .filter-btn").get_attribute("class")

    n = len(titles)
    page.wait_for_selector("#popover-watched.open")                       # the popover stays open across the re-render
    page.click('#popover-watched label:has(input[data-facet="attendance"]) span')
    _wait(page, lambda: len(titles) > n, "attendance request")
    assert "unseen_by=kids" in titles[-1] and "attendance=unknown" in titles[-1]
    assert "Who watched: unknown" in page.locator("#popover-watched .filter-btn").inner_text()

    # reload straight from the hash: the state round-trips
    page.reload()
    page.wait_for_selector("#filter-bar .filter-btn")
    assert "Unseen by Kids" in page.locator("#popover-watched .filter-btn").inner_text()

    n = len(titles)
    page.click("#filter-reset-btn")
    _wait(page, lambda: len(titles) > n, "reset request")
    assert "unseen_by" not in titles[-1] and "attendance" not in titles[-1]
    assert page.evaluate("document.documentElement.scrollWidth") <= 412
    assert errors == []


def test_grid_watched_popover_unchanged_without_people(server, browser):
    page, _, titles, errors = _open_grid(browser, server, [])
    page.click("#popover-watched .filter-btn")
    assert page.locator('#popover-watched input[data-facet="unseen_by"]').count() == 0
    assert page.locator('#popover-watched input[data-facet="attendance"]').count() == 0
    assert page.locator("#popover-watched .popover-item").count() == 3
    assert errors == []
