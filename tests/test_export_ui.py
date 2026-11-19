"""Playwright smoke test for the grid's "Export these" control and the two shapes on #/stats (spec
2026-11-18-grid-export-design), at phone widths.

Same harness and skip guard as tests/test_lists_ui.py, against the real app and a temporary database: the CI
"ui-smoke" job runs this file and fails if any test here is skipped.

Scenario: Elf has a 4K UHD copy and Paddington 2 a Blu-ray copy.
"""
import csv
import io

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import app as core  # noqa: E402
from conftest import seed_title  # noqa: E402
import test_people_ui as harness  # noqa: E402

server, browser = harness.server, harness.browser   # the same fixtures (a live app on a temp database, Chromium)

COPY = "/admin/api/export.csv?format=4K+UHD&shape=copy"
TITLE = "/admin/api/export.csv?format=4K+UHD&shape=title"
NOTE = "Export ignores search, genre and watch filters."


def _seed():
    con = core.connect()
    seed_title(con, 10719, copies=("4K UHD",))      # Elf
    seed_title(con, 346648, copies=("Blu-ray",))    # Paddington 2
    con.close()


def _grid(browser, server, hash="#/", editor=True, width=412):
    page = browser.new_page(viewport={"width": width, "height": 915})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server}/{'admin' if editor else ''}{hash}")
    page.wait_for_function("document.querySelector('#grid-container') && "
                           "!document.querySelector('#grid-container').textContent.includes('Loading movies')")
    return page, errors


def _no_hscroll(page, width):
    sw = page.evaluate("document.documentElement.scrollWidth")
    assert sw == width, f"horizontal scroll at {width}px: scrollWidth={sw}"


def _ratio(fg, bg):
    def lum(rgb):
        c = [x / 255 for x in rgb]
        c = [x / 12.92 if x <= 0.03928 else ((x + 0.055) / 1.055) ** 2.4 for x in c]
        return 0.2126 * c[0] + 0.7152 * c[1] + 0.0722 * c[2]
    hi, lo = sorted([lum(fg), lum(bg)], reverse=True)
    return (hi + 0.05) / (lo + 0.05)


def _contrast(page, selector):
    """Text colour against the first opaque background behind it, as a WCAG ratio."""
    fg, bg = page.evaluate("""(sel) => {
        const el = document.querySelector(sel);
        const rgb = (s) => (s.match(/[\\d.]+/g) || []).map(Number);
        let node = el, bg = [0, 0, 0, 0];
        while (node) {
          const c = rgb(getComputedStyle(node).backgroundColor);
          if (c.length >= 3 && (c.length === 3 || c[3] > 0)) { bg = c; break; }
          node = node.parentElement;
        }
        return [rgb(getComputedStyle(el).color), bg];
    }""", selector)
    return _ratio(fg[:3], bg[:3])


def _open_menu(page):
    page.click("#btn-grid-export")
    page.wait_for_selector("#popover-export.open")


def test_format_filter_shows_two_links_and_the_download_is_that_view(server, browser):
    _seed()
    page, errors = _grid(browser, server, "#/?format=4K+UHD")
    assert page.locator("#grid-export").count() == 1
    assert page.locator("#grid-export-note").count() == 0
    btn = page.locator("#btn-grid-export")
    assert "Export these" in btn.inner_text() and btn.bounding_box()["height"] >= 44
    assert not page.locator("#export-copy").is_visible()      # behind the menu until it is opened
    _open_menu(page)
    copy, title = page.locator("#export-copy"), page.locator("#export-title")
    assert copy.inner_text().strip() == "One row per copy" and title.inner_text().strip() == "One row per title"
    assert copy.get_attribute("href") == COPY and title.get_attribute("href") == TITLE
    assert copy.get_attribute("download") is not None
    assert copy.bounding_box()["height"] >= 44 and title.bounding_box()["height"] >= 44
    with page.expect_download() as dl:
        copy.click()
    rows = list(csv.DictReader(io.StringIO(open(dl.value.path(), encoding="utf-8-sig").read())))
    assert [(r["title"], r["format"]) for r in rows] == [("Elf", "4K UHD")]    # not Paddington 2
    assert not page.locator("#popover-export").evaluate("e => e.classList.contains('open')")   # closed behind the tap
    _open_menu(page)
    with page.expect_download() as dl:
        title.click()
    rows = list(csv.DictReader(io.StringIO(open(dl.value.path(), encoding="utf-8-sig").read())))
    assert [r["title"] for r in rows] == ["Elf"] and dl.value.suggested_filename.endswith("-titles.csv")
    assert errors == []


def test_no_filter_no_control_and_unhonored_filters_alone_do_not_show_it(server, browser):
    _seed()
    for hash in ("#/", "#/?q=elf", "#/?genre=Comedy", "#/?quality=4k", "#/?watched=unwatched", "#/?rating=8%2B"):
        page, errors = _grid(browser, server, hash)
        assert page.locator("#grid-export").count() == 0, hash
        assert page.locator("#grid-export-note").count() == 0, hash
        assert errors == []
        page.close()


def test_other_filters_add_the_one_line_note(server, browser):
    _seed()
    page, errors = _grid(browser, server, "#/?format=4K+UHD&genre=Comedy")
    note = page.locator("#grid-export-note")
    assert note.inner_text().strip() == NOTE
    _open_menu(page)
    assert page.locator("#export-copy").get_attribute("href") == COPY    # the genre is not in the link
    page.close()
    # a search or a watch filter says the same; the Quality menu is named, since it is also not exported
    for hash, text in (("#/?format=4K+UHD&q=elf", NOTE), ("#/?format=4K+UHD&watched=unwatched", NOTE),
                       ("#/?format=4K+UHD&rating=8%2B", NOTE),
                       ("#/?format=4K+UHD&quality=hd", "Export ignores search, genre, quality and watch filters.")):
        page, errors = _grid(browser, server, hash)
        assert page.locator("#grid-export-note").inner_text().strip() == text, hash
        page.close()


def test_every_honored_filter_reaches_the_link(server, browser):
    _seed()
    cases = (
        ("#/?kind=digital&service=Movies+Anywhere", "kind=digital&service=Movies+Anywhere"),
        ("#/?medium=disc", "kind=physical"),
        ("#/?medium=streaming", "kind=digital"),
        ("#/?location_id=7", "location=7"),
        ("#/?format=Blu-ray&location_id=7", "format=Blu-ray&location=7"),
    )
    for hash, query in cases:
        page, errors = _grid(browser, server, hash)
        page.wait_for_selector("#btn-grid-export")
        _open_menu(page)
        assert page.locator("#export-copy").get_attribute("href") == f"/admin/api/export.csv?{query}&shape=copy", hash
        assert page.locator("#export-title").get_attribute("href") == f"/admin/api/export.csv?{query}&shape=title"
        page.close()
    # "Disc + Streaming" cannot be a copy filter: no control by itself
    page, errors = _grid(browser, server, "#/?medium=both")
    assert page.locator("#grid-export").count() == 0 and errors == []


def test_anonymous_visitor_never_sees_the_control(server, browser):
    _seed()
    for hash in ("#/?format=4K+UHD", "#/?format=4K+UHD&genre=Comedy", "#/?location_id=7&medium=disc"):
        page, errors = _grid(browser, server, hash, editor=False)
        html = page.content()
        assert page.locator("#grid-export, #btn-grid-export, #export-copy, #export-title").count() == 0
        assert "export.csv" not in html and "Export these" not in html
        assert errors == []
        page.close()
    page = browser.new_page(viewport={"width": 412, "height": 915})
    page.goto(f"{server}/#/stats")
    page.wait_for_selector(".stats-view h1")
    assert page.locator("#btn-export-csv, #btn-export-csv-titles").count() == 0


def test_stats_keeps_export_csv_and_gains_the_title_shape(server, browser):
    _seed()
    page = browser.new_page(viewport={"width": 412, "height": 915})
    page.goto(f"{server}/admin#/stats")
    page.wait_for_selector("#btn-export-csv")
    copy, title = page.locator("#btn-export-csv"), page.locator("#btn-export-csv-titles")
    assert copy.get_attribute("href") == "/admin/api/export.csv" and "Export CSV" in copy.inner_text()
    assert title.get_attribute("href") == "/admin/api/export.csv?shape=title"
    assert "One row per title" in title.inner_text()
    assert copy.bounding_box()["height"] >= 44 and title.bounding_box()["height"] >= 44
    with page.expect_download() as dl:
        title.click()
    rows = list(csv.DictReader(io.StringIO(open(dl.value.path(), encoding="utf-8-sig").read())))
    assert sorted(r["title"] for r in rows) == ["Elf", "Paddington 2"]


def test_no_horizontal_scroll_and_aa_contrast_at_412_and_360(server, browser):
    _seed()
    for width in (412, 360):
        page, errors = _grid(browser, server, "#/?format=4K+UHD&genre=Comedy&q=elf&quality=hd", width=width)
        _no_hscroll(page, width)
        _open_menu(page)
        _no_hscroll(page, width)
        for sel in ("#btn-grid-export", "#export-copy", "#export-title", "#grid-export-note"):
            box = page.locator(sel).bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= width, (sel, width)
            assert _contrast(page, sel) >= 4.5, (sel, width)
        for sel in ("#btn-grid-export", "#export-copy", "#export-title"):
            assert page.locator(sel).bounding_box()["height"] >= 44, (sel, width)
        assert errors == []
        page.close()
        page = browser.new_page(viewport={"width": width, "height": 915})
        page.goto(f"{server}/admin#/stats")
        page.wait_for_selector("#btn-export-csv-titles")
        _no_hscroll(page, width)
        for sel in ("#btn-export-csv", "#btn-export-csv-titles"):
            box = page.locator(sel).bounding_box()
            assert box["x"] >= 0 and box["x"] + box["width"] <= width and box["height"] >= 44, (sel, width)
        page.close()
