"""Playwright check that the About page shows the running commit to an editor only.
Same harness and skip guard as tests/test_people_ui.py: skipped when playwright or Chromium is missing, and the CI
"ui-smoke" job fails if any test here is skipped. /healthz is stubbed with a commit; /admin/api/me is stubbed as an
editor (200) for one page and as signed out (401) for the other.
"""
import glob
import json
import os
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
COMMIT = "63ff0bc"


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


def _open_about(browser, server, path, me_status, me_body):
    page = browser.new_page(viewport={"width": 412, "height": 915})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.route("**/healthz", lambda r: r.fulfill(status=200, content_type="application/json",
                                                 body=json.dumps({"ok": True, "titles": 0, "copies": 0,
                                                                  "commit": COMMIT})))
    page.route("**/admin/api/me", lambda r: r.fulfill(status=me_status, content_type="application/json",
                                                      body=json.dumps(me_body)))
    page.goto(f"{server}{path}")
    page.wait_for_selector(".about-view")
    page.wait_for_timeout(400)
    assert errors == []
    return page


def test_an_editor_sees_the_running_commit_on_about(server, browser):
    page = _open_about(browser, server, "/admin#/about", 200, {"editor": True, "email": "lan", "tmdb": False,
                                                              "ask": False, "wishlist": False})
    assert page.locator(".about-commit").inner_text() == f"Running commit {COMMIT}"


def test_the_public_about_page_shows_no_commit(server, browser):
    page = _open_about(browser, server, "/#/about", 401, {"error": "sign in"})
    assert page.locator(".about-commit").count() == 0
