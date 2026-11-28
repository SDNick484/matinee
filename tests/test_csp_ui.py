"""The CSP guard in tests/conftest.py (autouse `csp_violations`) really sees a blocked script or image.

Scenario: an editor opens the collection on a phone (412 px) while The Dark Knight (TMDB 155) is on the shelf; a
poster URL from an unlisted origin and an injected inline script, the two things the policy exists to stop, are
each recorded, and the page itself loads without a single violation.
"""
import glob
import os
import threading

import pytest

pytest.importorskip("playwright.sync_api")
from playwright import sync_api  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

from conftest import seed_title  # noqa: E402


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
    import app as core
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.LAN_EDIT", True)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.WISHLIST_URL", "")
    core.init_db()
    monkeypatch.setattr("app._standalone_db", None)
    con = core.connect()
    seed_title(con, 155)
    con.close()
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


def _open(browser, server):
    page = browser.new_page(viewport={"width": 412, "height": 915})
    page.goto(server + "/admin")
    page.wait_for_selector(".poster-img, .poster-ph, .card", timeout=10000)
    return page


def test_the_page_loads_without_a_violation(browser, server, csp_violations):
    _open(browser, server)
    assert csp_violations == []


def test_an_injected_inline_script_is_blocked_and_recorded(browser, server, csp_violations):
    page = _open(browser, server)
    page.evaluate("""() => {
      window.ran = false;
      document.querySelector('#view').insertAdjacentHTML('beforeend', '<img src=x onerror="window.ran=true">');
      const s = document.createElement('script'); s.textContent = 'window.ran = true'; document.body.append(s);
    }""")
    page.wait_for_timeout(300)
    assert page.evaluate("window.ran") is False
    assert any("script-src" in m for m in csp_violations), csp_violations
    csp_violations.clear()            # expected here: without this the guard fails the test at teardown


def test_an_image_from_an_unlisted_origin_is_blocked_and_recorded(browser, server, csp_violations):
    page = _open(browser, server)
    page.evaluate("""() => { const i = new Image(); i.src = 'https://tracker.example.com/p.gif'; document.body.append(i); }""")
    page.wait_for_timeout(300)
    assert any("img-src" in m for m in csp_violations), csp_violations
    csp_violations.clear()
