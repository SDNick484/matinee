"""Playwright smoke test for the Calendar (spec 2026-11-19-calendar-settings-design §3) at phone width (412 and 360 px).

Same harness and skip guard as tests/test_stats_ui.py, against the real app and a temporary database: the CI
"ui-smoke" job runs this file and fails if any test here is skipped.

Scenario: today is 2026-11-19. The 40-Year-Old Virgin watched 2026-11-14; Elf watched 2026-11-14 and 2026-11-28
(its poster file is missing, so the placeholder shows); Jaws logged with the bucket "1_2_years" (stored 2025-11-19).
"""
import base64
import datetime as dt

import pytest

sync_api = pytest.importorskip("playwright.sync_api")

import app as core  # noqa: E402
import test_people_ui as harness  # noqa: E402

server, browser = harness.server, harness.browser   # the same fixtures (a live app on a temp database, Chromium)

PNG = base64.b64decode("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGP4z8DwHwAFAAH/q842iQAAAABJRU5ErkJggg==")
VIEWPORTS = pytest.mark.parametrize("width", [412, 360], ids=["412px", "360px"])


@pytest.fixture
def seeded(server, monkeypatch):
    monkeypatch.setattr("app.pacific_today", lambda now=None: dt.date(2026, 11, 19))
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    core.IMAGE_DIR.mkdir(parents=True, exist_ok=True)
    (core.IMAGE_DIR / "18-p342.jpg").write_bytes(PNG)
    con = core.connect()
    ids = {}
    for tmdb_id, title, year, poster in ((18, "The 40-Year-Old Virgin", 2005, "18-p342.jpg"),
                                         (10719, "Elf", 2003, "10719-p342.jpg"), (578, "Jaws", 1975, None)):
        ids[title] = con.execute(
            "INSERT INTO titles (tmdb_id, title, sort_title, search_text, year, poster_file, fetched_at) "
            "VALUES (?, ?, ?, ?, ?, ?, '2026-11-01T00:00:00Z')",
            (tmdb_id, title, title.lower(), title.lower(), year, poster)).lastrowid
        con.execute("INSERT INTO copies (title_id, kind, format) VALUES (?, 'physical', 'Blu-ray')", (ids[title],))
    for title, day, bucket, created in (("The 40-Year-Old Virgin", "2026-11-14", None, "2026-11-14 20:00:00"),
                                        ("Elf", "2026-11-14", None, "2026-11-14 21:00:00"),
                                        ("Elf", "2026-11-28", None, "2026-11-28 20:00:00"),
                                        ("Jaws", "2025-11-19", "1_2_years", "2026-11-01 20:00:00")):
        con.execute("INSERT INTO watches (title_id, watched_date, date_bucket, notes, created_at) VALUES (?, ?, ?, 'girls loved it', ?)",
                    (ids[title], day, bucket, created))
    con.commit()
    con.close()
    return server, ids


def _open(browser, server, hash="#/calendar", editor=False, width=412):
    page = browser.new_page(viewport={"width": width, "height": 915})
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(f"{server}/{'admin' if editor else ''}{hash}")
    return page, errors


def _calendar(browser, server, hash="#/calendar", editor=False, width=412):
    page, errors = _open(browser, server, hash, editor, width)
    page.wait_for_selector("#calendar-grid .cal-num")
    return page, errors


def _day(date):
    return f'#calendar-grid button.cal-day[data-date="{date}"]'


def _no_side_scroll(page):
    return page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")


@pytest.mark.parametrize("editor", [False, True], ids=["public", "editor"])
def test_the_header_and_footer_link_to_the_calendar_before_stats(seeded, browser, editor):
    page, errors = _open(browser, seeded[0], "#/", editor)
    page.wait_for_selector("#btn-header-calendar")
    ids = page.eval_on_selector_all("#nav-actions > *", "els => els.map(e => e.id)")
    assert ids.index("btn-header-calendar") == ids.index("btn-header-stats") - 1
    link = page.locator("#btn-header-calendar")
    assert link.get_attribute("href") == "#/calendar" and link.inner_text().strip() == "Calendar"
    assert link.locator("svg").count() == 1
    assert link.get_attribute("aria-current") is None
    assert page.locator('#footer-links a[href="#/calendar"]').count() == 1
    link.click()
    page.wait_for_selector("#calendar-grid .cal-num")
    assert page.get_attribute("#btn-header-calendar", "aria-current") == "page"
    assert page.get_attribute("#btn-header-stats", "aria-current") is None
    assert errors == []


@VIEWPORTS
def test_the_month_shows_posters_on_the_nights_we_watched(seeded, browser, width):
    server, ids = seeded
    page, errors = _calendar(browser, server, width=width)
    assert page.inner_text("#calendar-title") == "November 2026"
    assert page.locator("#calendar-grid .cal-wd").count() == 7
    assert page.eval_on_selector_all("#calendar-grid .cal-num", "els => els.map(e => e.textContent)") == [str(d) for d in range(1, 31)]
    assert page.locator("#calendar-grid button.cal-day").count() == 2
    nov14 = page.locator(_day("2026-11-14"))
    assert nov14.get_attribute("aria-label") == "November 14: The 40-Year-Old Virgin, Elf"
    assert nov14.locator(".cal-more").inner_text() == "+1"
    assert nov14.locator("img.poster-img").get_attribute("src").startswith("/images/18-p342.jpg")
    nov28 = page.locator(_day("2026-11-28"))
    assert nov28.get_attribute("aria-label") == "November 28: Elf" and nov28.locator(".cal-more").count() == 0
    # Nov 1 is a Sunday in 2026, so there is no blank before the 1st; empty days are not buttons; today has a ring
    assert page.locator("#calendar-grid .cal-blank").count() == 0
    assert page.locator("#calendar-grid div.cal-day.cal-today .cal-num").inner_text() == "19"
    assert page.locator("#calendar-grid .cal-today").count() == 1
    sizes = page.eval_on_selector_all("#calendar-grid button.cal-day",
                                      "els => els.map(e => { const r = e.getBoundingClientRect(); return [r.width, r.height]; })")
    assert sizes and all(w >= 44 and h >= 44 for w, h in sizes)
    assert _no_side_scroll(page) and errors == []


def test_a_bucket_watch_is_nowhere_on_the_calendar(seeded, browser):
    page, errors = _calendar(browser, seeded[0], "#/calendar/2025-11")
    assert page.inner_text("#calendar-title") == "November 2025"
    assert page.locator("#calendar-grid button.cal-day").count() == 0
    assert page.inner_text("#calendar-status") == "Nothing watched in November 2025."
    assert errors == []


def test_numbers_and_badges_stay_readable(seeded, browser):
    page, _ = _calendar(browser, seeded[0])
    ratio = page.evaluate("""() => {
        const lum = (c) => { const [r, g, b] = c.match(/[\\d.]+/g).slice(0, 3).map(Number).map(v => { v /= 255;
            return v <= 0.03928 ? v / 12.92 : Math.pow((v + 0.055) / 1.055, 2.4); }); return 0.2126 * r + 0.7152 * g + 0.0722 * b; };
        return ['.cal-num', '.cal-more'].map(sel => { const s = getComputedStyle(document.querySelector(sel));
            const [a, b] = [lum(s.color), lum(s.backgroundColor)].sort((x, y) => y - x); return (a + 0.05) / (b + 0.05); });
    }""")
    assert all(r >= 4.5 for r in ratio), ratio


@VIEWPORTS
def test_tapping_a_day_lists_its_movies_with_working_links(seeded, browser, width):
    server, ids = seeded
    page, errors = _calendar(browser, server, width=width)
    page.click(_day("2026-11-14"))
    sheet = page.locator("#calendar-sheet")
    assert sheet.locator("#calendar-sheet-title").inner_text().strip().endswith("Saturday, November 14, 2026")
    rows = sheet.locator(".cal-row")
    # The title span, not the whole row: a poster that fails to load is replaced by a placeholder carrying the title.
    titles = sheet.locator(".cal-row .cal-row-title")
    assert [titles.nth(i).inner_text().strip() for i in range(titles.count())] == ["The 40-Year-Old Virgin (2005)", "Elf (2003)"]
    assert rows.nth(0).get_attribute("href") == f"#/t/{ids['The 40-Year-Old Virgin']}"
    assert rows.nth(1).get_attribute("href") == f"#/t/{ids['Elf']}"
    assert all(rows.nth(i).bounding_box()["height"] >= 44 for i in range(2))
    assert _no_side_scroll(page)
    page.keyboard.press("Escape")
    assert page.locator("#calendar-sheet").count() == 0
    page.click(_day("2026-11-14"))
    page.click("#calendar-sheet .cal-row >> text=Elf (2003)")
    page.wait_for_function(f"location.hash === '#/t/{ids['Elf']}' && document.querySelector('.detail-view')")
    assert page.locator("#calendar-sheet").count() == 0 and errors == []


def test_a_one_movie_day_opens_the_sheet_too(seeded, browser):
    server, ids = seeded
    page, errors = _calendar(browser, server)
    page.click(_day("2026-11-28"))
    rows = page.locator("#calendar-sheet .cal-row")
    assert rows.count() == 1 and rows.first.get_attribute("href") == f"#/t/{ids['Elf']}"
    page.click("#calendar-sheet #btn-modal-close")
    assert page.locator("#calendar-sheet").count() == 0
    assert page.evaluate("document.activeElement.dataset.date") == "2026-11-28" and errors == []


def test_a_missing_poster_falls_back_to_the_title(seeded, browser):
    page, errors = _calendar(browser, seeded[0], "#/calendar")
    cell = page.locator(_day("2026-11-28"))
    cell.locator(".poster-ph").wait_for()
    assert cell.locator(".poster-ph").inner_text() == "Elf"
    assert cell.evaluate("e => e.scrollWidth <= e.clientWidth && e.scrollHeight <= e.clientHeight")
    cell.click()
    page.locator("#calendar-sheet .cal-row .poster-ph").wait_for()
    assert errors == []


def test_the_arrows_change_the_month_and_the_hash(seeded, browser):
    page, errors = _calendar(browser, seeded[0])
    for arrow in ("#calendar-prev", "#calendar-next"):
        box = page.locator(arrow).bounding_box()
        assert box["width"] >= 44 and box["height"] >= 44
    page.click("#calendar-prev")
    page.wait_for_function("document.getElementById('calendar-title').textContent === 'October 2026'")
    assert page.evaluate("location.hash") == "#/calendar/2026-10"
    assert page.inner_text("#calendar-status") == "Nothing watched in October 2026."
    page.go_back()
    page.wait_for_function("document.getElementById('calendar-title').textContent === 'November 2026'")
    assert page.locator(_day("2026-11-14")).count() == 1 and page.inner_text("#calendar-status") == ""
    page.click("#calendar-next")
    page.click("#calendar-next")
    page.wait_for_function("document.getElementById('calendar-title').textContent === 'January 2027'")
    assert page.evaluate("location.hash") == "#/calendar/2027-01"
    assert errors == []


def test_direct_links_and_a_bad_month(seeded, browser):
    page, errors = _calendar(browser, seeded[0], "#/calendar/2026-11")
    assert page.inner_text("#calendar-title") == "November 2026"
    for bad in ("#/calendar/2026-13", "#/calendar/Nov", "#/calendar/2026-11/x"):
        other, other_errors = _open(browser, seeded[0], bad)
        other.wait_for_selector(".empty-state")
        assert other.inner_text(".empty-state h2") == "Page not found" and other.locator("#calendar-view").count() == 0
        assert other_errors == []
    feb, _ = _calendar(browser, seeded[0], "#/calendar/2028-02", width=360)
    assert feb.eval_on_selector_all("#calendar-grid .cal-num", "els => els.length") == 29
    assert feb.locator("#calendar-grid .cal-blank").count() == 2   # Feb 1, 2028 is a Tuesday
    assert errors == []


def test_each_audience_reads_its_own_endpoint(seeded, browser):
    for editor, prefix in ((False, "/api/calendar"), (True, "/admin/api/calendar")):
        page = browser.new_page(viewport={"width": 412, "height": 915})
        seen = []
        page.on("request", lambda r: seen.append(r.url) if "/calendar" in r.url and "/api/" in r.url else None)
        page.goto(f"{seeded[0]}/{'admin' if editor else ''}#/calendar/2026-11")
        page.wait_for_selector("#calendar-grid .cal-num")
        assert [u.split(seeded[0])[1] for u in seen] == [f"{prefix}?month=2026-11"]
        assert page.locator("#calendar-grid button.cal-day").count() == 2


def test_a_failed_fetch_says_so(seeded, browser):
    page, errors = _open(browser, seeded[0], "#/")
    page.route("**/api/calendar*", lambda r: r.fulfill(status=500, content_type="application/json", body='{"error": "boom"}'))
    page.click("#btn-header-calendar")
    page.wait_for_function("document.getElementById('calendar-status')?.textContent === 'boom'")
    assert page.locator("#calendar-grid .cal-day").count() == 0 and errors == []


def test_going_back_restores_the_grid_scroll(seeded, browser):
    page, errors = _open(browser, seeded[0], "#/")
    page.wait_for_selector("#grid-container .movie-card")
    page.evaluate("document.body.style.minHeight = '3000px'; window.scrollTo(0, 400); location.hash = '#/calendar'")
    page.wait_for_selector("#calendar-grid .cal-num")
    page.click("#calendar-back")
    page.wait_for_selector("#grid-container .movie-card")
    page.wait_for_function("Math.round(window.scrollY) === 400")
    assert errors == []
