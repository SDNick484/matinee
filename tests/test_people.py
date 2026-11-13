"""People (schema v9) and the editor-only people API.

Scenario: the family is Nick and Lauren (adults) and Midge, Alice and Paige (kids). Names live in the database,
never in the repository, so every test creates its own people.
"""
import sqlite3

import pytest

import app as matinee
from app import app, db, init_db
from conftest import seed_people, seed_title

READ_KEY = "test-read-key-9876543210"
NAME_MSG = "A name is required (up to 40 characters)."
RESERVED_MSG = "That name can't be used. Pick a name without commas that isn't everyone, adults or kids."


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("app.DB_PATH", tmp_path / "test.db")
    monkeypatch.setattr("app.DATA_DIR", tmp_path)
    monkeypatch.setattr("app.IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr("app._standalone_db", None)
    monkeypatch.setattr("app.LAN_EDIT", True)
    monkeypatch.setattr("app.PUBLIC_VIEW", True)
    monkeypatch.setattr("app.MATINEE_READ_KEY", READ_KEY, raising=False)
    init_db()
    with app.test_client() as c:
        yield c


@pytest.fixture
def anon(client, monkeypatch):
    monkeypatch.setattr("app.LAN_EDIT", False)
    return client


def make(client, name, kind="kid", color="red", **extra):
    return client.post("/admin/api/people", json={"name": name, "kind": kind, "color": color, **extra})


# ------------------------------------------------------------------ schema


def test_schema_v9_is_additive_and_idempotent(client):
    con = db()
    seed_title(con, 155, watched=("2026-11-07", None), rating=9.5)
    tables = ("watches", "user_ratings")
    before = [con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables]
    matinee.migrate(con)
    matinee.migrate(con)
    assert [con.execute(f"SELECT count(*) FROM {t}").fetchone()[0] for t in tables] == before
    assert con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0] == matinee.SCHEMA_VERSION
    assert con.execute("SELECT count(*) FROM people").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM watch_people").fetchone()[0] == 0


def test_constants():
    assert matinee.PERSON_KINDS == ("adult", "kid")
    assert matinee.PERSON_COLORS == ("green", "blue", "red", "pink", "purple")
    assert matinee.MAX_ACTIVE_PEOPLE == 8
    assert matinee.SCHEMA_VERSION == "11"


def test_watch_people_cascades_from_watch_and_restricts_person(client):
    con = db()
    ids = seed_people(con)
    tid = seed_title(con, 155, watched=("2026-11-07",))
    wid = con.execute("SELECT id FROM watches WHERE title_id=?", (tid,)).fetchone()[0]
    con.execute("INSERT INTO watch_people (watch_id, person_id) VALUES (?, ?)", (wid, ids["Midge"]))
    con.commit()
    with pytest.raises(sqlite3.IntegrityError):
        con.execute("DELETE FROM people WHERE id=?", (ids["Midge"],))
    con.rollback()
    con.execute("DELETE FROM titles WHERE id=?", (tid,))
    con.commit()
    assert con.execute("SELECT count(*) FROM watch_people").fetchone()[0] == 0


# --- every earlier schema version migrates to v9 --------------------------------------------------------------
# Literal DDL of each historical SCHEMA (plus what that version's migrate() created), from git history:
# v1 bcac5d9/d87423d, v2 e6c6f55, v3 7665f53, v4 eac6ed8, v5 d922104, v6 1689ce5, v7 4701d06, v8 e9e9f12.

META = "CREATE TABLE meta (k TEXT PRIMARY KEY, v TEXT);"


def _titles(extra=""):
    return f"""CREATE TABLE titles (
  id INTEGER PRIMARY KEY,
  tmdb_id INTEGER NOT NULL UNIQUE,
  imdb_id TEXT,
  title TEXT NOT NULL,
  sort_title TEXT NOT NULL,
  search_text TEXT NOT NULL,
  year INTEGER, release_date TEXT, runtime INTEGER, overview TEXT,
  certification TEXT,
{extra}  collection_tmdb_id INTEGER, collection_name TEXT,
  tmdb_vote REAL, tmdb_votes INTEGER,
  poster_path TEXT, backdrop_path TEXT,
  poster_file TEXT, backdrop_file TEXT,
  fetched_at TEXT NOT NULL,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX titles_collection ON titles(collection_tmdb_id);
CREATE TABLE genres (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
CREATE TABLE title_genres (
  title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  genre_id INTEGER NOT NULL REFERENCES genres(id),
  PRIMARY KEY (title_id, genre_id)
);"""


LOCATIONS = """CREATE TABLE locations (
  id INTEGER PRIMARY KEY,
  parent_id INTEGER REFERENCES locations(id) ON DELETE CASCADE,
  name TEXT NOT NULL,
  sort_order INTEGER DEFAULT 0
);
CREATE INDEX locations_parent ON locations(parent_id);"""


def _copies(location):
    loc = "  location_id INTEGER REFERENCES locations(id) ON DELETE SET NULL,\n" if location else ""
    return f"""CREATE TABLE copies (
  id INTEGER PRIMARY KEY,
  title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
  kind TEXT NOT NULL CHECK (kind IN ('physical','digital')),
  format TEXT NOT NULL,
  service TEXT,
{loc}  added_by TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX copies_title ON copies(title_id);"""


UPC_CACHE = """CREATE TABLE upc_cache (
  upc TEXT PRIMARY KEY,
  raw_title TEXT,
  brand TEXT,
  data TEXT,
  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX upc_cache_created ON upc_cache(created_at);"""

USER_RATINGS = """CREATE TABLE user_ratings (
  id INTEGER PRIMARY KEY,
  title_id INTEGER NOT NULL UNIQUE REFERENCES titles(id) ON DELETE CASCADE,
  rating REAL NOT NULL CHECK (rating >= 0.0 AND rating <= 10.0),
  created_at TEXT DEFAULT CURRENT_TIMESTAMP,
  updated_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX user_ratings_title ON user_ratings(title_id);
CREATE INDEX user_ratings_score ON user_ratings(rating);"""


def _watches(date_cols):
    return f"""CREATE TABLE watches (
  id INTEGER PRIMARY KEY,
  title_id INTEGER NOT NULL REFERENCES titles(id) ON DELETE CASCADE,
{date_cols}  created_at TEXT DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX watches_title_date ON watches(title_id, watched_date DESC);
CREATE INDEX watches_date ON watches(watched_date DESC);"""


OMDB = """CREATE TABLE omdb_ratings (
  title_id INTEGER PRIMARY KEY REFERENCES titles(id) ON DELETE CASCADE,
  imdb_id TEXT NOT NULL,
  imdb_rating REAL,
  imdb_votes INTEGER,
  rotten_tomatoes_score INTEGER,
  metacritic_score INTEGER,
  fetched_at TEXT NOT NULL
);
CREATE INDEX omdb_ratings_fetched ON omdb_ratings(fetched_at);"""

ASK_LOG = """CREATE TABLE ask_log (
  id INTEGER PRIMARY KEY,
  ts REAL NOT NULL,
  editor TEXT,
  rounds INTEGER NOT NULL,
  input_tokens INTEGER NOT NULL DEFAULT 0,
  output_tokens INTEGER NOT NULL DEFAULT 0,
  status TEXT NOT NULL
);
CREATE INDEX ask_log_ts ON ask_log(ts);"""

W_V2 = "  watched_date TEXT NOT NULL,\n"                                   # v2: a watch always has a date
W_V3 = "  watched_date TEXT,\n  notes TEXT,\n"                             # v3: dateless watches, notes
W_V4 = "  watched_date TEXT,\n  notes TEXT,\n  date_bucket TEXT,\n"        # v4: date buckets

HISTORICAL = {
    1: "\n".join([META, _titles(), _copies(False), UPC_CACHE]),
    2: "\n".join([META, _titles(), _copies(False), UPC_CACHE, USER_RATINGS, _watches(W_V2)]),
    3: "\n".join([META, _titles(), _copies(False), UPC_CACHE, USER_RATINGS, _watches(W_V3)]),
    4: "\n".join([META, _titles(), LOCATIONS, _copies(True), UPC_CACHE, USER_RATINGS, _watches(W_V4)]),
    5: "\n".join([META, _titles(), LOCATIONS, _copies(True), UPC_CACHE, USER_RATINGS, _watches(W_V4), OMDB]),
    6: "\n".join([META, _titles("  custom_poster_file TEXT,\n"), LOCATIONS, _copies(True), UPC_CACHE,
                 USER_RATINGS, _watches(W_V4), OMDB]),
    7: "\n".join([META, _titles("  custom_poster_file TEXT,\n  certification_override TEXT,\n"), LOCATIONS,
                 _copies(True), UPC_CACHE, USER_RATINGS, _watches(W_V4), OMDB]),
}
HISTORICAL[8] = HISTORICAL[7] + "\n" + ASK_LOG


def _rows(con, table):
    """Every row of `table` as (column names, rows), ordered by the first column (or rowid when it has none)."""
    cols = [r[1] for r in con.execute(f"PRAGMA table_info({table})")]
    order = "rowid" if cols[0] != "id" and table != "meta" else cols[0]
    return cols, [tuple(r) for r in con.execute(f"SELECT * FROM {table} ORDER BY {order}")]


@pytest.mark.parametrize("version", range(1, 9))
def test_migration_from_every_earlier_version(tmp_path, monkeypatch, version):
    path = tmp_path / f"v{version}.db"
    con = sqlite3.connect(path)
    con.executescript(HISTORICAL[version])
    con.execute("INSERT INTO meta VALUES ('schema', ?)", (str(version),))
    con.execute("""INSERT INTO titles (id, tmdb_id, imdb_id, title, sort_title, search_text, year, fetched_at)
                   VALUES (1, 155, 'tt0468569', 'The Dark Knight', 'dark knight', 'the dark knight', 2008,
                           '2026-01-01')""")
    con.execute("INSERT INTO copies (id, title_id, kind, format) VALUES (1, 1, 'physical', '4K UHD')")
    if version >= 2:
        con.execute("INSERT INTO user_ratings (title_id, rating) VALUES (1, 9.5)")
        con.execute("INSERT INTO watches (id, title_id, watched_date) VALUES (1, 1, '2026-11-07')")
    if version >= 3:
        con.execute("INSERT INTO watches (id, title_id, watched_date, notes) VALUES (2, 1, NULL, 'girls loved it')")
    if version >= 4:
        con.execute("INSERT INTO watches (id, title_id, watched_date, date_bucket) VALUES (3, 1, NULL, 'over_10_years')")
        con.execute("INSERT INTO locations (id, name) VALUES (1, 'Family Room')")
        con.execute("UPDATE copies SET location_id=1")
    if version >= 5:
        con.execute("INSERT INTO omdb_ratings (title_id, imdb_id, imdb_rating, fetched_at) "
                    "VALUES (1, 'tt0468569', 9.0, '2026-01-01')")
    if version >= 8:
        con.execute("INSERT INTO ask_log (ts, editor, rounds, status) VALUES (1.0, 'lan', 2, 'ok')")
    con.commit()
    tables = [r[0] for r in con.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name != 'meta'")]
    before = {t: _rows(con, t) for t in tables}
    con.close()

    monkeypatch.setattr(matinee, "DATA_DIR", tmp_path)
    monkeypatch.setattr(matinee, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(matinee, "DB_PATH", path)
    monkeypatch.setattr(matinee, "_standalone_db", None)
    matinee.init_db()      # the path the app takes on an old file: executescript(SCHEMA), migrate(), stamp
    matinee.init_db()      # and a restart on the already-migrated file
    con = matinee.connect()
    matinee.migrate(con)
    matinee.migrate(con)

    names = {r[0] for r in con.execute("SELECT name FROM sqlite_master WHERE type IN ('table', 'index')")}
    assert {"people", "watch_people", "watch_people_person"} <= names
    assert con.execute("SELECT count(*) FROM people").fetchone()[0] == 0
    assert con.execute("SELECT count(*) FROM watch_people").fetchone()[0] == 0
    # the app stamps the current version whatever the file said (init_db INSERT OR REPLACE)
    assert con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0] == matinee.SCHEMA_VERSION
    for table, (cols, rows) in before.items():
        # migrate() only adds columns (notes, date_bucket, location_id, ...); compare what the old file had
        sel = ", ".join(cols)
        order = "rowid" if cols[0] != "id" else "id"
        after = [tuple(r) for r in con.execute(f"SELECT {sel} FROM {table} ORDER BY {order}")]
        assert after == rows, table
    assert con.execute("PRAGMA integrity_check").fetchone()[0] == "ok"
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    con.close()


def test_migrate_twice_on_v9_keeps_people_and_attendance(tmp_path, monkeypatch):
    """A v9 database already holding people and watch_people rows survives init_db() and migrate() twice, byte for
    byte (no table rebuild, no reset of the new tables)."""
    path = tmp_path / "v9.db"
    monkeypatch.setattr(matinee, "DATA_DIR", tmp_path)
    monkeypatch.setattr(matinee, "IMAGE_DIR", tmp_path / "images")
    monkeypatch.setattr(matinee, "DB_PATH", path)
    monkeypatch.setattr(matinee, "_standalone_db", None)
    matinee.init_db()
    con = matinee.connect()
    ids = seed_people(con)
    con.execute("UPDATE people SET active = 0 WHERE id = ?", (ids["Paige"],))
    tid = seed_title(con, 155, watched=("2026-11-10", "2015-06-01", None))
    watches = [r[0] for r in con.execute("SELECT id FROM watches WHERE title_id = ? ORDER BY id", (tid,))]
    for name in ("Nick", "Lauren", "Paige"):
        con.execute("INSERT INTO watch_people (watch_id, person_id) VALUES (?, ?)", (watches[0], ids[name]))
    con.execute("INSERT INTO watch_people (watch_id, person_id) VALUES (?, ?)", (watches[2], ids["Midge"]))
    con.commit()
    def snapshot(c):   # every row, every column; watch_people has no rowid, so order by all of its columns
        return {t: [tuple(r) for r in c.execute(f"SELECT * FROM {t} ORDER BY 1, 2")]
                for t in ("people", "watch_people", "watches")}

    before = snapshot(con)
    assert len(before["people"]) == 5 and len(before["watch_people"]) == 4
    con.close()

    matinee.init_db()
    matinee.init_db()
    con = matinee.connect()
    matinee.migrate(con)
    matinee.migrate(con)
    assert snapshot(con) == before
    assert con.execute("SELECT v FROM meta WHERE k='schema'").fetchone()[0] == matinee.SCHEMA_VERSION
    assert con.execute("PRAGMA foreign_key_check").fetchall() == []
    con.close()


# ------------------------------------------------------------------ API


def test_create_list_update(client):
    r = make(client, "Midge")
    assert r.status_code == 200 and r.json["ok"] is True
    p = r.json["person"]
    assert p == {"id": p["id"], "name": "Midge", "kind": "kid", "color": "red", "sort_order": 0,
                 "active": True, "watch_count": 0}
    r = client.put(f"/admin/api/people/{p['id']}", json={"active": False})
    assert r.json["person"]["active"] is False
    r = client.put(f"/admin/api/people/{p['id']}", json={"name": "Midge", "kind": "kid", "color": "pink",
                                                         "sort_order": 4, "active": True})
    assert r.json["person"]["color"] == "pink" and r.json["person"]["sort_order"] == 4
    assert [x["name"] for x in client.get("/admin/api/people").json["people"]] == ["Midge"]


@pytest.mark.parametrize("body,msg", [
    ({"name": "", "kind": "kid", "color": "red"}, NAME_MSG),
    ({"name": "   ", "kind": "kid", "color": "red"}, NAME_MSG),
    ({"name": "x" * 41, "kind": "kid", "color": "red"}, NAME_MSG),
    ({"name": "Kids", "kind": "kid", "color": "red"}, RESERVED_MSG),
    ({"name": "everyone", "kind": "kid", "color": "red"}, RESERVED_MSG),
    ({"name": "Adults", "kind": "adult", "color": "red"}, RESERVED_MSG),
    ({"name": "A,B", "kind": "kid", "color": "red"}, RESERVED_MSG),
    ({"name": "42", "kind": "kid", "color": "red"}, RESERVED_MSG),
    ({"name": "Zoe", "kind": "teen", "color": "red"}, "kind must be adult or kid."),
    ({"name": "Zoe", "kind": "kid", "color": "teal"}, "color must be one of green, blue, red, pink, purple."),
])
def test_validation(client, body, msg):
    r = client.post("/admin/api/people", json=body)
    assert r.status_code == 400 and r.json == {"error": msg}
    assert client.get("/admin/api/people").json["people"] == []


@pytest.mark.parametrize("name", [["Midge"], {"first": "Midge"}, 42, 4.5, True])
def test_name_must_be_a_string(client, name):
    """JSON that is not a string is refused, not stored as str(value) ("['Midge']", "42", "True")."""
    r = client.post("/admin/api/people", json={"name": name, "kind": "kid", "color": "red"})
    assert r.status_code == 400 and r.json == {"error": NAME_MSG}
    assert client.get("/admin/api/people").json["people"] == []
    pid = make(client, "Alice", color="pink").json["person"]["id"]
    r = client.put(f"/admin/api/people/{pid}", json={"name": name})
    assert r.status_code == 400 and r.json == {"error": NAME_MSG}
    assert [p["name"] for p in client.get("/admin/api/people").json["people"]] == ["Alice"]


def test_update_validates_like_create(client):
    pid = make(client, "Alice", color="pink").json["person"]["id"]
    for body, msg in [({"name": "Kids"}, RESERVED_MSG), ({"name": ""}, NAME_MSG),
                      ({"kind": "teen"}, "kind must be adult or kid."),
                      ({"color": "teal"}, "color must be one of green, blue, red, pink, purple.")]:
        r = client.put(f"/admin/api/people/{pid}", json=body)
        assert r.status_code == 400 and r.json == {"error": msg}


def test_duplicate_name_case_insensitive(client):
    make(client, "Midge")
    r = make(client, "midge")
    assert r.status_code == 400 and r.json == {"error": "A person named Midge already exists."}
    pid = make(client, "Alice", color="pink").json["person"]["id"]
    r = client.put(f"/admin/api/people/{pid}", json={"name": "MIDGE"})
    assert r.status_code == 400 and r.json == {"error": "A person named Midge already exists."}
    assert client.put(f"/admin/api/people/{pid}", json={"name": "ALICE"}).status_code == 200  # own name


def test_ninth_active_person_refused(client):
    ids = [make(client, f"Guest {i}").json["person"]["id"] for i in range(1, 9)]
    r = make(client, "Guest 9")
    assert r.status_code == 400 and r.json == {"error": "At most 8 active people."}
    assert client.put(f"/admin/api/people/{ids[0]}", json={"active": False}).status_code == 200
    ninth = make(client, "Guest 9")
    assert ninth.status_code == 200 and ninth.json["person"]["active"] is True
    r = client.put(f"/admin/api/people/{ids[0]}", json={"active": True})
    assert r.status_code == 400 and r.json == {"error": "At most 8 active people."}
    assert client.get("/admin/api/people").json["people"][0]["active"] is False


def test_delete_without_attendance(client):
    pid = make(client, "Midge").json["person"]["id"]
    r = client.delete(f"/admin/api/people/{pid}")
    assert r.status_code == 200 and r.json == {"ok": True, "deleted_id": pid}
    assert client.get("/admin/api/people").json["people"] == []


def test_delete_with_attendance_refused(client):
    con = db()
    ids = seed_people(con)
    tid = seed_title(con, 155, watched=("2026-11-07",))
    wid = con.execute("SELECT id FROM watches WHERE title_id=?", (tid,)).fetchone()[0]
    con.execute("INSERT INTO watch_people (watch_id, person_id) VALUES (?, ?)", (wid, ids["Midge"]))
    con.commit()
    r = client.delete(f"/admin/api/people/{ids['Midge']}")
    assert r.status_code == 400 and r.json == {"error": "Midge is on 1 watches. Deactivate them instead."}
    people = {p["name"]: p for p in client.get("/admin/api/people").json["people"]}
    assert people["Midge"]["watch_count"] == 1 and people["Nick"]["watch_count"] == 0


def test_unknown_id_404(client):
    for r in (client.put("/admin/api/people/999", json={"active": False}), client.delete("/admin/api/people/999")):
        assert r.status_code == 404 and r.json == {"error": "No such person"}


@pytest.mark.parametrize("name", ["O'Brien", "<b>Zoe</b>"])
def test_name_round_trips_verbatim(client, name):
    assert make(client, name).status_code == 200
    assert [p["name"] for p in client.get("/admin/api/people").json["people"]] == [name]


def test_read_key_can_list_not_write(client, monkeypatch):
    monkeypatch.setattr("app.LAN_EDIT", False)
    hdr = {"Authorization": f"Bearer {READ_KEY}"}
    assert client.get("/admin/api/people", headers=hdr).status_code == 200
    r = client.post("/admin/api/people", json={"name": "Zoe", "kind": "kid", "color": "red"}, headers=hdr)
    assert r.status_code == 403 and r.json == {"error": "The read key can only read"}


def test_anonymous_401_and_no_public_route(anon):
    assert anon.get("/admin/api/people").status_code == 401
    assert anon.post("/admin/api/people", json={"name": "Zoe", "kind": "kid", "color": "red"}).status_code == 401
    assert anon.put("/admin/api/people/1", json={"active": False}).status_code == 401
    assert anon.delete("/admin/api/people/1").status_code == 401
    assert anon.get("/api/people").status_code == 404


def test_sort_order_defaults_to_next(client):
    orders = [make(client, n).json["person"]["sort_order"] for n in ("Nick", "Lauren", "Midge")]
    assert orders == [0, 1, 2]
    assert [p["name"] for p in client.get("/admin/api/people").json["people"]] == ["Nick", "Lauren", "Midge"]


def test_list_is_ordered_by_sort_order_then_id(client):
    make(client, "Midge", sort_order=5)
    make(client, "Alice", color="pink", sort_order=1)
    make(client, "Paige", color="purple", sort_order=1)
    assert [p["name"] for p in client.get("/admin/api/people").json["people"]] == ["Alice", "Paige", "Midge"]


def test_list_people_active_only(client):
    ids = seed_people(db())
    client.put(f"/admin/api/people/{ids['Paige']}", json={"active": False})
    assert [p["name"] for p in matinee.list_people(db(), active_only=True)] == ["Nick", "Lauren", "Midge", "Alice"]
    assert len(matinee.list_people(db())) == 5
