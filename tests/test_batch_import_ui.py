"""Playwright test for the batch barcode import, the TMDB type-ahead and the scanner start (issue #69) at phone width.

Same harness and skip guard as tests/test_lists_ui.py (whose fixtures it reuses): the live app runs in this process
on a temp database, so the UPC cache is seeded directly and TMDB is stubbed on app.tmdb_search / app.tmdb_details.
The CI "ui-smoke" job runs this file and fails if any test here is skipped.

Scenario: the shelf holds The Dark Knight Trilogy [4K UHD + Blu-ray + Digital] (UPC 883929637581, TMDB 155) and
Elf (Blu-ray, TMDB 10719); neither is in the catalog yet.
"""
import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import app as core  # noqa: E402
from conftest import seed_title  # noqa: E402
import test_people_ui as harness  # noqa: E402

server, browser = harness.server, harness.browser   # the same fixtures (a live app on a temp database, Chromium)

DARK_KNIGHT_UPC = "883929637581"
DARK_KNIGHT_RAW = "The Dark Knight Trilogy [4K UHD + Blu-ray + Digital]"
ELF_UPC = "025192384528"
ELF_RAW = "Elf (Blu-ray)"
DARK_KNIGHT = {"tmdb_id": 155, "title": "The Dark Knight", "year": 2008, "owned": False, "overview": "", "thumb": ""}
ELF = {"tmdb_id": 10719, "title": "Elf", "year": 2003, "owned": False, "overview": "", "thumb": ""}


def _seed_upcs(con):
    con.executemany("INSERT INTO upc_cache (upc, raw_title, brand, data) VALUES (?, ?, '', '{}')",
                    [(DARK_KNIGHT_UPC, DARK_KNIGHT_RAW), (ELF_UPC, ELF_RAW)])
    con.commit()


def _stub_tmdb(monkeypatch, hits):
    """app.tmdb_search answers from `hits` (a substring of the cleaned title -> result list); no network."""
    def fake_search(q):
        for needle, results in hits.items():
            if needle.lower() in q.lower():
                return list(results)
        return []
    monkeypatch.setattr(core, "tmdb_search", fake_search)


def _open_batch(browser, server, queue, width=412):
    page = browser.new_page(viewport={"width": width, "height": 915})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server}/admin#/add")
    page.wait_for_selector("#tab-scan")
    page.evaluate("(q) => localStorage.setItem('matinee_scan_queue', JSON.stringify(q))", queue)
    page.click("#tab-scan")
    page.wait_for_selector("#btn-batch-review")
    page.click("#btn-batch-review")
    page.wait_for_selector("#btn-batch-import-all")
    return page, errors


def _titles_with_copies(server, page):
    """{title: copies} for every title /api/titles lists, read from each title's own /api/titles/<id>."""
    rows = page.request.get(f"{server}/api/titles").json()["titles"]
    out = {}
    for row in rows:
        detail = page.request.get(f"{server}/api/titles/{row['id']}").json()
        if detail["copies"]:
            out[detail["title"]] = detail["copies"]
    return out


def test_import_all_adds_every_selected_disc_as_a_copy(server, browser, monkeypatch):
    con = core.connect()
    _seed_upcs(con)
    seed_title(con, 155, copies=())                    # in the catalog with no copies: the add needs no TMDB call
    seed_title(con, 10719, copies=())
    con.close()
    _stub_tmdb(monkeypatch, {"dark knight": [DARK_KNIGHT], "elf": [ELF]})
    page, errors = _open_batch(browser, server, [DARK_KNIGHT_UPC, ELF_UPC])
    page.click("#btn-batch-import-all")
    page.wait_for_selector("#toast.show")
    assert page.locator("#toast").inner_text().strip() == "Added 2 physical disc copies"
    copies = _titles_with_copies(server, page)
    assert {"The Dark Knight", "Elf"} <= set(copies), copies
    dk = copies["The Dark Knight"]
    assert [(c["kind"], c["format"]) for c in dk] == [("physical", "4K UHD")], dk
    assert [(c["kind"], c["format"]) for c in copies["Elf"]] == [("physical", "Blu-ray")]
    assert core.connect().execute("SELECT count(*) FROM copies").fetchone()[0] == 2
    assert errors == []
    page.close()


def test_a_failed_add_is_reported_in_the_toast_and_stays_queued(server, browser, monkeypatch):
    con = core.connect()
    _seed_upcs(con)
    seed_title(con, 155, copies=())                    # Dark Knight adds; Elf is not in the catalog
    con.close()
    _stub_tmdb(monkeypatch, {"dark knight": [DARK_KNIGHT], "elf": [ELF]})

    def no_tmdb(tmdb_id):
        raise core.TmdbError("unavailable", "TMDB is unavailable right now.")
    monkeypatch.setattr(core, "tmdb_details", no_tmdb)   # Elf is not in the catalog, so its add cannot fetch it

    page, errors = _open_batch(browser, server, [DARK_KNIGHT_UPC, ELF_UPC])
    page.click("#btn-batch-import-all")
    page.wait_for_selector("#toast.show")
    assert page.locator("#toast").inner_text().strip() == "Added 1, 1 failed"
    assert core.connect().execute("SELECT count(*) FROM copies").fetchone()[0] == 1   # the failure wrote nothing
    assert page.evaluate("JSON.parse(localStorage.getItem('matinee_scan_queue'))") == [ELF_UPC]
    assert errors == []
    page.close()


def test_type_ahead_renders_only_the_latest_query(server, browser, monkeypatch):
    import threading
    slow_done = threading.Event()

    def search(q):
        if q.lower() == "dune":
            slow_done.wait(timeout=20)                 # the answer for "dune" arrives after "dune 1984"
            return [{"tmdb_id": 841, "title": "Dune", "year": 2021, "owned": False, "overview": "", "thumb": ""}]
        return [{"tmdb_id": 841, "title": "Dune", "year": 1984, "owned": False, "overview": "", "thumb": ""}]
    monkeypatch.setattr(core, "tmdb_search", search)

    page = browser.new_page(viewport={"width": 412, "height": 915})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server}/admin#/add")
    page.wait_for_selector("#tmdb-search-input")
    page.fill("#tmdb-search-input", "dune")
    page.wait_for_timeout(600)                         # past the debounce: the "dune" request is in flight
    page.fill("#tmdb-search-input", "dune 1984")
    page.wait_for_function("document.querySelector('.result-year')?.textContent.trim() === '1984'")
    slow_done.set()                                    # the stale answer lands now
    page.wait_for_timeout(500)
    years = page.locator("#tmdb-results-container .result-year").all_inner_texts()
    assert years == ["1984"], years
    assert errors == []
    page.close()


def test_leaving_during_the_camera_prompt_stops_the_stream(server, browser):
    page = browser.new_page(viewport={"width": 412, "height": 915})
    page.add_init_script("""
        window.__stopped = 0;
        window.BarcodeDetector = class { constructor() {} async detect() { return []; } };
        Object.defineProperty(HTMLMediaElement.prototype, 'srcObject', { configurable: true,
            get() { return null; }, set(v) {} });
        HTMLMediaElement.prototype.play = function () { return Promise.resolve(); };
        navigator.mediaDevices.getUserMedia = () => new Promise((resolve) => {
            window.__grant = () => resolve({ getTracks: () => [{ stop() { window.__stopped++; } }] });
        });
    """)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server}/admin#/add")
    page.wait_for_selector("#tab-scan")
    page.click("#tab-scan")
    page.wait_for_function("typeof window.__grant === 'function'")
    page.click("#tab-search")                          # leave while the permission prompt is still open
    page.evaluate("window.__grant()")
    page.wait_for_function("window.__stopped >= 1")
    assert page.evaluate("window.__stopped") == 1
    assert errors == []
    page.close()
