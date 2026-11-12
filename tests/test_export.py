"""GET /admin/api/export.csv: one row per owned copy, spreadsheet-safe.

Scenario: The Dark Knight (TMDB 155) has a 4K UHD disc on Family Room > Media Cabinet > Shelf 1 and a
Digital HD copy on Movies Anywhere, rating 9.5, one watch on 2026-11-07 (note "girls loved it").
Amelie (TMDB 194) has one Blu-ray and no rating or watches.
"""
import csv
import datetime as dt
import io

import pytest

import app as matinee
from app import app, db, init_db
from conftest import seed_title

API_KEY = "test-write-key-0123456789"
READ_KEY = "test-read-key-9876543210"
NOTE = "girls loved it"
BOM = b"\xef\xbb\xbf"
HEADER = ["title", "year", "tmdb_id", "imdb_id", "certification", "runtime", "kind", "format", "service",
          "location_path", "user_rating", "watch_count", "last_watched", "added_on"]
DETAILS = ["edition", "code_status", "purchased_on", "price"]   # appended in schema v10; nothing before them moved



@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.MATINEE_API_KEY", API_KEY)
    monkeypatch.setattr("app.MATINEE_READ_KEY", READ_KEY, raising=False)
    init_db()
    with app.test_client() as c:
        yield c


def bearer(key):
    return {"Authorization": f"Bearer {key}"}


def rows_of(res):
    """Parse a response body the way a spreadsheet would: strip the BOM, decode, csv.reader."""
    text = res.data.decode("utf-8-sig")
    return list(csv.reader(io.StringIO(text, newline="")))


@pytest.fixture
def dark_knight(client):
    con = db()
    con.execute("INSERT INTO locations (id, parent_id, name) VALUES (1, NULL, 'Family Room'), "
                "(2, 1, 'Media Cabinet'), (3, 2, 'Shelf 1')")
    tid = seed_title(con, 155, copies=("4K UHD",), digital=("Digital HD",), watched=("2026-11-07",), rating=9.5)
    con.execute("UPDATE copies SET location_id=3, created_at='2026-11-02 18:04:11' "
                "WHERE title_id=? AND kind='physical'", (tid,))
    con.execute("UPDATE copies SET service='Movies Anywhere', created_at='2026-11-03 09:00:00' "
                "WHERE title_id=? AND kind='digital'", (tid,))
    con.execute("UPDATE watches SET notes=? WHERE title_id=?", (NOTE, tid))
    con.commit()
    return tid


# ------------------------------------------------------------------------------------- scenario

def test_dark_knight_gives_one_row_per_copy(client, dark_knight):
    res = client.get("/admin/api/export.csv", headers=bearer(API_KEY))
    assert res.status_code == 200
    rows = rows_of(res)
    assert rows[0] == HEADER + DETAILS
    assert rows[1:] == [
        ["The Dark Knight", "2008", "155", "tt0468569", "PG-13", "152", "physical", "4K UHD", "",
         "Family Room › Media Cabinet › Shelf 1", "9.5", "1", "2026-11-07", "2026-11-02", "", "none", "", ""],
        ["The Dark Knight", "2008", "155", "tt0468569", "PG-13", "152", "digital", "Digital HD",
         "Movies Anywhere", "", "9.5", "1", "2026-11-07", "2026-11-03", "", "none", "", ""],
    ]


def test_amelie_accent_round_trips_with_bom(client):
    seed_title(db(), 194)
    res = client.get("/admin/api/export.csv", headers=bearer(API_KEY))
    assert res.data.startswith(BOM)
    assert not res.data[len(BOM):].startswith(BOM)
    rows = rows_of(res)
    assert rows[1][0] == "Amélie"
    assert rows[1][2:4] == ["194", "tt0211915"]
    # unrated, never watched: empty cells and a zero count, not "None"
    assert rows[1][10:13] == ["", "0", ""]
    assert b"None" not in res.data


def test_watch_notes_are_not_exported(client, dark_knight):
    res = client.get("/admin/api/export.csv", headers=bearer(API_KEY))
    assert NOTE.encode() not in res.data


# ------------------------------------------------------------------------------------- copy details

def test_dark_knight_steelbook_row_ends_with_its_details(client, dark_knight):
    db().execute("UPDATE copies SET edition='Steelbook', code_status='unredeemed', purchased_on='2026-10-31', "
                 "price_cents=2499 WHERE title_id=? AND kind='physical'", (dark_knight,))
    db().commit()
    rows = rows_of(client.get("/admin/api/export.csv", headers=bearer(API_KEY)))
    assert rows[1][-4:] == ["Steelbook", "unredeemed", "2026-10-31", "24.99"]
    assert rows[1][:14] == ["The Dark Knight", "2008", "155", "tt0468569", "PG-13", "152", "physical", "4K UHD", "",
                            "Family Room › Media Cabinet › Shelf 1", "9.5", "1", "2026-11-07", "2026-11-02"]
    assert rows[2][-4:] == ["", "none", "", ""]   # the digital copy: no edition, no code, no price


@pytest.mark.parametrize("cents, text", [(0, "0.00"), (5, "0.05"), (100, "1.00"), (1499, "14.99"),
                                         (2499, "24.99"), (10_000_000, "100000.00")])
def test_price_is_dollars_with_two_decimals(client, cents, text):
    con = db()
    seed_title(con, 155)
    con.execute("UPDATE copies SET price_cents=?", (cents,))
    con.commit()
    assert rows_of(client.get("/admin/api/export.csv", headers=bearer(API_KEY)))[1][-1] == text


def test_an_edition_that_looks_like_a_formula_is_quoted(client):
    con = db()
    seed_title(con, 155)
    con.execute("UPDATE copies SET edition='=HYPERLINK(\"http://x\",\"y\")'")
    con.commit()
    assert rows_of(client.get("/admin/api/export.csv", headers=bearer(API_KEY)))[1][-4] == \
        "'=" + 'HYPERLINK("http://x","y")'


def test_old_columns_keep_their_positions():
    assert matinee.EXPORT_COLUMNS[:14] == tuple(HEADER) and matinee.EXPORT_COLUMNS[14:] == tuple(DETAILS)


# ------------------------------------------------------------------------------------- auth

def test_anonymous_gets_401(client, dark_knight):
    res = client.get("/admin/api/export.csv")
    assert res.status_code == 401
    assert res.get_json() == {"error": "Sign in to edit"}


def test_unknown_key_gets_401(client, dark_knight):
    assert client.get("/admin/api/export.csv", headers=bearer("nope")).status_code == 401


@pytest.mark.parametrize("key", [API_KEY, READ_KEY])
def test_write_key_and_read_key_may_export(client, dark_knight, key):
    res = client.get("/admin/api/export.csv", headers=bearer(key))
    assert res.status_code == 200
    assert len(rows_of(res)) == 3


def test_lan_identity_may_export(client, dark_knight, monkeypatch):
    monkeypatch.setattr("app.LAN_EDIT", True)
    assert client.get("/admin/api/export.csv").status_code == 200


def test_cross_site_browser_request_refused(client, dark_knight, monkeypatch):
    monkeypatch.setattr("app.LAN_EDIT", True)
    res = client.get("/admin/api/export.csv", headers={"Origin": "https://evil.example.com"})
    assert res.status_code == 403
    res = client.get("/admin/api/export.csv", headers={"Sec-Fetch-Site": "cross-site"})
    assert res.status_code == 403


def test_export_is_get_only(client, dark_knight):
    assert client.post("/admin/api/export.csv", headers=bearer(API_KEY)).status_code == 405


# ------------------------------------------------------------------------------------- headers

def test_response_headers(client, dark_knight):
    res = client.get("/admin/api/export.csv", headers=bearer(API_KEY))
    assert res.headers["Content-Type"] == "text/csv; charset=utf-8"
    assert res.headers["Cache-Control"] == "no-store"
    today = dt.date.today().isoformat()
    assert res.headers["Content-Disposition"] == f'attachment; filename="matinee-{today}.csv"'


def test_export_csv_filename_uses_the_given_day(client):
    body, name = matinee.export_csv(db(), today=dt.date(2026, 11, 11))
    assert name == "matinee-2026-11-11.csv"
    assert body.startswith(BOM + b"title,year,tmdb_id")


# ------------------------------------------------------------------------------------- format

def test_crlf_line_endings(client, dark_knight):
    body = client.get("/admin/api/export.csv", headers=bearer(API_KEY)).data
    assert body.count(b"\r\n") == 3
    assert body.endswith(b"\r\n")
    assert b"\n" not in body.replace(b"\r\n", b"")


def test_rfc4180_quoting_of_commas_and_quotes(client):
    con = db()
    tid = seed_title(con, 155)
    con.execute("UPDATE titles SET title='Batman, \"The Dark\" Knight' WHERE id=?", (tid,))
    con.commit()
    body = client.get("/admin/api/export.csv", headers=bearer(API_KEY)).data.decode("utf-8-sig")
    assert body.splitlines()[1].startswith('"Batman, ""The Dark"" Knight",2008,')
    assert rows_of(client.get("/admin/api/export.csv", headers=bearer(API_KEY)))[1][0] == 'Batman, "The Dark" Knight'


@pytest.mark.parametrize("lead", ["=", "+", "-", "@", "\t", "\r"])
def test_formula_prefixes_are_neutralised(client, lead):
    con = db()
    tid = seed_title(con, 155)
    con.execute("UPDATE titles SET title=? WHERE id=?", (f'{lead}HYPERLINK("http://x","y")', tid))
    con.execute("UPDATE copies SET service=? WHERE title_id=?", (f"{lead}cmd", tid))
    con.commit()
    rows = rows_of(client.get("/admin/api/export.csv", headers=bearer(API_KEY)))
    assert rows[1][0] == f"'{lead}" + 'HYPERLINK("http://x","y")'
    assert rows[1][8] == f"'{lead}cmd"


def test_numbers_and_ordinary_text_are_not_prefixed(client, dark_knight):
    rows = rows_of(client.get("/admin/api/export.csv", headers=bearer(API_KEY)))
    for row in rows[1:]:
        assert not any(cell.startswith("'") for cell in row)


def test_negative_number_cells_stay_numbers(client):
    con = db()
    tid = seed_title(con, 155)
    con.execute("UPDATE titles SET runtime=-5 WHERE id=?", (tid,))
    con.commit()
    assert rows_of(client.get("/admin/api/export.csv", headers=bearer(API_KEY)))[1][5] == "-5"


# ------------------------------------------------------------------------------------- edge cases

def test_empty_collection_is_header_only(client):
    res = client.get("/admin/api/export.csv", headers=bearer(API_KEY))
    assert res.status_code == 200
    assert rows_of(res) == [HEADER + DETAILS]


def test_title_with_no_copies_is_absent(client, dark_knight):
    con = db()
    con.execute("DELETE FROM copies WHERE title_id=?", (dark_knight,))
    con.commit()
    assert rows_of(client.get("/admin/api/export.csv", headers=bearer(API_KEY))) == [HEADER + DETAILS]


def test_certification_override_wins(client):
    con = db()
    tid = seed_title(con, 155)
    con.execute("UPDATE titles SET certification_override='PG' WHERE id=?", (tid,))
    con.commit()
    assert rows_of(client.get("/admin/api/export.csv", headers=bearer(API_KEY)))[1][4] == "PG"


def test_rows_are_ordered_by_title_then_copy(client, dark_knight):
    seed_title(db(), 194)
    seed_title(db(), 150540, copies=(), digital=("Digital HD",))
    first = rows_of(client.get("/admin/api/export.csv", headers=bearer(API_KEY)))
    second = rows_of(client.get("/admin/api/export.csv", headers=bearer(API_KEY)))
    assert first == second
    # sorted by sort_title like the grid, so the leading "The" is ignored: Amélie, Dark Knight, Inside Out
    assert [r[0] for r in first[1:]] == ["Amélie", "The Dark Knight", "The Dark Knight", "Inside Out"]
    assert [r[6] for r in first[1:] if r[0] == "The Dark Knight"] == ["physical", "digital"]


def test_location_without_a_shelf_is_empty(client):
    seed_title(db(), 155)
    assert rows_of(client.get("/admin/api/export.csv", headers=bearer(API_KEY)))[1][9] == ""
