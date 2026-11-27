"""Playwright smoke test for Play on the Family Room TV: the grid's play button, the details page and Movie Night, at
phone width (412 px, and 360) (spec 2026-11-26-play-on-tv-design §6.5).

Same harness and skip guard as tests/test_youtube_link_ui.py: skipped when playwright or Chromium is missing, and
the CI "ui-smoke" job fails if any test here is skipped. A real server and database. Home Assistant is a stub
server the test starts on a local port (it records every post and can be told to answer 500 or to be slow): the
page never reaches a real webhook, and the setting is only ever the stub's address. The YouTube ids are made up.

Scenario: Brooklyn on YouTube only; Heat on a 4K UHD disc on Family Room > Media Cabinet > Shelf 2 and on YouTube
(two ways); Elf with a digital copy and no YouTube id (no way to play). The household is Nick, Lauren, Midge, Alice
and Paige.
"""
import glob
import json
import os
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

from conftest import seed_people  # noqa: E402
from test_play_core import SHELF, YT_ID, seed_film, seed_shelf  # noqa: E402

HEAT_YT = "hT9vXq2LmPa"
SHELF_LINE = f"4K UHD · {SHELF}"
STARTING = "Starting Brooklyn on Family Room TV. Added to watch history."


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
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.HA_WEBHOOK_URL", "")
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


class Stub:
    """A local stand-in for the Home Assistant webhook. `posts` holds every decoded body; `status` and `delay` are
    what the next answers do."""
    def __init__(self):
        self.posts, self.status, self.delay = [], 200, 0.0
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length") or 0))
                stub.posts.append(json.loads(body))
                time.sleep(stub.delay)
                self.send_response(stub.status)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def log_message(self, *args):  # keep the test output quiet
                pass

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_port}/api/webhook/stub-play-hook"
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join(timeout=5)


@pytest.fixture
def stub(monkeypatch):
    s = Stub()
    monkeypatch.setattr("app.HA_PLAY_WEBHOOK_URL", s.url)
    yield s
    s.close()


def _seed():
    import app as core

    con = core.connect()
    people = seed_people(con)
    shelf = seed_shelf(con)
    brooklyn = seed_film(con, "Brooklyn", 2015, "PG-13",
                         copies=[("digital", "Digital HD", "Movies Anywhere", YT_ID, None)])
    heat = seed_film(con, "Heat", 1995, "R", copies=[("physical", "4K UHD", None, None, shelf),
                                                     ("digital", "Digital HD", "Apple TV", HEAT_YT, None)])
    elf = seed_film(con, "Elf", 2003, "PG", copies=[("digital", "Digital HD", "Apple TV", None, None)])
    con.close()
    return {"brooklyn": brooklyn["id"], "heat": heat["id"], "elf": elf["id"], "people": people}


def _watch_rows(title_id):
    import app as core

    con = core.connect()
    try:
        return [(r["id"], sorted(x[0] for x in con.execute("SELECT person_id FROM watch_people WHERE watch_id = ?",
                                                           (r["id"],)))) for r in
                con.execute("SELECT id FROM watches WHERE title_id = ?", (title_id,))]
    finally:
        con.close()


def _clear_watches(*title_ids):
    """Delete the plays' watches (all of them, or those of the given titles)."""
    import app as core

    con = core.connect()
    only = f" WHERE title_id IN ({','.join('?' * len(title_ids))})" if title_ids else ""
    con.execute("DELETE FROM watch_people WHERE watch_id IN (SELECT id FROM watches" + only + ")", title_ids)
    con.execute("DELETE FROM watches" + only, title_ids)
    con.commit()
    con.close()


def _until(page, pred, what):
    """Wait (without a fixed sleep) until pred() holds."""
    for _ in range(200):
        if pred():
            return
        page.wait_for_timeout(25)
    raise AssertionError(f"timed out waiting for {what}")


def _grid(browser, server, width=412, path="/admin"):
    page = browser.new_page(viewport={"width": width, "height": 915})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server}{path}")
    page.wait_for_selector(".movie-card")
    return page, errors


def _card(page, title_id):
    return page.locator(f'.movie-card[data-id="{title_id}"]')


def _play_btn(page, title_id):
    return _card(page, title_id).locator("[data-play-title]")


def _toast(page):
    return page.locator("#toast")


def _no_hscroll(page, width):
    page.wait_for_function("document.documentElement.scrollWidth > 0")
    sw = page.evaluate("document.documentElement.scrollWidth")
    assert sw <= width, f"horizontal scroll at {width}px: scrollWidth={sw}"


def _settle(page):
    """Nothing is in flight any more: every play control on the page is enabled again."""
    page.wait_for_function("!document.querySelector('[data-play-title]:disabled, [data-play-mode]:disabled')")


# ----------------------------------------------------------------------------------------------- the grid
def test_one_way_tap_plays(server, browser, stub):
    ids = _seed()
    page, errors = _grid(browser, server)
    expect(_play_btn(page, ids["brooklyn"])).to_have_attribute("aria-label", "Play Brooklyn on the Family Room TV")
    _play_btn(page, ids["brooklyn"]).click()
    expect(_toast(page)).to_have_text(STARTING)
    assert stub.posts == [{"event": "matinee_play", "mode": "stream", "source": "YouTube", "title": "Brooklyn",
                           "content_id": YT_ID}]
    assert page.url.endswith("/admin") or page.url.endswith("/admin#/") or "#/t/" not in page.url
    assert len(_watch_rows(ids["brooklyn"])) == 1
    assert errors == []


def test_two_ways_opens_sheet(server, browser, stub):
    ids = _seed()
    page, errors = _grid(browser, server)
    _play_btn(page, ids["heat"]).click()
    sheet = page.locator(".play-sheet")
    expect(sheet).to_be_visible()
    expect(sheet.get_by_role("button", name="Play on YouTube")).to_be_visible()
    expect(sheet.get_by_role("button", name="Start the disc")).to_be_visible()
    expect(sheet).to_contain_text(SHELF_LINE)
    assert stub.posts == []                                   # nothing is sent until a way is chosen
    sheet.get_by_role("button", name="Start the disc").click()
    note = page.locator(".play-note")
    expect(note).to_contain_text("Starting Heat on Family Room TV.")
    expect(note).to_contain_text(SHELF_LINE)
    expect(sheet).to_have_count(0)
    assert stub.posts == [{"event": "matinee_play", "mode": "disc", "source": "AV Receiver", "title": "Heat"}]
    page.wait_for_timeout(6000)                                # a toast would be gone by now
    expect(note).to_be_visible()
    note.get_by_role("button", name="Dismiss").click()
    expect(page.locator(".play-note")).to_have_count(0)
    assert errors == []


def test_sheet_plays_youtube_and_closes(server, browser, stub):
    ids = _seed()
    page, errors = _grid(browser, server)
    _play_btn(page, ids["heat"]).click()
    page.locator(".play-sheet").get_by_role("button", name="Play on YouTube").click()
    expect(_toast(page)).to_have_text("Starting Heat on Family Room TV. Added to watch history.")
    expect(page.locator(".play-sheet")).to_have_count(0)
    assert stub.posts == [{"event": "matinee_play", "mode": "stream", "source": "YouTube", "title": "Heat",
                           "content_id": HEAT_YT}]
    assert errors == []


def test_sheet_closes_on_cancel_and_escape(server, browser, stub):
    ids = _seed()
    page, _errors = _grid(browser, server)
    for how in ("cancel", "escape", "backdrop"):
        _play_btn(page, ids["heat"]).click()
        sheet = page.locator(".play-sheet")
        expect(sheet).to_be_visible()
        if how == "cancel":
            sheet.get_by_role("button", name="Cancel").click()
        elif how == "escape":
            page.keyboard.press("Escape")
        else:
            page.mouse.click(5, 5)
        expect(sheet).to_have_count(0)
    assert stub.posts == []


def test_no_way_no_button(server, browser, stub):
    ids = _seed()
    page, _errors = _grid(browser, server)
    expect(_play_btn(page, ids["brooklyn"])).to_have_count(1)    # the grid has drawn its buttons
    expect(_card(page, ids["elf"])).to_have_count(1)
    expect(_play_btn(page, ids["elf"])).to_have_count(0)


def test_poster_still_opens_details(server, browser, stub):
    ids = _seed()
    page, errors = _grid(browser, server)
    _card(page, ids["brooklyn"]).locator(".poster-wrap").click(position={"x": 12, "y": 50})
    page.wait_for_function("location.hash.startsWith('#/t/')")
    assert page.evaluate("location.hash") == f"#/t/{ids['brooklyn']}"
    assert stub.posts == []
    assert errors == []


def test_play_button_does_not_open_details(server, browser, stub):
    ids = _seed()
    page, _errors = _grid(browser, server)
    _play_btn(page, ids["brooklyn"]).click()
    expect(_toast(page)).to_have_text(STARTING)
    assert page.evaluate("location.hash") in ("", "#/")


def test_failure_message(server, browser, stub):
    ids = _seed()
    stub.status = 500
    page, errors = _grid(browser, server)
    _play_btn(page, ids["brooklyn"]).click()
    expect(_toast(page)).to_have_text("Couldn't reach the TV. Brooklyn wasn't started.")
    assert len(stub.posts) == 1
    assert _watch_rows(ids["brooklyn"]) == []                  # nothing is logged when the TV isn't reached
    _settle(page)                                              # and the button works again
    assert errors == []


def test_matinee_unreachable_message(server, browser, stub):
    ids = _seed()
    page, _errors = _grid(browser, server)
    page.route("**/admin/api/titles/*/play", lambda route: route.abort())
    _play_btn(page, ids["brooklyn"]).click()
    expect(_toast(page)).to_have_text("Couldn't send that. Check the connection and try again.")
    assert stub.posts == []


@pytest.mark.parametrize("how", ["same tick", "double click"])
def test_double_tap_sends_once(server, browser, stub, how):
    ids = _seed()
    stub.delay = 0.8                                           # the request is still out for the second tap
    page, errors = _grid(browser, server)
    btn = _play_btn(page, ids["brooklyn"])
    if how == "same tick":
        btn.evaluate("el => { el.click(); el.click(); }")
    else:
        btn.click(click_count=2)
    expect(btn).to_be_disabled()                               # disabled while the request is out
    expect(_toast(page)).to_have_text(STARTING)
    _settle(page)
    page.wait_for_timeout(500)
    assert len(stub.posts) == 1
    assert len(_watch_rows(ids["brooklyn"])) == 1
    assert errors == []


def test_off_and_signed_out(server, browser, stub, monkeypatch):
    ids = _seed()
    # the setting is on and an editor sees the buttons ...
    page, _errors = _grid(browser, server)
    expect(_play_btn(page, ids["brooklyn"])).to_have_count(1)
    page.close()
    # ... signed out (the public page), nothing about Play shows, and the public API has no `play` key
    page, _errors = _grid(browser, server, path="/")
    expect(_card(page, ids["brooklyn"])).to_have_count(1)
    assert page.locator("[data-play-title]").count() == 0
    body = page.request.get(f"{server}/api/titles").json()
    assert all("play" not in t for t in body["titles"])
    page.close()
    # the setting empty: an editor sees none either
    monkeypatch.setattr("app.HA_PLAY_WEBHOOK_URL", "")
    page, _errors = _grid(browser, server)
    expect(_card(page, ids["brooklyn"])).to_have_count(1)
    assert page.locator("[data-play-title]").count() == 0
    page.goto(f"{server}/admin#/t/{ids['heat']}")
    page.wait_for_selector(".detail-view .hero-title")
    assert page.locator("[data-play-title], .play-bar").count() == 0
    assert stub.posts == []


def test_second_visit(server, browser, stub):
    ids = _seed()
    page, errors = _grid(browser, server)
    _card(page, ids["brooklyn"]).locator(".poster-wrap").click(position={"x": 12, "y": 50})
    page.wait_for_selector(".detail-view .hero-title")
    page.locator("#btn-back").click()
    page.wait_for_selector(".movie-card")
    _play_btn(page, ids["brooklyn"]).click()
    expect(_toast(page)).to_have_text(STARTING)
    _settle(page)
    page.wait_for_timeout(300)
    assert len(stub.posts) == 1
    assert errors == []


def test_filtering_the_grid_keeps_the_buttons_working(server, browser, stub):
    ids = _seed()
    page, _errors = _grid(browser, server)
    page.fill("#grid-search", "Brook")
    page.wait_for_function("document.querySelectorAll('.movie-card').length === 1")
    _play_btn(page, ids["brooklyn"]).click()
    expect(_toast(page)).to_have_text(STARTING)
    _settle(page)
    page.wait_for_timeout(300)
    assert len(stub.posts) == 1


@pytest.mark.parametrize("width", [412, 360])
def test_touch_target_and_no_sideways_scroll(server, browser, stub, width):
    ids = _seed()
    page, errors = _grid(browser, server, width)
    expect(_play_btn(page, ids["heat"])).to_have_count(1)
    for key in ("brooklyn", "heat"):
        box = _play_btn(page, ids[key]).bounding_box()
        assert box["width"] >= 44 and box["height"] >= 44, box
        card = _card(page, ids[key]).bounding_box()
        assert box["x"] >= card["x"] - 0.5 and box["x"] + box["width"] <= card["x"] + card["width"] + 0.5
    _no_hscroll(page, width)
    _play_btn(page, ids["heat"]).click()
    expect(page.locator(".play-sheet")).to_be_visible()
    _no_hscroll(page, width)
    sheet = page.locator(".play-sheet").bounding_box()
    assert sheet["x"] >= 0 and sheet["x"] + sheet["width"] <= width
    for b in page.locator(".play-sheet button").all():
        assert b.bounding_box()["height"] >= 44
    page.keyboard.press("Escape")
    _play_btn(page, ids["brooklyn"]).click()
    expect(_toast(page)).to_have_text(STARTING)
    _no_hscroll(page, width)
    assert errors == []


# ----------------------------------------------------------------------------------- the details page
def _detail(browser, server, title_id, width=412):
    page = browser.new_page(viewport={"width": width, "height": 915})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server}/admin#/t/{title_id}")
    page.wait_for_selector(".detail-view .hero-title")
    return page, errors


def test_detail_buttons(server, browser, stub):
    ids = _seed()
    page, errors = _detail(browser, server, ids["heat"])
    bar = page.locator(".detail-view .play-bar")
    expect(bar.get_by_role("button", name="Play on YouTube")).to_be_visible()
    expect(bar.get_by_role("button", name="Start the disc")).to_be_visible()
    expect(bar).to_contain_text(SHELF_LINE)
    assert bar.bounding_box()["y"] < 500                       # near the top of the page, with the title
    assert page.locator(".watch-row").count() == 0
    bar.get_by_role("button", name="Start the disc").click()
    expect(page.locator(".play-note")).to_contain_text("Starting Heat on Family Room TV.")
    expect(page.locator(".play-note")).to_contain_text(SHELF_LINE)
    page.wait_for_selector(".watch-row")                       # redrawn from the answer: the watch shows
    assert stub.posts == [{"event": "matinee_play", "mode": "disc", "source": "AV Receiver", "title": "Heat"}]
    assert [rows[1] for rows in _watch_rows(ids["heat"])] == [[ids["people"]["Nick"], ids["people"]["Lauren"]]]
    _no_hscroll(page, 412)
    assert errors == []

    page, errors = _detail(browser, server, ids["brooklyn"])
    bar = page.locator(".detail-view .play-bar")
    expect(bar.locator("button")).to_have_count(1)
    expect(bar.get_by_role("button", name="Play on YouTube")).to_be_visible()
    bar.get_by_role("button", name="Play on YouTube").click()
    expect(_toast(page)).to_have_text(STARTING)
    page.wait_for_selector(".watch-row")
    assert len(stub.posts) == 2 and stub.posts[1]["content_id"] == YT_ID
    assert errors == []

    page, errors = _detail(browser, server, ids["elf"])
    assert page.locator(".play-bar, [data-play-title], [data-play-mode]").count() == 0


def test_detail_failure_keeps_the_page(server, browser, stub):
    ids = _seed()
    stub.status = 500
    page, errors = _detail(browser, server, ids["brooklyn"])
    page.locator(".play-bar").get_by_role("button", name="Play on YouTube").click()
    expect(_toast(page)).to_have_text("Couldn't reach the TV. Brooklyn wasn't started.")
    _settle(page)
    assert page.locator(".watch-row").count() == 0
    assert errors == []


@pytest.mark.parametrize("width", [412, 360])
def test_detail_no_sideways_scroll(server, browser, stub, width):
    ids = _seed()
    page, _errors = _detail(browser, server, ids["heat"], width)
    expect(page.locator(".play-bar")).to_be_visible()
    for b in page.locator(".play-bar button").all():
        assert b.bounding_box()["height"] >= 44
    _no_hscroll(page, width)


# ------------------------------------------------------------------------------------------ Movie Night
def _movie_night(browser, server, width=412):
    page = browser.new_page(viewport={"width": width, "height": 915})
    errors, plays = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: plays.append(r.post_data_json) if r.url.endswith("/play") else None)
    page.goto(f"{server}/admin")
    page.wait_for_selector(".movie-card")
    page.click("#btn-header-movie-night")
    page.click('.movie-night-tab[data-mode="never_watched"]')
    page.wait_for_selector(".movie-night-card")
    return page, plays, errors


def _pick_after(page, action, audience=None):
    """Run `action` (a click that asks for a new pick) and wait for the new pick's card."""
    with page.expect_response(lambda r: "/recommendations" in r.url and (audience is None or f"audience={audience}" in r.url)):
        action()
    page.wait_for_selector(".movie-night-card")


def test_movie_night_buttons(server, browser, stub):
    import app as core

    ids = _seed()
    con = core.connect()                                       # Brooklyn is the only film Movie Night can pick
    con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, '2025-01-01')", (ids["heat"],))
    con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, '2025-01-01')", (ids["elf"],))
    con.commit()
    con.close()
    page, plays, errors = _movie_night(browser, server)
    expect(page.locator(".movie-night-title")).to_have_text("Brooklyn")
    bar = page.locator("#movie-night-content .play-bar")
    expect(bar.locator("button")).to_have_count(1)

    expect(bar.get_by_role("button", name="Play on YouTube")).to_be_visible()
    bar.get_by_role("button", name="Play on YouTube").click()    # Everyone: no attendees key
    expect(_toast(page)).to_have_text(STARTING)
    assert plays == [{"mode": "youtube"}]
    assert [r[1] for r in _watch_rows(ids["brooklyn"])] == [sorted(ids["people"].values())]
    _settle(page)

    adults = sorted(ids["people"][n] for n in ("Nick", "Lauren"))
    kids = sorted(ids["people"][n] for n in ("Midge", "Alice", "Paige"))
    for n, (audience, want) in enumerate((("adults", adults), ("kids", kids)), start=2):
        _clear_watches(ids["brooklyn"])                         # Brooklyn is unseen by the new audience again
        _pick_after(page, lambda: page.click(f'.movie-night-audience-option[data-audience="{audience}"]'), audience)
        page.locator("#movie-night-content .play-bar").get_by_role("button", name="Play on YouTube").click()
        _until(page, lambda: len(plays) == n, f"post {n}")
        assert plays[-1]["mode"] == "youtube" and sorted(plays[-1]["attendees"]) == want
        _settle(page)
        assert [r[1] for r in _watch_rows(ids["brooklyn"])] == [want]
    assert len(stub.posts) == 3
    assert errors == []


def test_movie_night_second_roll(server, browser, stub):
    ids = _seed()
    import app as core

    con = core.connect()                                       # Brooklyn and Heat are the picks; Elf was seen
    con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, '2025-01-01')", (ids["elf"],))
    con.commit()
    con.close()
    page, plays, errors = _movie_night(browser, server)
    for _roll in range(3):
        _pick_after(page, lambda: page.click("#btn-movie-night-roll"))
        name = page.locator(".movie-night-title").inner_text()
        bar = page.locator("#movie-night-content .play-bar")
        expect(bar.locator("button").first).to_be_visible()
        before = len(stub.posts)
        if name == "Heat":
            bar.get_by_role("button", name="Start the disc").click()
        else:
            bar.get_by_role("button", name="Play on YouTube").click()
        _until(page, lambda: len(stub.posts) > before, "the post")
        assert stub.posts[-1]["title"] == name
        _settle(page)
        page.wait_for_timeout(200)
        assert len(stub.posts) == before + 1
        _clear_watches(ids["brooklyn"], ids["heat"])            # the film is unseen again for the next roll
    assert errors == []


def test_movie_night_no_sideways_scroll(server, browser, stub):
    ids = _seed()
    import app as core

    con = core.connect()
    con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, '2025-01-01')", (ids["brooklyn"],))
    con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, '2025-01-01')", (ids["elf"],))
    con.commit()
    con.close()
    for width in (412, 360):
        page, _plays, _errors = _movie_night(browser, server, width)
        expect(page.locator(".movie-night-title")).to_have_text("Heat")
        bar = page.locator("#movie-night-content .play-bar")
        expect(bar).to_contain_text(SHELF_LINE)
        for b in bar.locator("button").all():
            assert b.bounding_box()["height"] >= 44
        dialog = page.locator(".movie-night-dialog")
        assert page.evaluate("(d) => d.scrollWidth <= d.clientWidth", dialog.element_handle())
        _no_hscroll(page, width)
        page.close()


# ------------------------------------------------------------------- Fandango at Home first (2026-11-27 design §5)
ADDAMS_FD = "15429"
ADDAMS_YT = "aDdAmSfAm91"
ADDAMS_BODY = {"event": "matinee_play", "mode": "stream", "source": "Fandango at Home", "title": "The Addams Family",
               "content_id": ADDAMS_FD}
ADDAMS_STARTING = "Starting The Addams Family on Family Room TV. Added to watch history."


def _seed_addams():
    """_seed's films, and The Addams Family (1991, PG-13) with all three ways: a DVD on the shelf, and one Digital HD
    copy on Fandango at Home holding both its Fandango id (15429) and a YouTube id."""
    import app as core

    ids = _seed()
    con = core.connect()
    shelf = con.execute("SELECT id FROM locations WHERE name = 'Shelf 2'").fetchone()[0]
    ids["addams"] = seed_film(con, "The Addams Family", 1991, "PG-13", copies=[
        ("physical", "DVD", None, None, shelf),
        ("digital", "Digital HD", "Fandango at Home", ADDAMS_YT, None, ADDAMS_FD)])["id"]
    con.close()
    return ids


def test_fandango_one_tap_plays_without_a_chooser(server, browser, stub):
    ids = _seed_addams()
    page, errors = _grid(browser, server)
    _play_btn(page, ids["addams"]).click()
    expect(_toast(page)).to_have_text(ADDAMS_STARTING)
    expect(page.locator(".play-sheet")).to_have_count(0)
    assert stub.posts == [ADDAMS_BODY]
    _settle(page)
    _play_btn(page, ids["heat"]).click()                        # YouTube and a disc, no Fandango: still the chooser
    sheet = page.locator(".play-sheet")
    expect(sheet).to_be_visible()
    expect(sheet.locator("[data-way]")).to_have_count(2)
    expect(sheet.get_by_role("button", name="Play on Fandango at Home")).to_have_count(0)
    sheet.get_by_role("button", name="Cancel").click()
    expect(sheet).to_have_count(0)
    assert stub.posts == [ADDAMS_BODY]
    assert errors == []


def test_detail_shows_every_way_fandango_first(server, browser, stub):
    ids = _seed_addams()
    page, errors = _detail(browser, server, ids["addams"])
    bar = page.locator(".detail-view .play-bar")
    expect(bar.locator("button")).to_have_count(3)
    assert [b.inner_text().strip() for b in bar.locator("button").all()] == [
        "Play on Fandango at Home", "Play on YouTube", "Start the disc"]
    expect(bar).to_contain_text(f"DVD · {SHELF}")
    bar.get_by_role("button", name="Play on YouTube").click()     # the fallback is here
    expect(_toast(page)).to_have_text(ADDAMS_STARTING)
    assert stub.posts == [{"event": "matinee_play", "mode": "stream", "source": "YouTube",
                           "title": "The Addams Family", "content_id": ADDAMS_YT}]
    _settle(page)
    bar = page.locator(".detail-view .play-bar")                  # redrawn from the answer
    bar.get_by_role("button", name="Play on Fandango at Home").click()
    _until(page, lambda: len(stub.posts) == 2, "the Fandango post")
    assert stub.posts[1] == ADDAMS_BODY
    _settle(page)
    expect(page.locator(".copy-row").filter(has_text="Fandango at Home link")).to_have_count(1)
    assert len(_watch_rows(ids["addams"])) == 1                   # one watch a day, whichever way
    assert errors == []


def test_movie_night_offers_fandango_alone(server, browser, stub):
    import app as core

    ids = _seed_addams()
    con = core.connect()                                       # The Addams Family is the only film Movie Night can pick
    for key in ("brooklyn", "heat", "elf"):
        con.execute("INSERT INTO watches (title_id, watched_date) VALUES (?, '2025-01-01')", (ids[key],))
    con.commit()
    con.close()
    page, plays, errors = _movie_night(browser, server)
    expect(page.locator(".movie-night-title")).to_have_text("The Addams Family")
    bar = page.locator("#movie-night-content .play-bar")
    expect(bar.locator("button")).to_have_count(1)
    expect(bar.get_by_role("button", name="Play on Fandango at Home")).to_be_visible()
    bar.get_by_role("button", name="Play on Fandango at Home").click()
    expect(_toast(page)).to_have_text(ADDAMS_STARTING)
    assert plays == [{"mode": "fandango"}] and stub.posts == [ADDAMS_BODY]
    assert errors == []


@pytest.mark.parametrize("width", [412, 360])
def test_three_ways_no_sideways_scroll(server, browser, stub, width):
    ids = _seed_addams()
    page, _errors = _detail(browser, server, ids["addams"], width)
    expect(page.locator(".play-bar button")).to_have_count(3)
    for b in page.locator(".play-bar button").all():
        assert b.bounding_box()["height"] >= 44
        box = b.bounding_box()
        assert box["x"] >= 0 and box["x"] + box["width"] <= width
    _no_hscroll(page, width)
    page.close()
    page, _errors = _grid(browser, server, width)
    expect(_play_btn(page, ids["addams"])).to_have_count(1)
    _no_hscroll(page, width)
