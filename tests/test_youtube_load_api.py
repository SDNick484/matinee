"""POST /admin/api/youtube-links: load a list of owned YouTube films in one editor-only request
(spec 2026-11-26-play-on-tv-design §5). The list is tests/fixtures/youtube/library.json (or .csv): 16 well-known
films with made-up ids, matched against the collection test_youtube_load.seed_library builds.
"""
import csv
import io
import json

import pytest

from app import app, db, init_db
from test_youtube_load import CSV_LIST, JSON_LIST, seed_library

API_KEY = "test-write-key-0123456789"
READ_KEY = "test-read-key-9876543210"
URL = "/admin/api/youtube-links"
LINKED = {"dK8x2LmQw9A", "pB7rT4yUi0o", "wL3eE5rTy1u"}


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
    return seed_library(db())


def bearer(key=API_KEY):
    return {"Authorization": f"Bearer {key}"}


def load(client, body=JSON_LIST, content_type="application/json", query="", headers=None):
    return client.post(URL + query, data=body, content_type=content_type,
                       headers=bearer() if headers is None else headers)


def stored_ids():
    return {r["id"]: r["youtube_id"] for r in db().execute("SELECT id, youtube_id FROM copies")}


def test_load_links_clean_rows(client, ids):
    before = stored_ids()
    r = load(client)
    assert r.status_code == 200
    out = r.get_json()
    assert out["linked"] == 3 and out["already_linked"] == 1 and out["dry_run"] is False
    assert len(out["not_loaded"]) == 12
    after = stored_ids()
    assert {i: v for i, v in after.items() if v != before[i]} == {
        ids["dark_knight"]: "dK8x2LmQw9A", ids["princess_bride"]: "pB7rT4yUi0o", ids["wall_e"]: "wL3eE5rTy1u"}
    reasons = {row["title"]: row["reason"] for row in out["not_loaded"]}
    assert "disc only" in reasons["Paddington 2"] and "different year" in reasons["Jurassic Park"]
    assert set(out["not_loaded"][0]) == {"title", "year", "video_id", "play_link", "reason", "near"}


def test_load_takes_the_csv_too(client, ids):
    r = load(client, CSV_LIST, "text/csv")
    assert r.status_code == 200 and r.get_json()["linked"] == 3
    assert set(stored_ids().values()) >= LINKED


def test_dry_run_writes_nothing(client, ids):
    before = stored_ids()
    dry = load(client, query="?dry_run=1")
    assert dry.status_code == 200 and dry.get_json()["dry_run"] is True
    assert stored_ids() == before
    real = load(client).get_json()
    assert {k: v for k, v in dry.get_json().items() if k != "dry_run"} == {k: v for k, v in real.items() if k != "dry_run"}
    for falsy in ("0", "false", ""):
        assert load(client, query=f"?dry_run={falsy}").get_json()["dry_run"] is False


def test_second_load_links_nothing_new(client, ids):
    first = load(client).get_json()
    snapshot = stored_ids()
    second = load(client).get_json()
    assert second["linked"] == 0
    assert second["already_linked"] == first["linked"] + first["already_linked"]
    assert second["not_loaded"] == first["not_loaded"]
    assert stored_ids() == snapshot


def test_an_id_already_on_a_copy_is_never_overwritten(client, ids):
    load(client)
    swapped = json.loads(JSON_LIST)
    swapped[0]["video_id"] = "zZzZzZzZzZz"
    swapped[0]["play_link"] = "https://youtu.be/zZzZzZzZzZz"
    out = load(client, json.dumps(swapped).encode()).get_json()
    assert out["linked"] == 0
    assert any("different YouTube link" in r["reason"] for r in out["not_loaded"])
    assert stored_ids()[ids["dark_knight"]] == "dK8x2LmQw9A"


def test_csv_answer(client, ids):
    r = load(client, query="?format=csv")
    assert r.status_code == 200 and r.mimetype == "text/csv"
    assert "attachment" in r.headers["Content-Disposition"]
    assert r.data.startswith(b"\xef\xbb\xbf")
    rows = list(csv.reader(io.StringIO(r.data.decode("utf-8-sig"), newline="")))
    assert rows[0] == ["title", "year", "youtube_link", "reason", "near"]
    assert len(rows) == 1 + 12
    by_title = {row[0]: row for row in rows[1:]}
    # an id that starts with "-" keeps its full address: the cell starts with "https", so nothing is quoted
    assert by_title["Paddington 2"][2] == "https://www.youtube.com/watch?v=-pAdD1nGt0n"
    assert "disc only" in by_title["Paddington 2"][3]
    assert by_title["Paddington 2"][4] == "Paddington 2 (2017) [Blu-ray only]"
    assert by_title["Jurassic Park"][4] == "Jurassic Park (1993) [Digital 4K · Movies Anywhere]"
    assert by_title["Plan 9 from Outer Space"][4] == ""
    assert by_title["The Incredibles"][2] == "https://www.youtube.com/watch?v=short"      # what was in the list, as it was
    assert stored_ids()[ids["dark_knight"]] == "dK8x2LmQw9A"                    # the CSV answer still loads


def test_csv_answer_of_a_dry_run_loads_nothing(client, ids):
    before = stored_ids()
    assert load(client, query="?format=csv&dry_run=1").status_code == 200
    assert stored_ids() == before


def test_csv_answer_guards_a_cell_that_could_run_as_a_formula(client, ids):
    rows = [{"title": "=HYPERLINK(\"http://x\")", "year": "1999", "video_id": "-bad", "play_link": "-bad"}]
    r = load(client, json.dumps(rows).encode(), query="?format=csv")
    cells = list(csv.reader(io.StringIO(r.data.decode("utf-8-sig"), newline="")))[1]
    assert cells[0].startswith("'=") and cells[2] == "'-bad"


def test_unknown_format_is_400(client, ids):
    assert load(client, query="?format=xml").status_code == 400
    assert stored_ids()[ids["dark_knight"]] is None


@pytest.mark.parametrize("body, content_type", [
    (b"", "application/json"), (b"{}", "application/json"), (b"not json", "application/json"),
    (b"[]", "application/json"), (b"[1]", "application/json"), (b"", "text/csv"),
    (b"title,year\r\nMoana,2016\r\n", "text/csv"), (b"\xff\xfe", "text/csv"), (b"not json", "text/plain"),
])
def test_bad_body_is_400(client, ids, body, content_type):
    before = stored_ids()
    r = load(client, body, content_type)
    assert r.status_code == 400
    assert r.get_json()["error"].endswith(".") and len(r.get_json()["error"]) > 15
    assert stored_ids() == before


def test_a_row_missing_a_column_comes_back_not_loaded(client, ids):
    body = json.dumps([{"title": "The Dark Knight", "year": 2008, "video_id": "dK8x2LmQw9A"}]).encode()
    out = load(client, body).get_json()
    assert out["linked"] == 0 and "no play_link" in out["not_loaded"][0]["reason"]


def test_read_key_and_signed_out_refused(client, ids):
    before = stored_ids()
    assert load(client, headers={}).status_code == 401
    assert load(client, headers=bearer("wrong-key")).status_code == 401
    assert load(client, headers=bearer(READ_KEY)).status_code == 403
    assert load(client, headers=bearer(READ_KEY), query="?dry_run=1").status_code == 403
    assert client.get(URL, headers=bearer()).status_code == 405
    assert stored_ids() == before


def test_route_is_admin_only(client):
    rules = [r for r in app.url_map.iter_rules() if "youtube-links" in r.rule]
    assert [r.rule for r in rules] == [URL]
    assert rules[0].methods >= {"POST"} and "GET" not in rules[0].methods
    assert client.post("/api/youtube-links", data=JSON_LIST, content_type="application/json",
                       headers=bearer()).status_code == 404


def test_one_transaction(client, ids):
    """A write that fails on the second copy leaves the first unwritten, and the answer says nothing was loaded."""
    con = db()
    con.execute("CREATE TRIGGER refuse_second BEFORE UPDATE OF youtube_id ON copies "
                "WHEN NEW.youtube_id = 'pB7rT4yUi0o' BEGIN SELECT RAISE(ABORT, 'forced'); END")
    con.commit()
    before = stored_ids()
    r = load(client)
    assert r.status_code == 409 and "Nothing was loaded" in r.get_json()["error"]
    assert stored_ids() == before                        # the Dark Knight, written first, is not there either
    assert not db().in_transaction
    con.execute("DROP TRIGGER refuse_second")
    con.commit()
    assert load(client).get_json()["linked"] == 3        # and the same request works once the cause is gone


def test_a_huge_body_is_refused_before_it_is_read(client, ids):
    r = load(client, b"[" + b" " * 2_100_000 + b"]")
    assert r.status_code == 413 and r.get_json()["error"]
    assert stored_ids()[ids["dark_knight"]] is None
