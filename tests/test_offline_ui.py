"""Playwright: the service worker (static/sw.js) in Chromium at phone width.

Scenario: The Dark Knight (TMDB 155), Inception (27205) and Toy Story (862) are on the shelf; The Dark Knight has a
watch with the note "girls loved it" that Midge attended. A visitor opens https://movies.example.com/, goes into a
shop with no signal, reloads and searches for "The Dark Knight". An editor's phone (LAN_EDIT, so 127.0.0.1 is an
editor) browses the same titles under /admin and under /, and nothing editor-only reaches any cache. A private
instance (PUBLIC_VIEW=0) keeps only the shell. A deploy that changes one module replaces the precache.

Runs against the real app on a temp database, like tests/test_csp_ui.py; every page is also checked for CSP
violations by the autouse fixture in tests/conftest.py. The CI "ui-smoke" job fails if anything here skips.
"""
import glob
import os
import threading

import pytest

pytest.importorskip("playwright.sync_api")
from playwright import sync_api  # noqa: E402
from werkzeug.serving import make_server  # noqa: E402

import app as core  # noqa: E402
from conftest import seed_people, seed_title  # noqa: E402

NOTE = "girls loved it"
KID = "Midge"
PHONE = {"width": 412, "height": 915}


def _chromium_path():
    roots = [os.environ.get("PLAYWRIGHT_BROWSERS_PATH") or "", "/opt/pw-browsers"]
    for root in filter(None, roots):
        for pat in ("chromium-*/chrome-linux64/chrome", "chromium-*/chrome-linux/chrome"):
            hits = sorted(glob.glob(os.path.join(root, pat)))
            if hits:
                return hits[-1]
    return None  # let playwright use its own default


@pytest.fixture
def shelf(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.WISHLIST_URL", "")
    core.init_db()
    monkeypatch.setattr("app._standalone_db", None)
    con = core.connect()
    ids = {"dark_knight": seed_title(con, 155), "inception": seed_title(con, 27205), "toy_story": seed_title(con, 862)}
    people = seed_people(con)
    watch = con.execute("INSERT INTO watches (title_id, watched_date, notes) VALUES (?, '2026-11-07', ?)",
                        (ids["dark_knight"], NOTE)).lastrowid
    con.execute("INSERT INTO watch_people (watch_id, person_id) VALUES (?, ?)", (watch, people[KID]))
    con.commit()
    con.close()
    return ids


@pytest.fixture
def server(shelf):
    srv = make_server("127.0.0.1", 0, core.app, threaded=True)
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    yield f"http://127.0.0.1:{srv.server_port}"
    srv.shutdown()
    t.join(timeout=5)
    srv.server_close()


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
def context(browser):
    ctx = browser.new_context(viewport=PHONE, service_workers="allow")
    yield ctx
    ctx.close()


def _controlled(page):
    """Wait until the worker is installed, active and controlling this page."""
    page.wait_for_function("navigator.serviceWorker.controller !== null", timeout=15000)
    page.evaluate("navigator.serviceWorker.ready.then(() => true)")


def _caches(page):
    """{cache name: [(url, cache-control, body text)]} for every cache this origin holds."""
    return page.evaluate("""async () => {
      const out = {};
      for (const name of await caches.keys()) {
        const cache = await caches.open(name);
        out[name] = [];
        for (const req of await cache.keys()) {
          const res = await cache.match(req);
          const type = res.headers.get("Content-Type") || "";
          const text = /json|html|javascript|css|svg/.test(type) ? await res.text() : "";
          out[name].push([req.url, res.headers.get("Cache-Control") || "", text]);
        }
      }
      return out;
    }""")


def _paths(entries, server):
    return sorted(url.removeprefix(server) for url, _cc, _text in entries)


def _wait_for_cached(page, url):
    page.wait_for_function(f"""async () => {{
      for (const name of await caches.keys()) {{
        if (await (await caches.open(name)).match({url!r}, {{ ignoreVary: true }})) return true;
      }}
      return false;
    }}""", timeout=15000)


def _grid(page):
    page.wait_for_selector(".movie-card", timeout=15000)
    return page.locator(".movie-card .card-title").all_inner_texts()


def _assert_nothing_private(cached, server):
    for name, entries in cached.items():
        for url, cache_control, text in entries:
            path = url.removeprefix(server)
            assert not path.startswith("/admin"), (name, url)
            assert NOTE not in text, (name, url)
            assert KID not in text, (name, url)
            if path != "/":   # the shell page, the one no-store answer the worker keeps (sw.js says why)
                assert "no-store" not in cache_control and "private" not in cache_control, (name, url)


# --- a visitor in a shop with no signal ---------------------------------------------------------------------------

def test_offline_reload_still_finds_the_dark_knight(context, server):
    page = context.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(server + "/")
    _grid(page)
    _controlled(page)
    _wait_for_cached(page, server + "/api/titles")
    assert page.locator("#offline-line").is_hidden()

    context.set_offline(True)
    page.reload()
    assert sorted(_grid(page)) == ["Inception", "The Dark Knight", "Toy Story"]
    page.wait_for_selector("#offline-line:not([hidden])", timeout=5000)
    assert page.inner_text("#offline-line").startswith("Offline, showing the collection as of ")

    page.fill("#grid-search", "The Dark Knight")
    page.wait_for_function("document.querySelectorAll('.movie-card').length === 1", timeout=5000)
    assert _grid(page) == ["The Dark Knight"]
    page.fill("#grid-search", "toy")
    page.wait_for_function("document.querySelector('.movie-card .card-title')?.innerText === 'Toy Story'",
                           timeout=5000)
    assert _grid(page) == ["Toy Story"]

    cached = _caches(page)
    assert all("/admin" not in url for entries in cached.values() for url, _cc, _t in entries)
    _assert_nothing_private(cached, server)

    context.set_offline(False)
    page.fill("#grid-search", "")
    page.wait_for_function("document.querySelectorAll('.movie-card').length === 3", timeout=5000)
    page.wait_for_selector("#offline-line[hidden]", state="attached", timeout=5000)   # live data again
    assert errors == []


def test_a_title_seen_online_opens_offline_and_one_never_seen_says_so(context, server, shelf):
    page = context.new_page()
    page.goto(server + "/")
    _grid(page)
    _controlled(page)
    page.goto(f"{server}/#/t/{shelf['dark_knight']}")
    page.wait_for_selector(".detail-title, h1", timeout=10000)
    _wait_for_cached(page, f"{server}/api/titles/{shelf['dark_knight']}")

    context.set_offline(True)
    page.reload()
    page.wait_for_function("document.body.innerText.includes('The Dark Knight')", timeout=10000)
    assert NOTE not in page.inner_text("body")
    page.goto(f"{server}/#/t/{shelf['inception']}")
    page.wait_for_function("document.body.innerText.includes(\"isn't saved on this device\")", timeout=10000)


# --- privacy: the editor's phone runs the same worker ---------------------------------------------------------------

def _without_no_store(route):
    """The editor's answer as if the server had forgotten Cache-Control: no-store."""
    response = route.fetch()
    headers = {k: v for k, v in response.headers.items() if k.lower() != "cache-control"}
    route.fulfill(response=response, headers=headers)


@pytest.mark.parametrize("no_store", ["sent", "stripped"])
def test_an_editor_browsing_leaves_nothing_editor_only_in_any_cache(context, server, shelf, monkeypatch, no_store):
    """`stripped`: /admin/api answers lose their no-store on the way, so only the worker's /admin rule keeps them out."""
    monkeypatch.setattr("app.LAN_EDIT", True)   # 127.0.0.1 is the LAN: every request here is an editor's
    if no_store == "stripped":
        context.route("**/admin/api/**", _without_no_store)
    page = context.new_page()
    page.goto(server + "/admin")
    _grid(page)
    _controlled(page)
    page.goto(f"{server}/admin#/t/{shelf['dark_knight']}")
    page.wait_for_function(f"document.body.innerText.includes({NOTE!r})", timeout=10000)   # the editor sees it
    page.goto(f"{server}/admin#/stats")
    page.wait_for_timeout(500)
    page.goto(server + "/")                         # the same phone on the public view
    _grid(page)
    page.goto(f"{server}/#/t/{shelf['dark_knight']}")
    _wait_for_cached(page, f"{server}/api/titles/{shelf['dark_knight']}")
    page.goto(server + "/admin")                    # and back, so /admin/api answers pass the worker again
    _grid(page)
    page.wait_for_timeout(500)

    cached = _caches(page)
    paths = [p for entries in cached.values() for p in _paths(entries, server)]
    assert "/api/titles" in paths and f"/api/titles/{shelf['dark_knight']}" in paths
    _assert_nothing_private(cached, server)

    # the worker never answers /admin either: offline, the editor's page is the browser's own error
    context.set_offline(True)
    with pytest.raises(sync_api.Error):
        page.goto(server + "/admin")


def test_the_editor_page_disables_its_controls_offline(context, server, shelf, monkeypatch):
    monkeypatch.setattr("app.LAN_EDIT", True)
    page = context.new_page()
    page.goto(f"{server}/admin#/t/{shelf['dark_knight']}")
    page.wait_for_function(f"document.body.innerText.includes({NOTE!r})", timeout=10000)
    enabled = "[...document.querySelectorAll('#view button, #view input, #view select')].filter(e => !e.disabled).length"
    assert page.evaluate(enabled) > 0

    context.set_offline(True)
    page.wait_for_function(f"{enabled} === 0", timeout=5000)
    assert page.inner_text("#offline-line") == "Offline. Nothing can be changed until the connection is back."

    context.set_offline(False)
    page.wait_for_function(f"{enabled} > 0", timeout=5000)
    assert page.locator("#offline-line").is_hidden()


def test_a_private_collection_keeps_only_the_shell(context, server, monkeypatch):
    monkeypatch.setattr("app.PUBLIC_VIEW", False)
    monkeypatch.setattr("app.LAN_EDIT", True)
    page = context.new_page()
    page.goto(server + "/admin")
    _grid(page)
    _controlled(page)
    page.reload()                                   # the editor's data now passes through the controlling worker
    _grid(page)
    page.wait_for_timeout(500)

    cached = _caches(page)
    assert list(cached) == [n for n in cached if n.startswith("matinee-shell-")] and len(cached) == 1
    paths = _paths(next(iter(cached.values())), server)
    assert paths and all(p.startswith("/static/") for p in paths), paths
    _assert_nothing_private(cached, server)


# --- staleness: a deploy replaces the shell -------------------------------------------------------------------------

def test_a_changed_module_hash_installs_a_new_precache(context, server, monkeypatch):
    page = context.new_page()
    page.goto(server + "/")
    _grid(page)
    _controlled(page)
    before = [n for n in page.evaluate("caches.keys()") if n.startswith("matinee-shell-")]
    assert len(before) == 1

    real = core.asset_versions()
    monkeypatch.setattr("app.asset_versions", lambda: dict(real, **{"js/about.js": "0123456789"}))
    page.evaluate("navigator.serviceWorker.getRegistration().then(r => r.update())")
    page.wait_for_function(f"caches.keys().then(k => !k.includes({before[0]!r}) && "
                           "k.some(n => n.startsWith('matinee-shell-')))", timeout=15000)

    shells = {n: e for n, e in _caches(page).items() if n.startswith("matinee-shell-")}
    assert len(shells) == 1
    paths = _paths(next(iter(shells.values())), server)
    assert "/static/js/about.js?v=0123456789" in paths
    [page_html] = [text for url, _cc, text in next(iter(shells.values())) if url == server + "/"]
    assert "/static/js/about.js?v=0123456789" in page_html   # the stored page names the new module, not the old
    assert f"/static/js/about.js?v={real['js/about.js']}" not in paths
    assert page.evaluate("navigator.serviceWorker.getRegistration().then(r => !r.waiting && !r.installing)")
    page.reload()                                    # the new worker controls the next load
    _grid(page)
    assert "0123456789" in page.evaluate("document.querySelector('script[type=importmap]').textContent")
