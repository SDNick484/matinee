"""A Fandango at Home link on a digital copy over the API: `fandango_link` behaves exactly as `youtube_link` does,
independent of it, editor-only (spec 2026-11-27-fandango-play-design §3).

Scenario: The Addams Family (1991, TMDB 2907) has a Digital HD copy on Fandango at Home, whose page is
https://athome.fandango.com/content/browse/details/The-Addams-Family/15429, and a DVD. Inception (TMDB 27205) has a
Digital HD copy on Apple TV. The YouTube ids, and every Fandango number but 15429, are made up.
"""
import json

import pytest

from app import app, db, init_db
from conftest import seed_title
from export import EXPORT_COLUMNS

API_KEY = "test-write-key-0123456789"
READ_KEY = "test-read-key-9876543210"
ID = "15429"
PAGE = f"https://athome.fandango.com/content/browse/details/The-Addams-Family/{ID}"
FULL = f"https://athome.fandango.com/content/browse/details/title/{ID}"
YT = "dQw4w9WgXcQ"
BAD_LINK = "That isn't a Fandango at Home link. Paste the address of the film's Fandango at Home page."


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


def bearer(key=API_KEY):
    return {"Authorization": f"Bearer {key}"}


@pytest.fixture
def addams(client):
    """The Addams Family: a DVD and a Digital HD copy on Fandango at Home."""
    con = db()
    tid = con.execute("""INSERT INTO titles (tmdb_id, imdb_id, title, sort_title, search_text, year, fetched_at)
                         VALUES (2907, 'tt0101272', 'The Addams Family', 'addams family', 'the addams family', 1991,
                                 '2026-01-01')""").lastrowid
    con.execute("INSERT INTO copies (title_id, kind, format, created_at) VALUES (?, 'physical', 'DVD', "
                "'2026-01-01 12:00:00')", (tid,))
    con.execute("INSERT INTO copies (title_id, kind, format, service, created_at) VALUES (?, 'digital', 'Digital HD', "
                "'Fandango at Home', '2026-01-02 12:00:00')", (tid,))
    con.commit()
    return tid


def copy_ids(client, tid):
    copies = client.get(f"/admin/api/titles/{tid}", headers=bearer()).get_json()["copies"]
    return {c["kind"]: c["id"] for c in copies}


def put(client, copy_id, **body):
    return client.put(f"/admin/api/copies/{copy_id}", json=body, headers=bearer())


def digital(detail):
    return next(c for c in detail["copies"] if c["kind"] == "digital")


def stored(cid):
    row = db().execute("SELECT youtube_id, fandango_id FROM copies WHERE id=?", (cid,)).fetchone()
    return row["youtube_id"], row["fandango_id"]


@pytest.mark.parametrize("pasted", [PAGE, FULL, ID, f"  {PAGE}?cmp=share  ",
                                    f"https://www.vudu.com/content/movies/details/The-Addams-Family/{ID}"])
def test_put_link_stores_id_and_detail_shows_it(client, addams, pasted):
    cid = copy_ids(client, addams)["digital"]
    res = put(client, cid, fandango_link=pasted)
    assert res.status_code == 200
    assert digital(res.get_json())["fandango_id"] == ID
    assert stored(cid) == (None, ID)
    again = client.get(f"/admin/api/titles/{addams}", headers=bearer()).get_json()
    assert digital(again)["fandango_id"] == ID
    assert [c["fandango_id"] for c in again["copies"] if c["kind"] == "physical"] == [None]


def test_both_links_on_one_copy_are_independent(client, addams):
    cid = copy_ids(client, addams)["digital"]
    assert put(client, cid, fandango_link=PAGE, youtube_link=YT).status_code == 200
    assert stored(cid) == (YT, ID)
    assert put(client, cid, youtube_link="").status_code == 200          # clearing one leaves the other
    assert stored(cid) == (None, ID)
    assert put(client, cid, youtube_link=YT).status_code == 200          # and setting one leaves the other
    assert stored(cid) == (YT, ID)
    assert put(client, cid, fandango_link=None).status_code == 200
    assert stored(cid) == (YT, None)


def test_put_without_key_keeps_it(client, addams):
    cid = copy_ids(client, addams)["digital"]
    put(client, cid, fandango_link=ID)
    res = put(client, cid, service="Apple TV")
    assert res.status_code == 200
    assert digital(res.get_json())["fandango_id"] == ID and digital(res.get_json())["service"] == "Apple TV"


@pytest.mark.parametrize("cleared", [None, "", "   "])
def test_put_empty_or_null_clears_it(client, addams, cleared):
    cid = copy_ids(client, addams)["digital"]
    put(client, cid, fandango_link=ID)
    res = put(client, cid, fandango_link=cleared)
    assert res.status_code == 200
    assert digital(res.get_json())["fandango_id"] is None and stored(cid) == (None, None)


def test_post_copy_with_link(client, addams):
    res = client.post("/admin/api/copies", headers=bearer(), json={
        "title_id": addams, "kind": "digital", "format": "Digital 4K", "service": "Apple TV",
        "fandango_link": PAGE, "youtube_link": f"https://youtu.be/{YT}"})
    assert res.status_code == 201
    new = next(c for c in res.get_json()["copies"] if c["id"] == res.get_json()["copy_id"])
    assert (new["fandango_id"], new["youtube_id"]) == (ID, YT)


def test_post_copy_without_link_has_null(client, addams):
    res = client.post("/admin/api/copies", headers=bearer(), json={
        "title_id": addams, "kind": "digital", "format": "Digital 4K", "service": "Apple TV"})
    assert res.status_code == 201
    assert all(c["fandango_id"] is None for c in res.get_json()["copies"])


@pytest.mark.parametrize("pasted", [
    "https://www.fandango.com/the-addams-family-1991/15429", "1234567890123",
    "https://athome.fandango.com/content/browse/details/The-Addams-Family", f"https://youtu.be/{YT}", YT,
    12345, ["15429"], {"id": ID}, True])
def test_bad_link_is_400(client, addams, pasted):
    cid = copy_ids(client, addams)["digital"]
    res = put(client, cid, fandango_link=pasted)
    assert res.status_code == 400 and res.get_json()["error"] == BAD_LINK
    assert stored(cid) == (None, None)
    res = client.post("/admin/api/copies", headers=bearer(), json={
        "title_id": addams, "kind": "digital", "format": "Digital 4K", "service": "Apple TV", "fandango_link": pasted})
    assert res.status_code == 400 and res.get_json()["error"] == BAD_LINK
    assert db().execute("SELECT count(*) FROM copies").fetchone()[0] == 2


def test_link_on_physical_copy_is_400(client, addams):
    disc = copy_ids(client, addams)["physical"]
    res = put(client, disc, fandango_link=ID)
    assert res.status_code == 400
    assert res.get_json()["error"] == ("A Fandango at Home link belongs on a digital copy. "
                                       "A physical copy can't have one.")
    res = client.post("/admin/api/copies", headers=bearer(), json={
        "title_id": addams, "kind": "physical", "format": "Blu-ray", "fandango_link": PAGE})
    assert res.status_code == 400
    assert put(client, disc, fandango_link="").status_code == 200    # an empty link on a disc is just "none"
    assert put(client, disc, fandango_link=None).status_code == 200


def test_digital_with_id_turned_physical_is_400(client, addams):
    cid = copy_ids(client, addams)["digital"]
    put(client, cid, fandango_link=ID)
    res = put(client, cid, kind="physical", format="Blu-ray", service=None)
    assert res.status_code == 400
    assert res.get_json()["error"] == "This copy has a Fandango at Home link. Clear it before making the copy physical."
    row = db().execute("SELECT kind, fandango_id FROM copies WHERE id=?", (cid,)).fetchone()
    assert (row["kind"], row["fandango_id"]) == ("digital", ID)
    assert put(client, cid, kind="physical", format="Blu-ray", service=None, fandango_link="").status_code == 200


def test_same_id_on_second_copy_is_409(client, addams):
    cid = copy_ids(client, addams)["digital"]
    assert put(client, cid, fandango_link=ID).status_code == 200
    inception = seed_title(db(), 27205, copies=(), digital=("Digital HD",))
    other = db().execute("SELECT id FROM copies WHERE title_id=?", (inception,)).fetchone()[0]
    db().execute("UPDATE copies SET service='Apple TV' WHERE id=?", (other,))
    db().commit()
    res = put(client, other, fandango_link=PAGE)
    assert res.status_code == 409
    assert res.get_json()["error"] == "That Fandango at Home link is already on The Addams Family (1991)."
    assert stored(other) == (None, None)
    res = client.post("/admin/api/copies", headers=bearer(), json={
        "tmdb_id": 27205, "kind": "digital", "format": "Digital 4K", "service": "Apple TV", "fandango_link": ID})
    assert res.status_code == 409
    assert db().execute("SELECT count(*) FROM copies WHERE title_id=?", (inception,)).fetchone()[0] == 1
    assert put(client, cid, fandango_link=ID).status_code == 200      # the same id on the same copy again is fine


def test_a_youtube_id_on_another_copy_is_not_a_fandango_clash(client, addams):
    """The two columns are separate namespaces: a YouTube id never collides with a Fandango number."""
    cid = copy_ids(client, addams)["digital"]
    assert put(client, cid, youtube_link=YT).status_code == 200
    inception = seed_title(db(), 27205, copies=(), digital=("Digital HD",))
    other = db().execute("SELECT id FROM copies WHERE title_id=?", (inception,)).fetchone()[0]
    db().execute("UPDATE copies SET service='Apple TV' WHERE id=?", (other,))
    db().commit()
    assert put(client, other, fandango_link="27205").status_code == 200


def test_public_api_never_carries_it(client, addams):
    put(client, copy_ids(client, addams)["digital"], fandango_link=ID)
    for path in ("/api/titles", f"/api/titles/{addams}", "/api/copies", "/api/titles?q=addams"):
        res = client.get(path)                                       # signed out, PUBLIC_VIEW on
        assert res.status_code == 200, path
        text = json.dumps(res.get_json()).lower()
        assert "fandango_id" not in text and "fandango_link" not in text and f'"{ID}"' not in text, path


def test_owned_answer_carries_it(client, addams):
    put(client, copy_ids(client, addams)["digital"], fandango_link=ID)
    copies = {c["kind"]: c for c in client.get("/admin/api/tmdb/2907/owned", headers=bearer()).get_json()["copies"]}
    assert copies["digital"]["fandango_id"] == ID and copies["physical"]["fandango_id"] is None


def test_read_key_reads_it_and_cannot_write_it(client, addams):
    cid = copy_ids(client, addams)["digital"]
    put(client, cid, fandango_link=ID)
    res = client.get(f"/admin/api/titles/{addams}", headers=bearer(READ_KEY))
    assert res.status_code == 200 and digital(res.get_json())["fandango_id"] == ID
    assert client.put(f"/admin/api/copies/{cid}", json={"fandango_link": ""},
                      headers=bearer(READ_KEY)).status_code in (401, 403)
    assert client.put(f"/admin/api/copies/{cid}", json={"fandango_link": ""}).status_code in (401, 403)
    assert stored(cid) == (None, ID)


def test_export_ends_with_the_fandango_link(client, addams):
    put(client, copy_ids(client, addams)["digital"], fandango_link=PAGE)
    res = client.get("/admin/api/export.csv", headers=bearer())
    lines = res.data.decode("utf-8-sig").splitlines()
    assert lines[0].split(",")[-2:] == ["youtube_link", "fandango_link"] and EXPORT_COLUMNS[-1] == "fandango_link"
    assert [line.split(",")[-1] for line in lines[1:]] == ["", FULL]     # the DVD has none
    res = client.get("/admin/api/export.csv?shape=title", headers=bearer())
    assert b"fandango_link" not in res.data and FULL.encode() not in res.data   # the title shape is unchanged
