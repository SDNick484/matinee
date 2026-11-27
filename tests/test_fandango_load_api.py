"""POST /admin/api/fandango-links: load a list of Fandango at Home films in one editor-only request, the YouTube load
generalised (spec 2026-11-27-fandango-play-design §3a). The list is tests/fixtures/fandango/library.json (or .csv),
matched against the collection test_fandango_load.seed_fandango_library builds.
"""
import csv
import io
import json

import pytest

from app import app, db, init_db
from test_fandango_load import CSV_LIST, JSON_LIST, seed_fandango_library
from test_youtube_load import JSON_LIST as YOUTUBE_JSON

API_KEY = "test-write-key-0123456789"
READ_KEY = "test-read-key-9876543210"
URL = "/admin/api/fandango-links"
LINKED = {"15429", "104857", "20931", "38842"}


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


@pytest.fixture
def ids(client):
    return seed_fandango_library(db())


def bearer(key=API_KEY):
    return {"Authorization": f"Bearer {key}"}


def load(client, body=JSON_LIST, content_type="application/json", query="", headers=None, url=URL):
    return client.post(url + query, data=body, content_type=content_type,
                       headers=bearer() if headers is None else headers)


def stored_ids(column="fandango_id"):
    return {r["id"]: r[column] for r in db().execute(f"SELECT id, {column} FROM copies")}


def test_load_links_clean_rows(client, ids):
    before = stored_ids()
    r = load(client)
    assert r.status_code == 200
    out = r.get_json()
    assert out["linked"] == 4 and out["already_linked"] == 1 and out["dry_run"] is False
    assert len(out["not_loaded"]) == 13
    after = stored_ids()
    assert {i: v for i, v in after.items() if v != before[i]} == {
        ids["addams"]: "15429", ids["dark_knight"]: "104857", ids["princess_bride"]: "20931", ids["wall_e"]: "38842"}
    assert set(out["not_loaded"][0]) == {"title", "year", "fandango_id", "play_link", "reason", "near"}


def test_load_takes_the_csv_too(client, ids):
    r = load(client, CSV_LIST, "text/csv")
    assert r.status_code == 200 and r.get_json()["linked"] == 4
    assert set(stored_ids().values()) >= LINKED


def test_dry_run_writes_nothing(client, ids):
    before = stored_ids()
    dry = load(client, query="?dry_run=1")
    assert dry.status_code == 200 and dry.get_json()["dry_run"] is True
    assert stored_ids() == before
    real = load(client).get_json()
    assert {k: v for k, v in dry.get_json().items() if k != "dry_run"} == {k: v for k, v in real.items() if k != "dry_run"}


def test_second_load_links_nothing_new(client, ids):
    first = load(client).get_json()
    snapshot = stored_ids()
    second = load(client).get_json()
    assert second["linked"] == 0
    assert second["already_linked"] == first["linked"] + first["already_linked"]
    assert second["not_loaded"] == first["not_loaded"] and stored_ids() == snapshot


def test_a_link_pasted_by_hand_shows_as_already_linked(client, ids):
    """The healthy dry run the doc describes: a film whose link was pasted in the copy form counts as already linked."""
    cid = ids["dark_knight"]
    assert client.put(f"/admin/api/copies/{cid}", json={
        "fandango_link": "https://athome.fandango.com/content/browse/details/The-Dark-Knight/104857"},
        headers=bearer()).status_code == 200
    out = load(client, query="?dry_run=1").get_json()
    assert out["linked"] == 3 and out["already_linked"] == 2


def test_an_id_already_on_a_copy_is_never_overwritten(client, ids):
    load(client)
    swapped = json.loads(JSON_LIST)
    swapped[1]["fandango_id"] = "222222"
    swapped[1]["play_link"] = "https://athome.fandango.com/content/browse/details/The-Dark-Knight/222222"
    out = load(client, json.dumps(swapped).encode()).get_json()
    assert out["linked"] == 0
    assert any("different Fandango at Home link" in r["reason"] for r in out["not_loaded"])
    assert stored_ids()[ids["dark_knight"]] == "104857"


def test_the_two_loads_are_independent(client, ids):
    """Each load writes only its own column, and either order links the same copies."""
    yt_before = stored_ids("youtube_id")
    assert load(client).get_json()["linked"] == 4
    assert stored_ids("youtube_id") == yt_before                          # the Fandango load wrote no YouTube id
    out = load(client, YOUTUBE_JSON, url="/admin/api/youtube-links").get_json()
    assert out["linked"] == 3                                             # the Dark Knight's copy has both now
    row = db().execute("SELECT youtube_id, fandango_id FROM copies WHERE id=?", (ids["dark_knight"],)).fetchone()
    assert (row["youtube_id"], row["fandango_id"]) == ("dK8x2LmQw9A", "104857")


def test_csv_answer(client, ids):
    r = load(client, query="?format=csv")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert r.headers["Content-Disposition"] == 'attachment; filename="fandango-links-not-loaded.csv"'
    assert r.data.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(r.data.decode("utf-8-sig"), newline="")))
    assert rows[0] == ["title", "year", "fandango_link", "reason", "near"]
    assert len(rows) == 1 + 13
    by_title = {row[0]: row for row in rows[1:]}
    assert by_title["Paddington 2"][2] == "https://athome.fandango.com/content/browse/details/Paddington-2/610447"
    assert by_title["Paddington 2"][4] == "Paddington 2 (2017) [Blu-ray only]"
    assert by_title["Amelie"][2] == "https://athome.fandango.com/content/browse/details/title/82001"   # no play_link
    assert stored_ids()[ids["dark_knight"]] == "104857"                    # the CSV answer still loads


def test_csv_answer_guards_a_cell_that_could_run_as_a_formula(client, ids):
    rows = [{"title": "=HYPERLINK(\"http://x\")", "year": "1999", "fandango_id": "-1", "play_link": "-1"}]
    cells = list(csv.reader(io.StringIO(load(client, json.dumps(rows).encode(), query="?format=csv")
                                        .data.decode("utf-8-sig"), newline="")))[1]
    assert cells[0].startswith("'=") and cells[2] == "'-1"


def test_unknown_format_is_400(client, ids):
    assert load(client, query="?format=xml").status_code == 400
    assert stored_ids()[ids["dark_knight"]] is None


@pytest.mark.parametrize("body, content_type", [
    (b"", "application/json"), (b"{}", "application/json"), (b"not json", "application/json"),
    (b"[]", "application/json"), (b"", "text/csv"),
    (b"title,year,video_id,play_link\r\nMoana,2016,x,y\r\n", "text/csv"),   # a YouTube list
])
def test_bad_body_is_400(client, ids, body, content_type):
    before = stored_ids()
    r = load(client, body, content_type)
    assert r.status_code == 400 and r.get_json()["error"].endswith(".")
    assert stored_ids() == before


def test_read_key_and_signed_out_refused(client, ids):
    before = stored_ids()
    assert load(client, headers={}).status_code == 401
    assert load(client, headers=bearer(READ_KEY)).status_code == 403
    assert load(client, headers=bearer(READ_KEY), query="?dry_run=1").status_code == 403
    assert client.get(URL, headers=bearer()).status_code == 405
    assert stored_ids() == before


def test_route_is_admin_only(client):
    rules = [r for r in app.url_map.iter_rules() if "fandango-links" in r.rule]
    assert [r.rule for r in rules] == [URL]
    assert client.post("/api/fandango-links", data=JSON_LIST, content_type="application/json",
                       headers=bearer()).status_code == 404


def test_one_transaction(client, ids):
    con = db()
    con.execute("CREATE TRIGGER refuse_second BEFORE UPDATE OF fandango_id ON copies "
                "WHEN NEW.fandango_id = '38842' BEGIN SELECT RAISE(ABORT, 'forced'); END")
    con.commit()
    before = stored_ids()
    r = load(client)
    assert r.status_code == 409 and "Nothing was loaded" in r.get_json()["error"]
    assert stored_ids() == before and not db().in_transaction
    con.execute("DROP TRIGGER refuse_second")
    con.commit()
    assert load(client).get_json()["linked"] == 4


def test_a_huge_body_is_refused_before_it_is_read(client, ids):
    r = load(client, b"[" + b" " * 2_100_000 + b"]")
    assert r.status_code == 413 and stored_ids()[ids["dark_knight"]] is None
