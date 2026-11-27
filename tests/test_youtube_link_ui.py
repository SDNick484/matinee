"""Playwright smoke test for the YouTube link field on digital copies, at phone widths (412 px, and 360).

Same harness and skip guard as tests/test_copy_details_ui.py: skipped when playwright or Chromium is missing, and the
CI "ui-smoke" job fails if any test here is skipped. Nothing is stubbed: a real server and database, and The Dark
Knight (TMDB 155) with a 4K UHD disc and a Digital HD copy on Movies Anywhere (the ids here are made up).

Scenario: an editor pastes https://youtu.be/dQw4w9WgXcQ into the digital copy's Edit form; the database gets the id
dQw4w9WgXcQ and the form shows https://www.youtube.com/watch?v=dQw4w9WgXcQ when it is opened again.
"""
import glob
import os
import threading

import pytest

sync_api = pytest.importorskip("playwright.sync_api")
expect = sync_api.expect

ID = "dQw4w9WgXcQ"
FULL = f"https://www.youtube.com/watch?v={ID}"
BAD_LINK = "That isn't a YouTube link. Paste the address of the film's YouTube page."


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


@pytest.fixture
def dark_knight(server):
    from app import db
    from conftest import seed_title
    con = db()
    tid = seed_title(con, 155, copies=("4K UHD",), digital=("Digital HD",))
    con.execute("UPDATE copies SET service='Movies Anywhere' WHERE title_id=? AND kind='digital'", (tid,))
    con.commit()
    return tid


def copy_id(tid, kind):
    from app import db
    return db().execute("SELECT id FROM copies WHERE title_id=? AND kind=?", (tid, kind)).fetchone()[0]


def stored(cid):
    from app import db
    return db().execute("SELECT youtube_id FROM copies WHERE id=?", (cid,)).fetchone()[0]


def open_page(browser, url, width=412):
    page = browser.new_page(viewport={"width": width, "height": 915})
    errors, writes = [], []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("request", lambda r: writes.append((r.method, r.post_data)) if r.method in ("POST", "PUT") else None)
    page.goto(url)
    page.wait_for_selector(".detail-view")
    return page, errors, writes


def edit(page, cid):
    page.click(f'.copy-row[data-copy-id="{cid}"] .btn-edit-copy')
    page.wait_for_selector("#edit-copy-form")


def save_edit(page):
    page.click("#btn-edit-save")
    page.wait_for_selector("#edit-copy-form", state="detached")


def no_sideways_scroll(page, page_too=True):
    """The modal never scrolls sideways. The page itself is held to 412 px only: at 360 the header's own buttons
    already reach past the edge, with or without a form open."""
    if page_too:
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth"), "page scrolls sideways"
    assert page.evaluate("(() => { const d = document.querySelector('.modal-dialog'); "
                         "return !d || d.scrollWidth <= d.clientWidth; })()"), "the modal scrolls sideways"


def test_field_shows_for_digital_only(server, browser, dark_knight):
    page, errors, _ = open_page(browser, f"{server}/admin#/t/{dark_knight}")
    edit(page, copy_id(dark_knight, "digital"))
    assert page.get_by_label("YouTube link").is_visible()
    page.click("#btn-modal-cancel")
    edit(page, copy_id(dark_knight, "physical"))
    assert not page.locator("#edit-youtube").is_visible()          # not in the disc's form
    page.click("#btn-modal-cancel")
    page.click("#btn-add-copy")                                     # a new copy starts as a disc
    page.wait_for_selector("#copy-form")
    assert not page.locator("#add-youtube").is_visible()
    page.click('#kind-toggle [data-kind="digital"]')
    assert page.get_by_label("YouTube link").is_visible()
    page.click('#kind-toggle [data-kind="physical"]')               # and switching back hides it again
    assert not page.locator("#add-youtube").is_visible()
    assert errors == []


def test_paste_saves_and_shows_full_address(server, browser, dark_knight):
    cid = copy_id(dark_knight, "digital")
    page, errors, writes = open_page(browser, f"{server}/admin#/t/{dark_knight}")
    edit(page, cid)
    assert page.locator("#edit-youtube").input_value() == ""
    page.fill("#edit-youtube", f"  https://youtu.be/{ID}?si=abc  ")
    save_edit(page)
    assert stored(cid) == ID
    assert page.locator(f'.copy-row[data-copy-id="{cid}"]').inner_text().count("YouTube link") == 1
    edit(page, cid)
    assert page.locator("#edit-youtube").input_value() == FULL
    assert errors == []


def test_bad_paste_shows_the_sentence(server, browser, dark_knight):
    cid = copy_id(dark_knight, "digital")
    page, errors, writes = open_page(browser, f"{server}/admin#/t/{dark_knight}")
    edit(page, cid)
    page.fill("#edit-youtube", "https://vimeo.com/123456789")
    page.click("#btn-edit-save")
    page.wait_for_selector("#edit-youtube-error:not([hidden])")
    assert page.locator("#edit-youtube-error").inner_text() == BAD_LINK
    assert page.locator("#edit-copy-form").is_visible()             # still open, to be fixed
    assert stored(cid) is None
    page.fill("#edit-youtube", ID)                                   # typing again clears the sentence
    assert page.locator("#edit-youtube-error").is_hidden()
    save_edit(page)
    assert stored(cid) == ID
    assert errors == []


def test_a_taken_id_shows_the_409_sentence(server, browser, dark_knight):
    from app import db
    from conftest import seed_title
    con = db()
    inception = seed_title(con, 27205, copies=(), digital=("Digital HD",))
    con.execute("UPDATE copies SET service='Apple TV' WHERE title_id=?", (inception,))
    con.execute("UPDATE copies SET youtube_id=? WHERE title_id=?", (ID, inception))
    con.commit()
    page, errors, _ = open_page(browser, f"{server}/admin#/t/{dark_knight}")
    edit(page, copy_id(dark_knight, "digital"))
    page.fill("#edit-youtube", FULL)
    page.click("#btn-edit-save")
    page.wait_for_selector("#edit-youtube-error:not([hidden])")
    assert page.locator("#edit-youtube-error").inner_text() == "That YouTube link is already on Inception (2010)."
    assert errors == []


def test_clear_saves_empty(server, browser, dark_knight):
    cid = copy_id(dark_knight, "digital")
    from app import db
    db().execute("UPDATE copies SET youtube_id=? WHERE id=?", (ID, cid))
    db().commit()
    page, errors, writes = open_page(browser, f"{server}/admin#/t/{dark_knight}")
    edit(page, cid)
    assert page.locator("#edit-youtube").input_value() == FULL
    page.fill("#edit-youtube", "")
    save_edit(page)
    assert stored(cid) is None
    assert '"youtube_link":""' in writes[-1][1].replace(" ", "")
    assert errors == []


def test_an_unrelated_edit_keeps_the_link(server, browser, dark_knight):
    cid = copy_id(dark_knight, "digital")
    from app import db
    db().execute("UPDATE copies SET youtube_id=? WHERE id=?", (ID, cid))
    db().commit()
    page, errors, _ = open_page(browser, f"{server}/admin#/t/{dark_knight}")
    edit(page, cid)
    page.click('#edit-service-select-grid [data-srv="Apple TV"]')
    save_edit(page)
    assert stored(cid) == ID
    assert errors == []


def test_add_a_digital_copy_with_a_link(server, browser, dark_knight):
    page, errors, writes = open_page(browser, f"{server}/admin#/t/{dark_knight}")
    page.click("#btn-add-copy")
    page.wait_for_selector("#copy-form")
    page.click('#kind-toggle [data-kind="digital"]')
    page.click('#service-select-grid [data-srv="Apple TV"]')
    page.fill("#add-youtube", f"https://m.youtube.com/watch?v={ID}")
    page.click("#btn-save-copy")
    page.wait_for_selector("#copy-form", state="detached")
    from app import db
    assert db().execute("SELECT youtube_id FROM copies WHERE service='Apple TV'").fetchone()[0] == ID
    assert errors == []


def test_a_physical_add_sends_no_link(server, browser, dark_knight):
    page, errors, writes = open_page(browser, f"{server}/admin#/t/{dark_knight}")
    page.click("#btn-add-copy")
    page.wait_for_selector("#copy-form")
    page.click('#kind-toggle [data-kind="digital"]')
    page.fill("#add-youtube", ID)
    page.click('#kind-toggle [data-kind="physical"]')               # changed their mind: the hidden text isn't sent
    page.click("#btn-save-copy")
    page.wait_for_selector("#copy-form", state="detached")
    assert writes and "youtube_link" not in writes[-1][1]
    assert errors == []


@pytest.mark.parametrize("width", (412, 360))
def test_no_sideways_scroll_at_412(server, browser, dark_knight, width):
    cid = copy_id(dark_knight, "digital")
    from app import db
    db().execute("UPDATE copies SET youtube_id=? WHERE id=?", (ID, cid))
    db().commit()
    page, errors, _ = open_page(browser, f"{server}/admin#/t/{dark_knight}", width)
    wide = width >= 412
    edit(page, cid)
    no_sideways_scroll(page, wide)
    page.fill("#edit-youtube", "https://www.youtube.com/watch?feature=share&" + "x=" + "a" * 300 + f"&v={ID}x")
    page.click("#btn-edit-save")                                    # a long refused paste, with its sentence showing
    page.wait_for_selector("#edit-youtube-error:not([hidden])")
    no_sideways_scroll(page, wide)
    page.click("#btn-modal-cancel")
    page.click("#btn-add-copy")
    page.wait_for_selector("#copy-form")
    page.click('#kind-toggle [data-kind="digital"]')
    no_sideways_scroll(page, wide)
    assert errors == []


def test_second_visit(server, browser, dark_knight):
    cid = copy_id(dark_knight, "digital")
    page, errors, writes = open_page(browser, f"{server}/admin#/t/{dark_knight}")
    edit(page, cid)
    page.click("#btn-modal-cancel")
    edit(page, cid)                                                  # open, close, open again
    page.fill("#edit-youtube", ID)
    save_edit(page)
    assert [m for m, _ in writes] == ["PUT"]                         # one request per save
    assert stored(cid) == ID
    assert errors == []


# ------------------------------------------------- the Fandango at Home field (spec 2026-11-27-fandango-play-design §5)
# The Addams Family's page is https://athome.fandango.com/content/browse/details/The-Addams-Family/15429; the form
# shows a stored id back as .../details/title/15429. Here the number sits on The Dark Knight's digital copy.
FD_ID = "15429"
FD_PAGE = f"https://athome.fandango.com/content/browse/details/The-Addams-Family/{FD_ID}"
FD_FULL = f"https://athome.fandango.com/content/browse/details/title/{FD_ID}"
FD_BAD = "That isn't a Fandango at Home link. Paste the address of the film's Fandango at Home page."


def stored_both(cid):
    from app import db
    row = db().execute("SELECT fandango_id, youtube_id FROM copies WHERE id=?", (cid,)).fetchone()
    return row["fandango_id"], row["youtube_id"]


def test_two_fields_fandango_first_for_digital_only(server, browser, dark_knight):
    page, errors, _ = open_page(browser, f"{server}/admin#/t/{dark_knight}")
    edit(page, copy_id(dark_knight, "digital"))
    fandango, youtube = page.get_by_label("Fandango at Home link"), page.get_by_label("YouTube link")
    expect(fandango).to_be_visible()
    expect(youtube).to_be_visible()
    assert fandango.get_attribute("placeholder") == "Paste the film's Fandango at Home address"
    assert fandango.bounding_box()["y"] < youtube.bounding_box()["y"]        # Fandango at Home first
    page.click("#btn-modal-cancel")
    edit(page, copy_id(dark_knight, "physical"))
    expect(page.locator("#edit-fandango")).to_be_hidden()
    page.click("#btn-modal-cancel")
    page.click("#btn-add-copy")
    page.wait_for_selector("#copy-form")
    expect(page.locator("#add-fandango")).to_be_hidden()
    page.click('#kind-toggle [data-kind="digital"]')
    expect(page.locator("#add-fandango")).to_be_visible()
    assert errors == []


def test_paste_both_saves_and_shows_both_back(server, browser, dark_knight):
    cid = copy_id(dark_knight, "digital")
    page, errors, writes = open_page(browser, f"{server}/admin#/t/{dark_knight}")
    edit(page, cid)
    expect(page.locator("#edit-fandango")).to_have_value("")
    page.fill("#edit-fandango", f"  {FD_PAGE}?cmp=share  ")
    page.fill("#edit-youtube", f"https://youtu.be/{ID}")
    save_edit(page)
    assert stored_both(cid) == (FD_ID, ID)
    row = page.locator(f'.copy-row[data-copy-id="{cid}"]')
    expect(row).to_contain_text("Fandango at Home link")
    expect(row).to_contain_text("YouTube link")
    edit(page, cid)
    expect(page.locator("#edit-fandango")).to_have_value(FD_FULL)
    expect(page.locator("#edit-youtube")).to_have_value(FULL)
    page.fill("#edit-fandango", "")                                    # clearing one keeps the other
    save_edit(page)
    assert stored_both(cid) == (None, ID)
    assert '"fandango_link":""' in writes[-1][1].replace(" ", "")
    assert errors == []


def test_add_a_digital_copy_with_a_fandango_link(server, browser, dark_knight):
    page, errors, writes = open_page(browser, f"{server}/admin#/t/{dark_knight}")
    page.click("#btn-add-copy")
    page.wait_for_selector("#copy-form")
    page.click('#kind-toggle [data-kind="digital"]')
    page.click('#service-select-grid [data-srv="Fandango at Home"]')
    page.fill("#add-fandango", FD_ID)                                  # a bare number is the id
    page.click("#btn-save-copy")
    page.wait_for_selector("#copy-form", state="detached")
    from app import db
    assert db().execute("SELECT fandango_id, youtube_id FROM copies WHERE service='Fandango at Home'").fetchone()[:] \
        == (FD_ID, None)
    assert errors == []


def test_fandango_errors_show_beside_their_own_field(server, browser, dark_knight):
    from app import db
    from conftest import seed_title
    con = db()
    inception = seed_title(con, 27205, copies=(), digital=("Digital HD",))
    con.execute("UPDATE copies SET service='Apple TV', fandango_id=? WHERE title_id=?", (FD_ID, inception))
    con.commit()
    cid = copy_id(dark_knight, "digital")
    page, errors, _ = open_page(browser, f"{server}/admin#/t/{dark_knight}")
    edit(page, cid)
    page.fill("#edit-fandango", "https://www.fandango.com/the-addams-family-1991/15429")
    page.click("#btn-edit-save")
    expect(page.locator("#edit-fandango-error")).to_have_text(FD_BAD)
    expect(page.locator("#edit-youtube-error")).to_be_hidden()
    expect(page.locator("#edit-copy-form")).to_be_visible()
    page.fill("#edit-fandango", FD_PAGE)                                # typing again clears the sentence
    expect(page.locator("#edit-fandango-error")).to_be_hidden()
    page.click("#btn-edit-save")                                        # the number is on Inception already
    expect(page.locator("#edit-fandango-error")).to_have_text("That Fandango at Home link is already on Inception (2010).")
    page.fill("#edit-fandango", "")
    page.fill("#edit-youtube", "https://vimeo.com/123456789")           # and a YouTube error goes beside YouTube
    page.click("#btn-edit-save")
    expect(page.locator("#edit-youtube-error")).to_have_text(BAD_LINK)
    expect(page.locator("#edit-fandango-error")).to_be_hidden()
    assert stored_both(cid) == (None, None)
    assert errors == []


@pytest.mark.parametrize("width", (412, 360))
def test_both_fields_no_sideways_scroll(server, browser, dark_knight, width):
    cid = copy_id(dark_knight, "digital")
    from app import db
    db().execute("UPDATE copies SET youtube_id=?, fandango_id=? WHERE id=?", (ID, FD_ID, cid))
    db().commit()
    page, errors, _ = open_page(browser, f"{server}/admin#/t/{dark_knight}", width)
    wide = width >= 412
    edit(page, cid)
    no_sideways_scroll(page, wide)
    page.fill("#edit-fandango", "https://athome.fandango.com/content/browse/details/" + "x" * 300 + "/1234567890123")
    page.click("#btn-edit-save")
    expect(page.locator("#edit-fandango-error")).to_be_visible()
    no_sideways_scroll(page, wide)
    assert errors == []
