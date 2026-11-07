"""Playwright smoke test for the #/ask view at phone widths.

Skipped when playwright or a Chromium build is missing (CI does not install
them). /admin/api/me and /admin/api/ask are stubbed with page.route, so no
API key or network is needed.
"""
import glob
import json
import os
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

ME = {"editor": True, "email": "lan", "tmdb": False, "ask": True}
ASK = {
    "answer": "Two <b>ideas</b>.",
    "items": [{
        "id": 1, "title": "Inside Out", "year": 2015, "poster_url": None,
        "formats": ["4K UHD"], "has_disc": True, "has_digital": False,
        "copies": [{"kind": "physical", "format": "4K UHD", "service": None,
                    "location_path": "Family Room › Media Cabinet › Shelf 1"}],
    }],
    "turns_left": 4,
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


def _fulfil(body):
    return lambda route: route.fulfill(status=200, content_type="application/json", body=json.dumps(body))


def test_ask_view_at_phone_widths(server, browser):
    page = browser.new_page(viewport={"width": 412, "height": 915})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/admin/api/me", _fulfil(ME))
    asked = []
    page.route("**/admin/api/ask", lambda r: (asked.append(r.request.post_data_json), _fulfil(ASK)(r)))

    page.goto(f"{server}/admin#/ask")
    page.wait_for_selector("#btn-header-ask[aria-current='page']")
    page.fill("#ask-q", "something for the girls tonight")
    page.press("#ask-q", "Enter")
    page.wait_for_selector(".ask-a")

    assert asked == [{"thread": [{"q": "something for the girls tonight"}]}]
    assert page.inner_text(".ask-a").strip() == "Two <b>ideas</b>."
    assert page.locator(".ask-a b").count() == 0
    row = page.locator('.title-row[href="#/t/1"]')
    assert row.count() == 1
    assert "Shelf 1" in row.inner_text()
    assert "4 left" in page.inner_text(".ask-view")

    widths = {}
    for w in (412, 360):
        page.set_viewport_size({"width": w, "height": 915})
        page.wait_for_timeout(50)
        widths[w] = page.evaluate("document.documentElement.scrollWidth")
        assert widths[w] <= w, f"horizontal scroll at {w}px: scrollWidth={widths[w]}"
    print(f"scrollWidth: {widths}")
    assert errors == []


def test_ask_retry_resends_thread_and_public_404(server, browser):
    page = browser.new_page(viewport={"width": 360, "height": 800})
    page.route("**/admin/api/me", _fulfil(ME))
    bodies = []

    def ask(route):
        bodies.append(route.request.post_data_json)
        if len(bodies) == 1:
            route.fulfill(status=502, content_type="application/json",
                          body=json.dumps({"error": "AI search is unavailable right now, try again"}))
        else:
            _fulfil({**ASK, "turns_left": 0})(route)

    page.route("**/admin/api/ask", ask)
    page.goto(f"{server}/admin#/ask")
    page.fill("#ask-q", "where's the dark knight 4k?")
    page.press("#ask-q", "Enter")
    page.wait_for_selector("#ask-retry")
    assert "unavailable" in page.inner_text(".ask-error")
    page.click("#ask-retry")
    page.wait_for_selector(".ask-a")
    assert bodies == [{"thread": [{"q": "where's the dark knight 4k?"}]}] * 2
    assert page.locator(".ask-q").count() == 1
    assert page.is_disabled("#ask-q") and page.is_disabled("#ask-send")
    page.click("#ask-new")
    assert page.locator(".ask-q").count() == 0 and not page.is_disabled("#ask-q")

    page.goto(f"{server}/#/ask")
    page.wait_for_selector("text=Page not found")
    assert page.locator("#btn-header-ask").count() == 0


def test_ask_no_retry_on_429(server, browser):
    page = browser.new_page(viewport={"width": 360, "height": 800})
    page.route("**/admin/api/me", _fulfil(ME))
    page.route("**/admin/api/ask", lambda r: r.fulfill(
        status=429, content_type="application/json",
        body=json.dumps({"error": "daily limit reached"})))
    page.goto(f"{server}/admin#/ask")
    page.fill("#ask-q", "anything")
    page.press("#ask-q", "Enter")
    page.wait_for_selector(".ask-error")
    assert "limit" in page.inner_text(".ask-error")
    assert page.locator("#ask-retry").count() == 0
