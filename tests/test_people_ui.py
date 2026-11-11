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
    # a bucket button only chooses a bucket (it resets the toggles), so it stays enabled; its Log button would not
    assert not any(page.locator(".btn-watch-bucket").nth(i).is_disabled() for i in range(6))
    # with people, a bucket is chosen first (its default fills the toggles), then logged with the Log button
    page.click('.btn-watch-bucket[data-bucket="past_year"]')
    assert page.get_attribute('.btn-watch-bucket[data-bucket="past_year"]', "aria-pressed") == "true"
    assert [toggles.nth(i).get_attribute("aria-pressed") for i in range(5)] == ["true"] * 5
    for pid in (1, 2, 3, 4):
        page.click(f'#who-watched-row .person-toggle[data-person-id="{pid}"]')
    assert page.inner_text("#btn-log-bucket").strip() == "Log Past Year"
    assert page.locator("#btn-log-bucket").bounding_box()["height"] >= 44
    _mark_stale(page)
    page.click("#btn-log-bucket")
    _settled(page, posts, 2)
    assert posts[-1] == {"date_bucket": "past_year", "attendees": [5]}
    # everyone on is the default for a dated log, so no attendees key is sent (the server records everyone)
    page.fill("#input-watch-date", "2026-11-01")
    _mark_stale(page)
    page.click("#btn-log-watch")
    _settled(page, posts, 3)
    assert posts[-1] == {"watched_date": "2026-11-01"}

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


def _open_grid(browser, server, people, stored=None, rec=None, width=412, anonymous_me=None, hash="#/", titles_reply=None):
    """Open the grid with stubbed APIs. `rec(n, url)` returns (status, body) for the n-th recommendations call;
    `titles_reply(url)` does the same for /admin/api/titles (default: 200, no titles)."""
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
        status, body = titles_reply(route.request.url) if titles_reply else (200, {"titles": []})
        _json(route, body, status)

    def on_rec(route):
        recs.append(route.request.url)
        status, body = rec(len(recs), route.request.url) if rec else (200, {"results": [PICK]})
        _json(route, body, status)

    page.route("**/admin/api/titles?*", on_titles)
    page.route("**/admin/api/recommendations?*", on_rec)
    page.goto(f"{server}/admin{hash}")
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
    assert "audience=3" in recs[0] and "audience" not in recs[1]       # the retry is today's Everyone request
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
    assert len(recs) == 2 and "audience=3" in recs[0] and "audience" not in recs[1]
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
    who = group.locator(".popover-item-who")
    assert who.count() == 9 and all(who.nth(i).bounding_box()["height"] >= 44 for i in range(who.count()))

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


# --- final review fixes: people filters in the hash, pick-card dots, popover rows, People modal --------------

UNKNOWN_7 = "Unknown person: 7. People: Nick, Lauren, Midge, Alice, Paige."


def _row_min_heights(page, popover):
    return page.evaluate(f"Array.from(document.querySelectorAll('#{popover} .popover-item'))"
                         ".map((e) => getComputedStyle(e).minHeight)")


def test_anonymous_people_filter_in_hash_is_dropped(server, browser):
    """An editor's "Unseen by Kids" link opened in the public view: the grid loads without the filter."""
    page = browser.new_page(viewport={"width": 412, "height": 915})
    errors, urls = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/admin/api/**", lambda r: _json(r, {"error": "no"}, 401))
    page.route("**/api/facets", lambda r: _json(r, {}))

    def on_titles(route):
        urls.append(route.request.url)
        if "unseen_by" in route.request.url or "attendance" in route.request.url:
            _json(route, {"error": "Sign in to pick for specific people."}, 400)
        else:
            _json(route, {"titles": []})

    page.route("**/api/titles?*", on_titles)
    page.goto(f"{server}/#/?unseen_by=kids&attendance=unknown&sort=year")
    page.wait_for_selector("#filter-bar .filter-btn")
    assert len(urls) == 1 and "/admin/" not in urls[0]
    assert "unseen_by" not in urls[0] and "attendance" not in urls[0] and "sort=year" in urls[0]
    assert page.evaluate("location.hash") == "#/?sort=year"
    assert "Unable to load collection" not in page.locator("#view").inner_text()
    # the public Watched popover keeps its pre-people rows: the same min-height as the Rating popover's (M5)
    watched, rating = _row_min_heights(page, "popover-watched"), _row_min_heights(page, "popover-rating")
    assert len(watched) == 3 and set(watched) == set(rating) and "44px" not in watched
    assert errors == []


def test_editor_public_view_link_drops_people_filters(server, browser):
    page, _, titles, errors = _open_grid(browser, server, PEOPLE, hash="#/?unseen_by=kids&attendance=unknown&sort=year")
    _wait(page, lambda: titles, "first grid load")
    page.wait_for_selector("#footer-links a")
    assert page.locator("#footer-links a", has_text="Public View").get_attribute("href") == "/#/?sort=year"
    assert "unseen_by=kids" in titles[0]                         # the editor's own grid keeps the filter
    assert errors == []


def test_editor_stale_unseen_by_retries_once_without_it(server, browser):
    """unseen_by=7 names a person who was deleted: one retry without it, a toast with the server's message."""
    def reply(url):
        if "unseen_by" in url or "attendance" in url:
            return 400, {"error": UNKNOWN_7}
        return 200, {"titles": []}

    page, _, titles, errors = _open_grid(browser, server, PEOPLE, hash="#/?unseen_by=7&attendance=unknown&sort=year",
                                         titles_reply=reply)
    page.wait_for_selector("#filter-bar .filter-btn")
    assert len(titles) == 2
    assert "unseen_by=7" in titles[0] and "attendance=unknown" in titles[0]
    assert "unseen_by" not in titles[1] and "attendance" not in titles[1] and "sort=year" in titles[1]
    assert page.evaluate("location.hash") == "#/?sort=year"
    assert "Unable to load collection" not in page.locator("#view").inner_text()
    assert UNKNOWN_7 in page.locator("#toast").inner_text()
    assert errors == []


def test_editor_people_filter_400_does_not_loop(server, browser):
    """If the retry fails too, the error shows; there is no third request."""
    page, _, titles, errors = _open_grid(browser, server, PEOPLE, hash="#/?unseen_by=7",
                                         titles_reply=lambda url: (400, {"error": UNKNOWN_7}))
    page.wait_for_selector("text=Unable to load collection")
    assert len(titles) == 2 and "unseen_by=7" in titles[0] and "unseen_by" not in titles[1]
    assert errors == []


def _audience_reply(seen_by):
    kids = [{"id": 3, "name": "Midge"}, {"id": 4, "name": "Alice"}, {"id": 5, "name": "Paige"}]

    def rec(n, url):
        if "audience=kids" in url:
            return 200, {"results": [dict(PICK, seen_by=seen_by)], "audience": {"label": "kids", "people": kids}}
        return 200, {"results": [dict(PICK, seen_by=[])]}
    return rec


def test_pick_card_hides_seen_by_when_only_the_audience_has_seen_it(server, browser):
    page, recs, _, errors = _open_grid(browser, server, PEOPLE, stored="kids", rec=_audience_reply(["Midge"]))
    _open_picker(page, recs)
    assert "audience=kids" in recs[0]
    page.wait_for_selector(".movie-night-title")
    assert page.locator(".movie-night-seen-by").count() == 0
    assert errors == []


def test_pick_card_shows_only_people_outside_the_audience(server, browser):
    page, recs, _, errors = _open_grid(browser, server, PEOPLE, stored="kids", rec=_audience_reply(["Nick", "Midge"]))
    _open_picker(page, recs)
    page.wait_for_selector(".movie-night-seen-by .person-dot")
    dots = page.locator(".movie-night-seen-by .person-dot")
    assert dots.count() == 1 and dots.get_attribute("aria-label") == "Nick, watched"
    # outside a button the label needs a role to be read (M4)
    assert dots.get_attribute("role") == "img"
    assert page.get_by_role("img", name="Nick, watched").count() == 1
    assert errors == []


def _open_title_with_people_api(browser, server, people):
    """Title view whose /admin/api/people GET returns `people` (a list the test mutates) and whose POST adds one.
    Returns (page, title_gets, errors)."""
    page = browser.new_page(viewport={"width": 412, "height": 915})
    errors, title_gets = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/admin/api/me", lambda r: _json(r, ME))

    def on_people(route):
        if route.request.method == "POST":
            body = route.request.post_data_json
            person = {"id": len(people) + 1, "sort_order": len(people), "active": True, "watch_count": 0, **body}
            people.append(person)
            _json(route, {"ok": True, "person": person})
        else:
            _json(route, {"people": people})

    def on_title(route):
        title_gets.append(route.request.url)
        _json(route, {**TITLE, "seen_by": [], "watches": []})

    page.route("**/admin/api/people", on_people)
    page.route("**/admin/api/titles/1", on_title)
    page.goto(f"{server}/admin#/t/1")
    page.wait_for_selector(".detail-view")
    return page, title_gets, errors


def test_people_modal_close_refreshes_the_view_only_after_a_change(server, browser):
    people = []
    page, title_gets, errors = _open_title_with_people_api(browser, server, people)
    assert page.locator("#who-watched-row").count() == 0

    # open and close with no change: the title is not fetched again
    page.click("#btn-header-people")
    page.wait_for_selector("#add-person-form")
    close = page.locator("#modal-root .modal-actions .btn")
    assert close.inner_text().strip() == "Close" and close.bounding_box()["height"] >= 44     # M10
    n = len(title_gets)
    close.click()
    assert page.locator("#add-person-form").count() == 0 and len(title_gets) == n

    # add Midge and close: the Who watched row appears without navigation
    page.click("#btn-header-people")
    page.wait_for_selector("#add-person-form")
    page.fill("#person-add-name", "Midge")
    page.select_option("#person-add-kind", "kid")
    page.click("#add-person-form button[type=submit]")
    page.wait_for_selector('.people-row .people-name[aria-label="Name for Midge"]')
    _mark_stale(page)
    page.locator("#modal-root .modal-actions .btn").click()
    page.wait_for_function("document.querySelector('.detail-view') && !document.querySelector('.detail-view[data-stale]')"
                           " && document.querySelector('#who-watched-row .person-toggle')")
    assert page.locator("#who-watched-row .person-toggle").count() == 1
    assert len(title_gets) == n + 1 and page.evaluate("location.hash") == "#/t/1"
    assert errors == []


def test_people_modal_escape_after_a_change_refreshes_the_grid(server, browser):
    page, _, titles, errors = _open_grid(browser, server, PEOPLE)
    _wait(page, lambda: titles, "first grid load")
    page.wait_for_selector("#filter-bar .filter-btn")
    page.route("**/admin/api/people/*", lambda r: _json(r, {"ok": True, "person": PEOPLE[4]}))
    page.click("#btn-header-people")
    page.wait_for_selector("#add-person-form")
    n = len(titles)
    page.evaluate("document.querySelector('#modal-root .people-dialog').dataset.stale = '1'")
    page.locator('.people-row[data-id="5"] [data-act="kind"][data-kind="adult"]').click()
    page.wait_for_function("document.querySelector('#modal-root .people-dialog')"
                           " && !document.querySelector('#modal-root .people-dialog[data-stale]')")   # saved, re-rendered
    assert len(titles) == n                                      # nothing refreshes while the modal is open
    page.keyboard.press("Escape")
    _wait(page, lambda: len(titles) > n, "grid refresh after Escape")
    assert page.locator("#add-person-form").count() == 0
    assert errors == []


def test_ten_plus_years_switches_the_kids_off_and_sends_only_a_change(server, browser):
    """Ruling R21. Logging The Princess Bride as "10+ Years" with nothing changed sends no attendees (the server
    records Nick and Lauren); switching Midge back on sends the two adults and Midge. Today still means everyone."""
    page, posts, puts, errors = _open(browser, server, PEOPLE)
    toggles = page.locator("#who-watched-row .person-toggle")
    pressed = lambda: [toggles.nth(i).get_attribute("aria-pressed") for i in range(5)]  # noqa: E731

    page.click('.btn-watch-bucket[data-bucket="over_10_years"]')
    assert pressed() == ["true", "true", "false", "false", "false"]
    assert page.inner_text("#btn-log-bucket").strip() == "Log 10+ Years"
    _mark_stale(page)
    page.click("#btn-log-bucket")
    _settled(page, posts, 1)
    assert posts[-1] == {"date_bucket": "over_10_years"}

    page.click('.btn-watch-bucket[data-bucket="over_10_years"]')
    page.click('#who-watched-row .person-toggle[data-person-id="3"]')     # Midge back on
    _mark_stale(page)
    page.click("#btn-log-bucket")
    _settled(page, posts, 2)
    assert posts[-1] == {"date_bucket": "over_10_years", "attendees": [1, 2, 3]}

    # choosing 10+ then another bucket restores everyone; tapping the chosen bucket again unchooses it
    page.click('.btn-watch-bucket[data-bucket="over_10_years"]')
    page.click('.btn-watch-bucket[data-bucket="5_10_years"]')
    assert pressed() == ["true"] * 5
    page.click('.btn-watch-bucket[data-bucket="5_10_years"]')
    assert page.locator("#btn-log-bucket").is_hidden()

    _mark_stale(page)
    page.click("#btn-watched-today")
    _settled(page, posts, 3)
    assert set(posts[-1]) == {"watched_date"}
    assert errors == []


def test_buckets_still_log_on_one_tap_without_people(server, browser):
    page, posts, puts, errors = _open(browser, server, [])
    assert page.locator("#who-watched-row").count() == 0 and page.locator("#btn-log-bucket").count() == 0
    page.click('.btn-watch-bucket[data-bucket="over_10_years"]')
    for _ in range(250):
        if posts:
            break
        page.wait_for_timeout(20)
    assert posts == [{"date_bucket": "over_10_years"}]
    assert errors == []


@pytest.mark.parametrize("hash_,keeps", [("#/add", "#tmdb-search-input"), ("#/stats", ".stats-view"),
                                         ("#/about", ".about-view")])
def test_people_modal_close_leaves_views_without_people_alone(server, browser, hash_, keeps):
    """Review F3: closing the People modal after a change re-ran handleRoute on every route, which reset the Add
    form (a half-typed search, the scanner) and redrew Stats. Only the grid and the title view show people."""
    people = []
    page, title_gets, errors = _open_title_with_people_api(browser, server, people)
    page.evaluate(f"location.hash = '{hash_}'")
    page.wait_for_selector(keeps)
    if hash_ == "#/add":
        page.fill("#tmdb-search-input", "paddington")
    page.evaluate(f"document.querySelector('{keeps}').dataset.kept = '1'")
    page.click("#btn-header-people")
    page.wait_for_selector("#add-person-form")
    page.fill("#person-add-name", "Midge")
    page.select_option("#person-add-kind", "kid")
    page.click("#add-person-form button[type=submit]")
    page.wait_for_selector('.people-row .people-name[aria-label="Name for Midge"]')
    page.locator("#modal-root .modal-actions .btn").click()
    page.wait_for_timeout(300)
    assert page.locator(f"{keeps}[data-kept]").count() == 1, "the view was re-rendered"
    if hash_ == "#/add":
        assert page.input_value("#tmdb-search-input") == "paddington"
    assert errors == []
