"""The movie wish list (schema v11): a want per title, Want to buy, clearing on purchase, the Wish Lists app link.

Scenarios (docs/superpowers/specs/2026-11-12-wishlist-design.md section 5):
  Spirited Away (TMDB 129) is not in Matinee: Want to buy with 4K UHD creates it with no copies.
  The Dark Knight (155) is owned only as Digital HD on Movies Anywhere and wanted on 4K UHD.
  Paddington 2 (346648) is wanted in Any format and a DVD ends the want.
"""
import json
import re
import sqlite3
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

import app as matinee
from app import app, db, init_db
from conftest import FIXTURES_TMDB, seed_title
from test_copy_details import old_database, point_app_at, rows_of

API_KEY = "test-write-key-0123456789"
WANT_COLUMNS = ["title_id", "wanted_format", "note", "created_at"]
WISH_COLUMNS = ["id", "title_id", "person_id", "person_name", "item_id", "wanted_format", "created_at"]


def bearer():
    return {"Authorization": f"Bearer {API_KEY}"}


class FakeTmdb:
    """tmdb_get answered from tests/fixtures/tmdb; a missing movie file is TMDB's 404."""

    def __init__(self):
        self.calls = []

    def __call__(self, path, **params):
        self.calls.append(path)
        m = re.fullmatch(r"/movie/(\d+)", path)
        f = FIXTURES_TMDB / f"movie-{m.group(1)}.json"
        if not f.exists():
            raise matinee.TmdbError("not_found", "TMDB has no such movie.")
        return json.loads(f.read_text())


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.LAN_EDIT", False)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.MATINEE_API_KEY", API_KEY)
    monkeypatch.setattr("app.TMDB_READ_TOKEN", "test-token")
    monkeypatch.setattr("app.tmdb_get", FakeTmdb())
    monkeypatch.setattr("app.http_bytes", lambda url, timeout=15: b"\xff\xd8jpeg " + url.encode())
    monkeypatch.setattr("app.WISHLIST_URL", "http://127.0.0.1:1")   # nothing listens: the app is "down"
    init_db()
    with app.test_client() as c:
        yield c


@pytest.fixture
def dark_knight(client):
    """Owned only as Digital HD on Movies Anywhere."""
    con = db()
    tid = seed_title(con, 155, copies=(), digital=("Digital HD",))
    con.execute("UPDATE copies SET service='Movies Anywhere' WHERE title_id=?", (tid,))
    con.commit()
    return tid


@pytest.fixture
def paddington(client):
    return seed_title(db(), 346648, copies=())


def want(client, **body):
    return client.post("/admin/api/wants", headers=bearer(), json=body)


def editor_title(client, tid):
    return client.get(f"/admin/api/titles/{tid}", headers=bearer()).get_json()


def add_copy(client, tid, kind="physical", fmt="Blu-ray", service=None):
    return client.post("/admin/api/copies", headers=bearer(),
                       json={"title_id": tid, "kind": kind, "format": fmt, "service": service})


# ============================================================================== schema v11
def test_schema_version_is_11():
    assert matinee.SCHEMA_VERSION == "11"


def test_fresh_database_has_the_tables(client):
    con = db()
    assert [r[1] for r in con.execute("PRAGMA table_info(wants)")] == WANT_COLUMNS
    assert [r[1] for r in con.execute("PRAGMA table_info(wish_items)")] == WISH_COLUMNS
    assert con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0] == "11"


def test_wants_table_refuses_nonsense_whatever_wrote_it(client, dark_knight, paddington):
    con = db()
    with pytest.raises(sqlite3.IntegrityError):    # a note over 140 characters
        con.execute("INSERT INTO wants (title_id, note) VALUES (?, ?)", (dark_knight, "x" * 141))
    con.rollback()
    con.execute("INSERT INTO wants (title_id, note) VALUES (?, ?)", (dark_knight, "x" * 140))
    with pytest.raises(sqlite3.IntegrityError):    # at most one want per title
        con.execute("INSERT INTO wants (title_id) VALUES (?)", (dark_knight,))
    con.rollback()
    with pytest.raises(sqlite3.IntegrityError):    # a want needs a title
        con.execute("INSERT INTO wants (title_id) VALUES (9999)")
    con.rollback()


def test_deleting_a_title_deletes_its_want_and_history(client, dark_knight):
    con = db()
    con.execute("INSERT INTO wants (title_id, wanted_format) VALUES (?, '4K UHD')", (dark_knight,))
    con.execute("INSERT INTO wish_items (title_id, person_id, person_name, item_id) VALUES (?, 3, 'Lauren', 55)",
                (dark_knight,))
    con.commit()
    assert client.delete(f"/admin/api/titles/{dark_knight}", headers=bearer()).status_code == 200
    assert con.execute("SELECT count(*) FROM wants").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM wish_items").fetchone()[0] == 0


@pytest.mark.parametrize("version", range(1, 11))
def test_every_earlier_version_migrates_and_keeps_everything(tmp_path, monkeypatch, version):
    path = tmp_path / f"v{version}.db"
    con = old_database(path, version) if version < 10 else _v10_database(path)
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'meta'")]
    before = {t: rows_of(con, t) for t in tables}
    con.close()

    point_app_at(monkeypatch, tmp_path, path)
    matinee.init_db()
    matinee.init_db()       # a restart on the migrated file
    con = matinee.connect()
    matinee.migrate(con)
    matinee.migrate(con)
    assert con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0] == "11"
    for table, (cols, rows) in before.items():
        assert rows_of(con, table, cols)[1] == rows, table
    assert [r[1] for r in con.execute("PRAGMA table_info(wants)")] == WANT_COLUMNS
    assert [r[1] for r in con.execute("PRAGMA table_info(wish_items)")] == WISH_COLUMNS
    assert con.execute("SELECT count(*) FROM wants").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM copies").fetchone()[0] == 2     # the disc and the digital copy
    if version >= 2:
        assert con.execute("SELECT count(*) FROM watches").fetchone()[0] >= 1
    if version >= 9:
        assert con.execute("SELECT count(*) FROM watch_people").fetchone()[0] == 2
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    con.close()


def _v10_database(path):
    """A database as v10 left it: v9 plus the four copy columns."""
    con = old_database(path, 9)
    for ddl in ("edition TEXT CHECK (edition IS NULL OR length(edition) <= 60)",
                "code_status TEXT NOT NULL DEFAULT 'none' CHECK (code_status IN ('none', 'unredeemed', 'redeemed', 'expired'))",
                "purchased_on TEXT", "price_cents INTEGER CHECK (price_cents IS NULL OR price_cents >= 0)"):
        con.execute(f"ALTER TABLE copies ADD COLUMN {ddl}")
    con.execute("UPDATE meta SET v='10' WHERE k='schema'")
    con.commit()
    return con


# ============================================================================== Want to buy
def test_want_to_buy_creates_the_title_with_no_copies(client):
    res = want(client, tmdb_id=129, format="4K UHD", note="the Studio Ghibli box")
    assert res.status_code == 201, res.get_json()
    body = res.get_json()
    assert (body["title"], body["year"], body["owned"], body["copies"]) == ("Spirited Away", 2001, False, [])
    assert body["want"]["wanted_format"] == "4K UHD" and body["want"]["note"] == "the Studio Ghibli box"
    assert body["wishes"] == []
    # kept but not owned: absent from the public grid and from the editor grid
    assert client.get("/api/titles").get_json()["titles"] == []
    assert client.get("/admin/api/titles", headers=bearer()).get_json()["titles"] == []
    assert client.get("/healthz").get_json()["titles"] == 1


def test_want_to_buy_by_title_id_makes_no_tmdb_call(client, dark_knight):
    res = want(client, title_id=dark_knight, format="4K UHD")
    assert res.status_code == 201
    assert matinee.tmdb_get.calls == []


def test_a_title_already_in_matinee_is_found_by_tmdb_id(client, dark_knight):
    res = want(client, tmdb_id=155, format="4K UHD")
    assert res.status_code == 201 and res.get_json()["id"] == dark_knight
    assert matinee.tmdb_get.calls == []


def test_the_dark_knight_wanted_on_4k_is_editor_only(client, dark_knight):
    want(client, title_id=dark_knight, format="4K UHD")
    d = editor_title(client, dark_knight)
    assert d["want"]["wanted_format"] == "4K UHD" and d["owned"] is True
    public = client.get(f"/api/titles/{dark_knight}")
    assert public.status_code == 200
    text = public.get_data(as_text=True)
    for key in ("want", "wanted_format", "wishes"):
        assert f'"{key}"' not in text
    assert [t for t in client.get("/api/titles").get_json()["titles"]] != []   # still in the grid, unchanged
    assert "4K UHD" not in client.get("/api/titles").get_data(as_text=True)


def test_a_title_without_a_want_has_want_null(client, dark_knight):
    d = editor_title(client, dark_knight)
    assert d["want"] is None and d["wishes"] == []


def test_a_second_want_replaces_the_first(client, dark_knight):
    assert want(client, title_id=dark_knight, format="4K UHD", note="steelbook").status_code == 201
    res = want(client, title_id=dark_knight, format="Blu-ray")
    assert res.status_code == 200
    assert res.get_json()["want"]["wanted_format"] == "Blu-ray" and res.get_json()["want"]["note"] is None
    assert db().execute("SELECT count(*) FROM wants").fetchone()[0] == 1


def test_no_format_means_any_copy(client, paddington):
    res = want(client, title_id=paddington)
    assert res.status_code == 201 and res.get_json()["want"]["wanted_format"] is None
    res = want(client, title_id=paddington, format="")
    assert res.get_json()["want"]["wanted_format"] is None


def test_note_is_trimmed_and_blank_means_none(client, paddington):
    assert want(client, title_id=paddington, note="  for Grandma Ann  ").get_json()["want"]["note"] == "for Grandma Ann"
    assert want(client, title_id=paddington, note="   ").get_json()["want"]["note"] is None
    assert want(client, title_id=paddington, note="x" * 140).status_code == 200


def test_formats_match_the_way_copies_do(client, paddington):
    assert want(client, title_id=paddington, format="4k uhd").get_json()["want"]["wanted_format"] == "4K UHD"


@pytest.mark.parametrize("body, message", [
    ({"format": "Laserdisc"}, "Format must be one of 4K UHD, Blu-ray, 3D Blu-ray, DVD, VHS, Digital 4K, Digital HD, "
                              "Digital SD."),
    ({"format": 4}, "Format must be one of"),
    ({"note": "x" * 141}, "A note can be up to 140 characters."),
    ({"note": 5}, "A note can be up to 140 characters."),
])
def test_bad_want_values_are_400_and_change_nothing(client, paddington, body, message):
    res = want(client, title_id=paddington, **body)
    assert res.status_code == 400 and message in res.get_json()["error"]
    assert db().execute("SELECT count(*) FROM wants").fetchone()[0] == 0


def test_bad_want_never_creates_a_title(client):
    res = want(client, tmdb_id=129, format="Laserdisc")
    assert res.status_code == 400
    assert db().execute("SELECT count(*) FROM titles").fetchone()[0] == 0
    assert matinee.tmdb_get.calls == []      # validated before TMDB is asked


@pytest.mark.parametrize("body, status", [({}, 400), ({"title_id": "abc"}, 400), ({"tmdb_id": "abc"}, 400),
                                          ({"tmdb_id": -1}, 400), ({"title_id": 9999}, 404)])
def test_a_want_needs_a_known_title(client, body, status):
    assert want(client, **body).status_code == status


def test_remove_a_want(client, dark_knight):
    want(client, title_id=dark_knight, format="4K UHD")
    res = client.delete(f"/admin/api/titles/{dark_knight}/want", headers=bearer())
    assert res.status_code == 200 and res.get_json() == {"ok": True}
    assert editor_title(client, dark_knight)["want"] is None
    assert client.delete(f"/admin/api/titles/{dark_knight}/want", headers=bearer()).status_code == 200
    assert client.delete("/admin/api/titles/9999/want", headers=bearer()).status_code == 404


def test_removing_a_want_keeps_the_push_history(client, dark_knight):
    want(client, title_id=dark_knight, format="4K UHD")
    con = db()
    con.execute("INSERT INTO wish_items (title_id, person_id, person_name, item_id, wanted_format) "
                "VALUES (?, 3, 'Lauren', 55, '4K UHD')", (dark_knight,))
    con.commit()
    client.delete(f"/admin/api/titles/{dark_knight}/want", headers=bearer())
    assert editor_title(client, dark_knight)["wishes"][0]["person_name"] == "Lauren"


# ---------------------------------------------------------------- TMDB errors keep the existing shapes
def test_a_tmdb_miss_while_adding_a_want(client):
    res = want(client, tmdb_id=99999999, format="4K UHD")
    assert res.status_code == 404
    assert res.get_json() == {"error": "TMDB has no movie with id 99999999", "reason": "not_found"}
    assert db().execute("SELECT count(*) FROM titles").fetchone()[0] == 0


def test_a_tmdb_rate_limit_while_adding_a_want(client, monkeypatch):
    def busy(*a, **k):
        raise matinee.TmdbError("rate_limited", "TMDB is busy. Try again in a minute.")
    monkeypatch.setattr("app.tmdb_get", busy)
    res = want(client, tmdb_id=129, format="4K UHD")
    assert res.status_code == 503 and res.get_json()["reason"] == "rate_limited"
    assert db().execute("SELECT count(*) FROM titles").fetchone()[0] == 0
    assert db().execute("SELECT count(*) FROM wants").fetchone()[0] == 0


def test_a_tmdb_unavailable_error_is_502(client, monkeypatch):
    def down(*a, **k):
        raise matinee.TmdbError("unavailable", "TMDB didn't answer. Try again later.")
    monkeypatch.setattr("app.tmdb_get", down)
    assert want(client, tmdb_id=129).status_code == 502


# ============================================================================== the Wanted list
def test_wants_list_is_newest_first_with_owned_formats_and_pushed_to(client, dark_knight):
    want(client, tmdb_id=129, format="4K UHD")
    want(client, title_id=dark_knight, format="4K UHD", note="steelbook if they have it")
    con = db()
    con.execute("UPDATE wants SET created_at='2026-11-01 10:00:00' WHERE title_id != ?", (dark_knight,))
    con.execute("INSERT INTO wish_items (title_id, person_id, person_name, item_id) VALUES (?, 3, 'Lauren', 55)",
                (dark_knight,))
    con.execute("INSERT INTO wish_items (title_id, person_id, person_name, item_id) VALUES (?, 3, 'Lauren', 56)",
                (dark_knight,))
    con.commit()
    rows = client.get("/admin/api/wants", headers=bearer()).get_json()["wants"]
    assert [r["title"] for r in rows] == ["The Dark Knight", "Spirited Away"]
    dk, sa = rows
    assert dk["owned_formats"] == ["Digital HD"] and dk["pushed_to"] == ["Lauren"]   # once, not twice
    assert (dk["wanted_format"], dk["note"], dk["year"], dk["tmdb_id"]) == ("4K UHD", "steelbook if they have it",
                                                                          2008, 155)
    assert sa["owned_formats"] == [] and sa["pushed_to"] == []
    assert {"title_id", "poster_url", "created_at"} <= set(dk)


def test_wants_list_is_empty_to_begin_with(client):
    assert client.get("/admin/api/wants", headers=bearer()).get_json() == {"wants": []}


def test_the_read_key_may_read_the_wants_but_not_write(client, monkeypatch, dark_knight):
    monkeypatch.setattr("app.MATINEE_READ_KEY", "test-read-key-0123456789")
    h = {"Authorization": "Bearer test-read-key-0123456789"}
    assert client.get("/admin/api/wants", headers=h).status_code == 200
    assert client.post("/admin/api/wants", headers=h, json={"title_id": dark_knight}).status_code == 403


# ============================================================================== clearing
def test_a_blu_ray_leaves_a_4k_want_and_a_4k_clears_it(client, dark_knight):
    want(client, title_id=dark_knight, format="4K UHD")
    res = add_copy(client, dark_knight, fmt="Blu-ray")
    assert res.status_code == 201 and "want_cleared" not in res.get_json()
    assert editor_title(client, dark_knight)["want"]["wanted_format"] == "4K UHD"

    res = add_copy(client, dark_knight, fmt="4K UHD")
    assert res.status_code == 201 and res.get_json()["want_cleared"] is True
    assert res.get_json()["want"] is None
    assert editor_title(client, dark_knight)["want"] is None


def test_a_dvd_clears_a_want_for_any(client, paddington):
    want(client, title_id=paddington)
    res = add_copy(client, paddington, fmt="DVD")
    assert res.status_code == 201 and res.get_json()["want_cleared"] is True
    assert db().execute("SELECT count(*) FROM wants").fetchone()[0] == 0


def test_a_digital_copy_clears_a_digital_want_of_that_format(client, paddington):
    want(client, title_id=paddington, format="Digital 4K")
    assert "want_cleared" not in add_copy(client, paddington, "digital", "Digital HD", "Apple TV").get_json()
    assert add_copy(client, paddington, "digital", "Digital 4K", "Apple TV").get_json()["want_cleared"] is True


def test_clearing_keeps_the_wish_items(client, dark_knight):
    want(client, title_id=dark_knight, format="4K UHD")
    con = db()
    con.execute("INSERT INTO wish_items (title_id, person_id, person_name, item_id, wanted_format) "
                "VALUES (?, 3, 'Lauren', 55, '4K UHD')", (dark_knight,))
    con.commit()
    add_copy(client, dark_knight, fmt="4K UHD")
    assert db().execute("SELECT person_name FROM wish_items").fetchall()[0][0] == "Lauren"


def test_a_bad_copy_does_not_clear_the_want(client, dark_knight):
    want(client, title_id=dark_knight, format="4K UHD")
    assert add_copy(client, dark_knight, fmt="Laserdisc").status_code == 400
    assert editor_title(client, dark_knight)["want"] is not None


def test_adding_a_copy_by_tmdb_id_clears_too(client):
    want(client, tmdb_id=129, format="4K UHD")
    res = client.post("/admin/api/copies", headers=bearer(),
                      json={"tmdb_id": 129, "kind": "physical", "format": "4K UHD"})
    assert res.status_code == 201 and res.get_json()["want_cleared"] is True
    assert matinee.tmdb_get.calls == ["/movie/129"]      # the title was already there: one TMDB call in all


def test_editing_a_copy_does_not_clear_a_want(client, dark_knight):
    want(client, title_id=dark_knight, format="4K UHD")
    cid = db().execute("SELECT id FROM copies WHERE title_id=?", (dark_knight,)).fetchone()[0]
    res = client.put(f"/admin/api/copies/{cid}", headers=bearer(),
                     json={"kind": "physical", "format": "4K UHD", "service": None})
    assert res.status_code == 200
    assert editor_title(client, dark_knight)["want"] is not None


# ============================================================================== editor-only routes
ROUTES = [("GET", "/admin/api/wants"), ("POST", "/admin/api/wants"), ("DELETE", "/admin/api/titles/1/want"),
          ("GET", "/admin/api/wishlist/people"), ("POST", "/admin/api/titles/1/wishlist")]


@pytest.mark.parametrize("method, path", ROUTES)
def test_anonymous_requests_are_401(client, dark_knight, method, path):
    res = client.open(path, method=method, json={})
    assert res.status_code == 401, (method, path)


def test_every_want_and_wish_route_is_covered_above():
    rules = {(m, r.rule.replace("<int:title_id>", "1")) for r in app.url_map.iter_rules()
             for m in r.methods - {"HEAD", "OPTIONS"} if "want" in r.rule or "wishlist" in r.rule}
    assert rules == set(ROUTES)


# ============================================================================== the Wish Lists app
class StubWishlist:
    """A stand-in for the family Wish Lists app on a loopback port: GET /api/people, POST /api/people/<id>/items."""

    def __init__(self, people=None, fail_with=None):
        self.people = people if people is not None else [
            {"id": 1, "name": "Nick", "color": "#2e7d32"}, {"id": 3, "name": "Lauren", "color": "#1565c0"}]
        self.posts, self.headers, self.fail_with = [], [], fail_with
        stub = self

        class Handler(BaseHTTPRequestHandler):
            def _send(self, code, body):
                raw = json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(raw)))
                self.end_headers()
                self.wfile.write(raw)

            def do_GET(self):
                stub.headers.append(dict(self.headers))
                if self.path == "/api/people":
                    return self._send(200, stub.people)
                self._send(404, {"error": "no such route"})

            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
                stub.headers.append(dict(self.headers))
                m = re.fullmatch(r"/api/people/(\d+)/items", self.path)
                if stub.fail_with:
                    return self._send(*stub.fail_with)
                if not m:
                    return self._send(404, {"error": "no such route"})
                stub.posts.append((int(m.group(1)), body))
                self._send(201, {"id": 55, "name": body["name"]})

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self):
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def stub(monkeypatch):
    s = StubWishlist()
    monkeypatch.setattr("app.WISHLIST_URL", s.url)
    yield s
    s.close()


@pytest.fixture
def spirited_away(client):
    return want(client, tmdb_id=129, format="4K UHD").get_json()["id"]


def test_the_wish_lists_people_come_from_the_other_app(client, stub):
    res = client.get("/admin/api/wishlist/people", headers=bearer())
    assert res.get_json() == {"people": [{"id": 1, "name": "Nick", "color": "#2e7d32"},
                                         {"id": 3, "name": "Lauren", "color": "#1565c0"}]}
    assert stub.headers[0].get("X-Wishlist") == "1"


def test_push_posts_one_item_with_the_search_url_and_stores_a_row(client, stub, spirited_away):
    res = client.post(f"/admin/api/titles/{spirited_away}/wishlist", headers=bearer(), json={"person_id": 3})
    assert res.status_code == 200
    assert res.get_json() == {"ok": True, "person": "Lauren", "item_id": 55}
    [(pid, item)] = stub.posts
    assert pid == 3
    assert item == {"name": "Spirited Away (2001)", "kind": "online", "priority": 2,
                    "url": "https://www.amazon.com/s?k=Spirited+Away+2001+4K+UHD",
                    "notes": "Movie · 4K UHD · TMDB 129"}
    row = db().execute("SELECT title_id, person_id, person_name, item_id, wanted_format FROM wish_items").fetchone()
    assert tuple(row) == (spirited_away, 3, "Lauren", 55, "4K UHD")
    d = editor_title(client, spirited_away)
    assert [w["person_name"] for w in d["wishes"]] == ["Lauren"]


def test_the_editors_note_and_the_want_note_go_on_the_item(client, stub):
    tid = want(client, tmdb_id=129, format="4K UHD", note="the Ghibli box").get_json()["id"]
    client.post(f"/admin/api/titles/{tid}/wishlist", headers=bearer(), json={"person_id": 3, "notes": "birthday"})
    assert stub.posts[0][1]["notes"] == "Movie · 4K UHD · TMDB 129 · the Ghibli box · birthday"


def test_a_want_for_any_format_searches_by_title_and_year(client, stub, paddington):
    want(client, title_id=paddington)
    client.post(f"/admin/api/titles/{paddington}/wishlist", headers=bearer(), json={"person_id": 1})
    item = stub.posts[0][1]
    assert item["url"] == "https://www.amazon.com/s?k=Paddington+2+2017"
    assert item["notes"] == "Movie · Any format · TMDB 346648"


def test_the_shopping_template_is_a_setting(client, stub, spirited_away, monkeypatch):
    monkeypatch.setattr("app.WISHLIST_MOVIE_URL", "https://www.example.com/find?q={q}&sort=price")
    client.post(f"/admin/api/titles/{spirited_away}/wishlist", headers=bearer(), json={"person_id": 3})
    assert stub.posts[0][1]["url"] == "https://www.example.com/find?q=Spirited+Away+2001+4K+UHD&sort=price"


def test_an_unknown_person_is_400_and_posts_nothing(client, stub, spirited_away):
    for body in ({"person_id": 999}, {}, {"person_id": "abc"}):
        res = client.post(f"/admin/api/titles/{spirited_away}/wishlist", headers=bearer(), json=body)
        assert res.status_code == 400 and res.get_json() == {"error": "Pick whose wish list"}
    assert stub.posts == []
    assert db().execute("SELECT count(*) FROM wish_items").fetchone()[0] == 0


def test_the_wish_lists_app_being_down_is_502_with_its_message(client, spirited_away):
    res = client.post(f"/admin/api/titles/{spirited_away}/wishlist", headers=bearer(), json={"person_id": 3})
    assert res.status_code == 502
    assert res.get_json()["error"].startswith("wish list isn't reachable at http://127.0.0.1:1")
    assert db().execute("SELECT count(*) FROM wish_items").fetchone()[0] == 0
    assert client.get("/admin/api/wishlist/people", headers=bearer()).status_code == 502


def test_the_wish_lists_app_refusing_the_item_is_502_and_stores_nothing(client, stub, spirited_away):
    stub.fail_with = (400, {"error": "Item name is required"})
    res = client.post(f"/admin/api/titles/{spirited_away}/wishlist", headers=bearer(), json={"person_id": 3})
    assert res.status_code == 502 and res.get_json()["error"] == "Item name is required"
    assert db().execute("SELECT count(*) FROM wish_items").fetchone()[0] == 0


def test_pushing_a_title_with_no_want_is_refused(client, stub, dark_knight):
    res = client.post(f"/admin/api/titles/{dark_knight}/wishlist", headers=bearer(), json={"person_id": 3})
    assert res.status_code == 400 and res.get_json() == {"error": "Add a want first"}
    assert stub.posts == []
    assert client.post("/admin/api/titles/9999/wishlist", headers=bearer(), json={"person_id": 3}).status_code == 404


def test_pushing_twice_records_both_and_lists_the_person_once(client, stub, spirited_away):
    for _ in range(2):
        client.post(f"/admin/api/titles/{spirited_away}/wishlist", headers=bearer(), json={"person_id": 3})
    assert len(stub.posts) == 2
    assert db().execute("SELECT count(*) FROM wish_items").fetchone()[0] == 2
    assert client.get("/admin/api/wants", headers=bearer()).get_json()["wants"][0]["pushed_to"] == ["Lauren"]


# ---------------------------------------------------------------- availability
def test_wishlist_available_follows_what_answers(stub, monkeypatch):
    from wishlist import wishlist_available
    assert wishlist_available() is True
    monkeypatch.setattr("app.WISHLIST_URL", "http://127.0.0.1:1")   # port 1: nothing listens
    assert wishlist_available() is False
    monkeypatch.setattr("app.WISHLIST_URL", "")
    assert wishlist_available() is False


def test_me_says_whether_the_button_should_show(client, stub, monkeypatch):
    me = lambda: client.get("/admin/api/me", headers=bearer()).get_json()["wishlist"]   # noqa: E731
    assert me() is True
    monkeypatch.setattr("app.WISHLIST_URL", "http://127.0.0.1:1")
    assert me() is False
    assert client.get("/api/me").get_json().keys() == {"editor", "public"}


def test_the_defaults_are_localhost_and_an_amazon_search():
    import settings
    assert settings.DEFAULT_WISHLIST_URL == "http://127.0.0.1:8766"
    assert settings.DEFAULT_WISHLIST_MOVIE_URL == "https://www.amazon.com/s?k={q}"
