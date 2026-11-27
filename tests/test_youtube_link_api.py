"""A YouTube link on a digital copy over the API: parsed from what people paste, stored as an 11-character id,
editor-only (spec 2026-11-26-play-on-tv-design §4).

Scenario: The Dark Knight (TMDB 155) has a 4K UHD disc (copy 1) and a Digital HD copy on Movies Anywhere (copy 2);
Inception (TMDB 27205) has a Digital HD copy on Apple TV. The ids are made up.
"""
import json

import pytest

import app as matinee
from app import app, db, init_db
from conftest import seed_title
from youtube import parse_youtube_link

API_KEY = "test-write-key-0123456789"
READ_KEY = "test-read-key-9876543210"
ID = "dQw4w9WgXcQ"
OTHER = "-eYxeRJCYMY"
BAD_LINK = "That isn't a YouTube link. Paste the address of the film's YouTube page."


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
def dark_knight(client):
    """The Dark Knight: a plain 4K UHD disc (copy 1) and a Digital HD copy on Movies Anywhere (copy 2)."""
    con = db()
    tid = seed_title(con, 155, copies=("4K UHD",), digital=("Digital HD",))
    con.execute("UPDATE copies SET service='Movies Anywhere' WHERE title_id=? AND kind='digital'", (tid,))
    con.commit()
    return tid


def copy_ids(client, tid):
    copies = client.get(f"/admin/api/titles/{tid}", headers=bearer()).get_json()["copies"]
    return {c["kind"]: c["id"] for c in copies}


def put(client, copy_id, **body):
    return client.put(f"/admin/api/copies/{copy_id}", json=body, headers=bearer())


def digital(detail):
    return next(c for c in detail["copies"] if c["kind"] == "digital")


@pytest.mark.parametrize("pasted, expected", [
    (ID, ID),
    (OTHER, OTHER),
    (f"https://www.youtube.com/watch?v={ID}", ID),
    (f"https://youtu.be/{ID}?si=abc", ID),
    (f"https://youtu.be/{OTHER}", OTHER),
    (f"https://www.youtube.com/watch?v={ID}&t=30s", ID),
    (f"https://www.youtube.com/watch?feature=share&v={ID}", ID),
    (f"https://m.youtube.com/watch?v={ID}", ID),
    (f"http://youtube.com/watch?v={ID}", ID),
    (f"https://www.youtu.be/{ID}", ID),
    (f"  https://youtu.be/{ID}  ", ID),
    (f"https://youtu.be/{ID}\n", ID),
    (f"\t{ID}\r\n", ID),
    (f"youtu.be/{ID}", ID),
    (None, None), ("", None), ("   ", None),
])
def test_parse_youtube_link_accepts(pasted, expected):
    assert parse_youtube_link(pasted) == expected


@pytest.mark.parametrize("pasted", [
    "https://vimeo.com/123456789",
    "dQw4w9WgXc", "dQw4w9WgXcQa",                                    # 10 and 12 characters
    "https://youtu.be/dQw4w9WgXc", "https://www.youtube.com/watch?v=dQw4w9WgXcQa",
    "https://www.youtube.com/playlist?list=PLabcdefghijk",           # a playlist address with no v
    "https://www.youtube.com/watch?list=PLabcdefghijk",
    "https://www.youtube.com/watch?v=",
    f"https://www.youtube.com/channel/{ID}",
    f"https://evil.example.com/watch?v={ID}", f"https://notyoutube.com/watch?v={ID}",
    f"https://youtube.com.evil.example.com/watch?v={ID}",
    "https://youtu.be/", "https://youtu.be",
    "not a link at all", 12345678901, ["dQw4w9WgXcQ"], True,
])
def test_parse_youtube_link_rejects(pasted):
    with pytest.raises(ValueError):
        parse_youtube_link(pasted)


def test_put_link_stores_id_and_detail_shows_it(client, dark_knight):
    cid = copy_ids(client, dark_knight)["digital"]
    res = put(client, cid, youtube_link=f"https://www.youtube.com/watch?v={ID}")
    assert res.status_code == 200
    assert digital(res.get_json())["youtube_id"] == ID
    assert db().execute("SELECT youtube_id FROM copies WHERE id=?", (cid,)).fetchone()[0] == ID
    again = client.get(f"/admin/api/titles/{dark_knight}", headers=bearer()).get_json()
    assert digital(again)["youtube_id"] == ID
    assert [c.get("youtube_id") for c in again["copies"] if c["kind"] == "physical"] == [None]


def test_put_a_pasted_share_link_stores_the_bare_id(client, dark_knight):
    cid = copy_ids(client, dark_knight)["digital"]
    assert put(client, cid, youtube_link=f"  https://youtu.be/{OTHER}?si=abc\n").status_code == 200
    assert db().execute("SELECT youtube_id FROM copies WHERE id=?", (cid,)).fetchone()[0] == OTHER


def test_put_without_key_keeps_it(client, dark_knight):
    cid = copy_ids(client, dark_knight)["digital"]
    put(client, cid, youtube_link=ID)
    res = put(client, cid, service="Apple TV")
    assert res.status_code == 200
    assert digital(res.get_json())["youtube_id"] == ID
    assert digital(res.get_json())["service"] == "Apple TV"


@pytest.mark.parametrize("cleared", [None, "", "   "])
def test_put_empty_or_null_clears_it(client, dark_knight, cleared):
    cid = copy_ids(client, dark_knight)["digital"]
    put(client, cid, youtube_link=ID)
    res = put(client, cid, youtube_link=cleared)
    assert res.status_code == 200
    assert digital(res.get_json())["youtube_id"] is None
    assert db().execute("SELECT youtube_id FROM copies WHERE id=?", (cid,)).fetchone()[0] is None


def test_post_copy_with_link(client, dark_knight):
    res = client.post("/admin/api/copies", headers=bearer(), json={
        "title_id": dark_knight, "kind": "digital", "format": "Digital 4K", "service": "Apple TV",
        "youtube_link": f"https://youtu.be/{ID}"})
    assert res.status_code == 201
    new = next(c for c in res.get_json()["copies"] if c["id"] == res.get_json()["copy_id"])
    assert new["youtube_id"] == ID


def test_post_copy_without_link_has_null(client, dark_knight):
    res = client.post("/admin/api/copies", headers=bearer(), json={
        "title_id": dark_knight, "kind": "digital", "format": "Digital 4K", "service": "Apple TV"})
    assert res.status_code == 201
    assert all(c["youtube_id"] is None for c in res.get_json()["copies"])


@pytest.mark.parametrize("pasted", ["https://vimeo.com/123", "dQw4w9WgXc", "https://www.youtube.com/playlist?list=PLx"])
def test_bad_link_is_400(client, dark_knight, pasted):
    cid = copy_ids(client, dark_knight)["digital"]
    res = put(client, cid, youtube_link=pasted)
    assert res.status_code == 400 and res.get_json()["error"] == BAD_LINK
    assert db().execute("SELECT youtube_id FROM copies WHERE id=?", (cid,)).fetchone()[0] is None
    res = client.post("/admin/api/copies", headers=bearer(), json={
        "title_id": dark_knight, "kind": "digital", "format": "Digital 4K", "service": "Apple TV", "youtube_link": pasted})
    assert res.status_code == 400 and res.get_json()["error"] == BAD_LINK
    assert db().execute("SELECT count(*) FROM copies").fetchone()[0] == 2


def test_bad_link_value_types_are_400(client, dark_knight):
    cid = copy_ids(client, dark_knight)["digital"]
    for value in (12345678901, ["x"], {"v": ID}, True):
        res = put(client, cid, youtube_link=value)
        assert res.status_code == 400 and res.get_json()["error"] == BAD_LINK


def test_link_on_physical_copy_is_400(client, dark_knight):
    disc = copy_ids(client, dark_knight)["physical"]
    res = put(client, disc, youtube_link=ID)
    assert res.status_code == 400
    assert "digital" in res.get_json()["error"]
    res = client.post("/admin/api/copies", headers=bearer(), json={
        "title_id": dark_knight, "kind": "physical", "format": "Blu-ray", "youtube_link": ID})
    assert res.status_code == 400
    assert put(client, disc, youtube_link="").status_code == 200     # an empty link on a disc is just "none"
    assert put(client, disc, youtube_link=None).status_code == 200


def test_digital_with_id_turned_physical_is_400(client, dark_knight):
    cid = copy_ids(client, dark_knight)["digital"]
    put(client, cid, youtube_link=ID)
    res = put(client, cid, kind="physical", format="Blu-ray", service=None)
    assert res.status_code == 400
    assert "YouTube" in res.get_json()["error"]
    row = db().execute("SELECT kind, youtube_id FROM copies WHERE id=?", (cid,)).fetchone()
    assert (row["kind"], row["youtube_id"]) == ("digital", ID)
    # clearing the link in the same edit lets it become a disc
    assert put(client, cid, kind="physical", format="Blu-ray", service=None, youtube_link="").status_code == 200


def test_same_id_on_second_copy_is_409(client, dark_knight):
    cid = copy_ids(client, dark_knight)["digital"]
    assert put(client, cid, youtube_link=ID).status_code == 200
    inception = seed_title(db(), 27205, copies=(), digital=("Digital HD",))
    other = db().execute("SELECT id FROM copies WHERE title_id=?", (inception,)).fetchone()[0]
    db().execute("UPDATE copies SET service='Apple TV' WHERE id=?", (other,))
    db().commit()
    res = put(client, other, youtube_link=f"https://youtu.be/{ID}")
    assert res.status_code == 409
    assert res.get_json()["error"] == "That YouTube link is already on The Dark Knight (2008)."
    assert db().execute("SELECT youtube_id FROM copies WHERE id=?", (other,)).fetchone()[0] is None
    res = client.post("/admin/api/copies", headers=bearer(), json={
        "title_id": inception, "kind": "digital", "format": "Digital 4K", "service": "Apple TV", "youtube_link": ID})
    assert res.status_code == 409
    assert db().execute("SELECT count(*) FROM copies WHERE title_id=?", (inception,)).fetchone()[0] == 1
    # the same id on the same copy again is fine, and so is a second digital copy of the same film... without it
    assert put(client, cid, youtube_link=ID).status_code == 200


def test_post_with_a_taken_id_creates_no_title(client, dark_knight):
    put(client, copy_ids(client, dark_knight)["digital"], youtube_link=ID)
    before = db().execute("SELECT count(*) FROM titles").fetchone()[0]
    res = client.post("/admin/api/copies", headers=bearer(), json={
        "tmdb_id": 27205, "kind": "digital", "format": "Digital HD", "service": "Apple TV", "youtube_link": ID})
    assert res.status_code == 409
    assert db().execute("SELECT count(*) FROM titles").fetchone()[0] == before


def test_public_api_never_carries_it(client, dark_knight):
    put(client, copy_ids(client, dark_knight)["digital"], youtube_link=ID)
    for path in ("/api/titles", f"/api/titles/{dark_knight}", "/api/copies", "/api/titles?q=dark"):
        res = client.get(path)                                       # signed out, PUBLIC_VIEW on
        assert res.status_code == 200, path
        assert "youtube" not in json.dumps(res.get_json()).lower() and ID not in res.get_data(as_text=True), path


def test_owned_answer_carries_it(client, dark_knight):
    put(client, copy_ids(client, dark_knight)["digital"], youtube_link=ID)
    res = client.get("/admin/api/tmdb/155/owned", headers=bearer())
    assert res.status_code == 200
    copies = {c["kind"]: c for c in res.get_json()["copies"]}
    assert copies["digital"]["youtube_id"] == ID
    assert copies["physical"]["youtube_id"] is None


def test_read_key_reads_it(client, dark_knight):
    put(client, copy_ids(client, dark_knight)["digital"], youtube_link=ID)
    res = client.get(f"/admin/api/titles/{dark_knight}", headers=bearer(READ_KEY))
    assert res.status_code == 200
    assert digital(res.get_json())["youtube_id"] == ID
    assert client.put(f"/admin/api/copies/{copy_ids(client, dark_knight)['digital']}", json={"youtube_link": ""},
                      headers=bearer(READ_KEY)).status_code in (401, 403)


def test_signed_out_cannot_write_it(client, dark_knight):
    cid = copy_ids(client, dark_knight)["digital"]
    assert client.put(f"/admin/api/copies/{cid}", json={"youtube_link": ID}).status_code in (401, 403)
    assert db().execute("SELECT youtube_id FROM copies WHERE id=?", (cid,)).fetchone()[0] is None


def test_app_re_exports_the_rule():
    assert matinee.youtube_id_ok(ID) and not matinee.youtube_id_ok("short")
